"""Tests for src/shaping/aggregation.py — small-frame aggregation."""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.shaping.aggregation import (
    MAGIC_AGG,
    AggregationShaper,
    _decode_aggregation,
    _encode_aggregation,
)


def _make_frame(size: int = 100) -> bytes:
    return b"F" * size


class TestAggregationEncodeDecode:
    def test_single_frame_roundtrip(self):
        frames = [_make_frame(50)]
        encoded = _encode_aggregation(frames)
        assert encoded[:4] == MAGIC_AGG
        decoded = _decode_aggregation(encoded)
        assert decoded == frames

    def test_multiple_frames_roundtrip(self):
        frames = [_make_frame(10), _make_frame(200), _make_frame(50)]
        encoded = _encode_aggregation(frames)
        decoded = _decode_aggregation(encoded)
        assert decoded == frames

    def test_empty_frames(self):
        frames = [b"", b"", b""]
        encoded = _encode_aggregation(frames)
        decoded = _decode_aggregation(encoded)
        assert decoded == frames

    def test_large_frame_count(self):
        frames = [_make_frame(i) for i in range(50)]
        encoded = _encode_aggregation(frames)
        decoded = _decode_aggregation(encoded)
        assert decoded == frames

    def test_invalid_magic_raises(self):
        with pytest.raises(ValueError, match="Invalid aggregation magic"):
            _decode_aggregation(b"XXXX" + b"\x00" * 50)

    def test_truncated_envelope_raises(self):
        with pytest.raises(ValueError, match="too short"):
            _decode_aggregation(b"VAGG")

    def test_truncated_length_table_raises(self):
        import struct
        # Declare 5 frames but only provide length for 2
        header = struct.pack(">4s H", MAGIC_AGG, 5)
        body = struct.pack(">I", 10) + struct.pack(">I", 20)
        with pytest.raises(ValueError, match="truncated"):
            _decode_aggregation(header + body)


class TestAggregationShaper:
    def test_disabled_passthrough(self):
        shaper = AggregationShaper(max_bytes=4096, enabled=False)
        frame = _make_frame(100)
        chunks = shaper.encode_frame(frame)
        assert len(chunks) == 1
        assert chunks[0].data == frame

    def test_enabled_buffers_single_frame(self):
        shaper = AggregationShaper(max_bytes=4096, enabled=True)
        frame = _make_frame(100)
        chunks = shaper.encode_frame(frame)
        assert chunks == []  # buffered, not emitted

    def test_flush_emits_aggregated(self):
        shaper = AggregationShaper(max_bytes=4096, enabled=True)
        shaper.encode_frame(_make_frame(50))
        shaper.encode_frame(_make_frame(80))
        chunks = shaper.flush()
        assert len(chunks) == 1
        assert chunks[0].data[:4] == MAGIC_AGG

    def test_aggregation_roundtrip(self):
        shaper = AggregationShaper(max_bytes=4096, enabled=True)
        frames = [_make_frame(i * 10 + 10) for i in range(10)]
        for f in frames:
            shaper.encode_frame(f)
        chunks = shaper.flush()
        assert len(chunks) == 1
        decoded = shaper.decode_chunk(chunks[0].data)
        assert decoded == frames

    def test_single_frame_after_flush_not_aggregated(self):
        shaper = AggregationShaper(max_bytes=4096, enabled=True)
        shaper.encode_frame(_make_frame(100))
        chunks = shaper.flush()
        assert len(chunks) == 1
        assert chunks[0].data == _make_frame(100)  # no envelope for single frame

    def test_auto_flush_on_max_bytes(self):
        shaper = AggregationShaper(max_bytes=200, enabled=True)
        frames = [_make_frame(100), _make_frame(80), _make_frame(30)]
        results = []
        for f in frames:
            results.extend(shaper.encode_frame(f))
        results.extend(shaper.flush())
        assert len(results) >= 1
        decoded_all: list[bytes] = []
        for ch in results:
            decoded_all.extend(shaper.decode_chunk(ch.data))
        assert decoded_all == frames

    def test_empty_flush_returns_empty(self):
        shaper = AggregationShaper(max_bytes=4096, enabled=True)
        assert shaper.flush() == []

    def test_close_returns_remaining(self):
        shaper = AggregationShaper(max_bytes=4096, enabled=True)
        shaper.encode_frame(_make_frame(50))
        chunks = shaper.close()
        assert len(chunks) == 1

    def test_disabled_decode_chunk_passthrough(self):
        shaper = AggregationShaper(max_bytes=4096, enabled=False)
        frame = _make_frame(100)
        result = shaper.decode_chunk(frame)
        assert result == [frame]

    def test_decode_non_aggregated_passthrough(self):
        shaper = AggregationShaper(max_bytes=4096, enabled=True)
        frame = _make_frame(100)
        result = shaper.decode_chunk(frame)
        assert result == [frame]
