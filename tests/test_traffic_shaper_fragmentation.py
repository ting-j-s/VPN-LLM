"""Tests for src/shaping/fragmentation.py — split/reassemble skeleton."""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.shaping.fragmentation import (
    MAGIC_FRAG,
    chunks_to_fragments,
    fragments_to_chunks,
    reassemble_fragments,
    split_frame,
)


class TestSplitReassemble:
    def test_single_fragment_roundtrip(self):
        frame = b"hello world"
        fragments = split_frame(frame, max_chunk_size=100, min_size=100)
        assert len(fragments) == 1
        assert fragments[0] == frame  # below min_size, no envelope

    def test_large_frame_split(self):
        frame = b"A" * 200
        fragments = split_frame(frame, max_chunk_size=50, min_size=1)
        assert len(fragments) > 1
        for frag in fragments:
            assert frag[:4] == MAGIC_FRAG

    def test_split_reassemble_roundtrip(self):
        frame = bytes(random.getrandbits(8) for _ in range(500))
        fragments = split_frame(frame, max_chunk_size=100, min_size=1)
        reassembled = reassemble_fragments(fragments)
        assert reassembled == frame

    def test_below_min_size_not_split(self):
        frame = b"small"
        fragments = split_frame(frame, max_chunk_size=100, min_size=100)
        assert fragments == [frame]

    def test_exact_chunk_size(self):
        # frame size exactly equals threshold
        frame = b"B" * 100
        fragments = split_frame(frame, max_chunk_size=100, min_size=100)
        # max_chunk_size 100 with 12-byte header = 88 data per fragment
        # 100 bytes / 88 = 2 fragments
        assert len(fragments) == 2
        assert reassemble_fragments(fragments) == frame

    def test_same_seed_orders_fragments(self):
        frame = b"C" * 300
        fragments = split_frame(frame, max_chunk_size=100, min_size=1)
        # Fragments should have correct indices
        for i, frag in enumerate(fragments):
            import struct
            _, fid, count, _ = struct.unpack(">4s H H I", frag[:12])
            assert fid == i
            assert count == len(fragments)

    def test_reassemble_order_independent(self):
        frame = b"D" * 300
        fragments = split_frame(frame, max_chunk_size=100, min_size=1)
        # Shuffle fragments — reassemble should still work
        shuffled = list(fragments)
        random.Random(42).shuffle(shuffled)
        assert reassemble_fragments(shuffled) == frame

    def test_reassemble_empty_list_raises(self):
        with pytest.raises(ValueError, match="No fragments"):
            reassemble_fragments([])

    def test_reassemble_truncated_raises(self):
        with pytest.raises(ValueError, match="too short"):
            reassemble_fragments([b"VFRG"])

    def test_invalid_max_chunk_size_raises(self):
        with pytest.raises(ValueError, match="max_chunk_size"):
            split_frame(b"test", max_chunk_size=0)

    def test_max_chunk_too_small_for_header_raises(self):
        with pytest.raises(ValueError, match="max_chunk_size"):
            split_frame(b"test", max_chunk_size=12, min_size=1)


class TestFragmentsToChunks:
    def test_converts_fragments_to_chunks(self):
        frame = b"E" * 300
        fragments = split_frame(frame, max_chunk_size=100, min_size=1)
        chunks = fragments_to_chunks(fragments)
        assert len(chunks) == len(fragments)
        for ch in chunks:
            assert ch.metadata.get("fragment_id") is not None
            assert ch.metadata.get("fragment_count") is not None
            assert ch.metadata.get("original_len") is not None

    def test_chunks_reassemble(self):
        frame = b"F" * 300
        fragments = split_frame(frame, max_chunk_size=100, min_size=1)
        chunks = fragments_to_chunks(fragments)
        reassembled = chunks_to_fragments(chunks)
        assert reassembled == frame
