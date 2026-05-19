"""Tests for src/shaping/padding.py — encode/decode reversibility."""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.shaping.padding import (
    MAGIC_PAD,
    PaddingShaper,
    _decode_padding,
    _encode_padding,
)
from src.shaping.base import TrafficShaper


def _make_frame(size: int = 100) -> bytes:
    return b"X" * size


class TestPaddingEncodeDecode:
    """Test the low-level encode/decode functions."""

    def test_zero_padding_roundtrip(self):
        frame = _make_frame(50)
        encoded = _encode_padding(frame, 0, 0, random.Random(42))
        assert encoded[:4] == MAGIC_PAD
        decoded = _decode_padding(encoded)
        assert decoded == [frame]

    def test_fixed_padding_roundtrip(self):
        frame = _make_frame(200)
        rng = random.Random(123)
        encoded = _encode_padding(frame, 10, 10, rng)
        assert encoded[:4] == MAGIC_PAD
        decoded = _decode_padding(encoded)
        assert decoded == [frame]

    def test_random_padding_range(self):
        frame = _make_frame(100)
        rng = random.Random(99)
        for _ in range(20):
            encoded = _encode_padding(frame, 5, 50, rng)
            decoded = _decode_padding(encoded)
            assert decoded == [frame]

    def test_padding_length_varies(self):
        frame = _make_frame(64)
        rng = random.Random(7)
        lengths = set()
        for _ in range(50):
            lengths.add(len(_encode_padding(frame, 10, 100, rng)))
        assert len(lengths) > 1

    def test_different_seeds_produce_different_padding(self):
        frame = _make_frame(64)
        a = _encode_padding(frame, 10, 100, random.Random(1))
        b = _encode_padding(frame, 10, 100, random.Random(2))
        assert a != b

    def test_encoded_larger_than_original(self):
        frame = _make_frame(50)
        encoded = _encode_padding(frame, 20, 20, random.Random(42))
        assert len(encoded) == len(frame) + 10 + 20  # header + original + padding

    def test_encode_preserves_original_bytes(self):
        frame = bytes(range(256))
        encoded = _encode_padding(frame, 5, 5, random.Random(42))
        decoded = _decode_padding(encoded)
        assert decoded == [frame]

    def test_invalid_magic_raises(self):
        with pytest.raises(ValueError, match="Invalid padding magic"):
            _decode_padding(b"XXXX" + b"\x00" * 100)

    def test_truncated_envelope_raises(self):
        with pytest.raises(ValueError, match="too short"):
            _decode_padding(b"VPAD")

    def test_length_mismatch_raises(self):
        # Construct valid header but wrong data length
        import struct
        header = struct.pack(">4s I H", MAGIC_PAD, 50, 5)
        bad_data = header + b"X" * 50  # missing padding
        with pytest.raises(ValueError, match="length mismatch"):
            _decode_padding(bad_data)


class TestPaddingShaper:
    def test_disabled_passthrough(self):
        shaper = PaddingShaper(min_padding_bytes=10, max_padding_bytes=20, enabled=False)
        frame = _make_frame(100)
        chunks = shaper.encode_frame(frame)
        assert len(chunks) == 1
        assert chunks[0].data == frame
        decoded = shaper.decode_chunk(chunks[0].data)
        assert decoded == [frame]

    def test_enabled_adds_padding(self):
        shaper = PaddingShaper(min_padding_bytes=20, max_padding_bytes=20, enabled=True)
        frame = _make_frame(100)
        chunks = shaper.encode_frame(frame)
        assert len(chunks) == 1
        assert len(chunks[0].data) > len(frame)
        assert chunks[0].data[:4] == MAGIC_PAD

    def test_enabled_roundtrip(self):
        shaper = PaddingShaper(min_padding_bytes=5, max_padding_bytes=50,
                               rng=random.Random(42), enabled=True)
        for _ in range(30):
            frame = bytes(random.getrandbits(8) for _ in range(random.randint(1, 500)))
            chunks = shaper.encode_frame(frame)
            decoded = shaper.decode_chunk(chunks[0].data)
            assert decoded == [frame]

    def test_deterministic_with_seed(self):
        frame = _make_frame(100)
        s1 = PaddingShaper(min_padding_bytes=10, max_padding_bytes=100,
                           rng=random.Random(1), enabled=True)
        s2 = PaddingShaper(min_padding_bytes=10, max_padding_bytes=100,
                           rng=random.Random(1), enabled=True)
        assert s1.encode_frame(frame) == s2.encode_frame(frame)

    def test_decode_non_padded_passthrough(self):
        shaper = PaddingShaper(min_padding_bytes=10, max_padding_bytes=20, enabled=True)
        frame = _make_frame(50)
        decoded = shaper.decode_chunk(frame)
        assert decoded == [frame]

    def test_decode_bad_magic_passthrough(self):
        shaper = PaddingShaper(min_padding_bytes=10, max_padding_bytes=20, enabled=True)
        result = shaper.decode_chunk(b"XXXX" + b"\x00" * 100)
        assert result == [b"XXXX" + b"\x00" * 100]

    def test_empty_frame(self):
        shaper = PaddingShaper(min_padding_bytes=5, max_padding_bytes=5, enabled=True)
        frame = b""
        chunks = shaper.encode_frame(frame)
        decoded = shaper.decode_chunk(chunks[0].data)
        assert decoded == [b""]

    def test_flush_empty(self):
        shaper = PaddingShaper(min_padding_bytes=10, max_padding_bytes=20, enabled=True)
        assert shaper.flush() == []

    def test_close_empty(self):
        shaper = PaddingShaper(min_padding_bytes=10, max_padding_bytes=20, enabled=True)
        assert shaper.close() == []
