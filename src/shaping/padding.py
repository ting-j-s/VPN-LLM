"""Random padding strategy for reducing repeated_length_ratio.

Envelope format (big-endian):
  MAGIC_PAD(4) + original_len(4) + padding_len(2) + original_payload(N) + random_bytes(M)

Total overhead per frame: 10 bytes + M padding bytes.
"""

from __future__ import annotations

import random as random_module
import struct

from .base import ShapedChunk, TrafficShaper

MAGIC_PAD = b"VPAD"
ENVELOPE_HEADER_SIZE = 10  # MAGIC(4) + original_len(4) + padding_len(2)


def _encode_padding(frame: bytes, min_pad: int, max_pad: int, rng: random_module.Random) -> bytes:
    """Wrap frame in a padding envelope."""
    if min_pad < 0 or max_pad < min_pad:
        raise ValueError(f"Invalid padding range: [{min_pad}, {max_pad}]")

    pad_len = rng.randint(min_pad, max_pad) if max_pad > min_pad else min_pad
    padding = bytes(rng.getrandbits(8) for _ in range(pad_len))

    header = struct.pack(">4s I H", MAGIC_PAD, len(frame), pad_len)
    return header + frame + padding


def _decode_padding(data: bytes) -> list[bytes]:
    """Extract original frame(s) from a padding envelope.

    Returns a list of original frame bytes.
    """
    if len(data) < ENVELOPE_HEADER_SIZE:
        raise ValueError(f"Padding envelope too short: {len(data)} < {ENVELOPE_HEADER_SIZE}")

    magic, orig_len, pad_len = struct.unpack(">4s I H", data[:ENVELOPE_HEADER_SIZE])
    if magic != MAGIC_PAD:
        raise ValueError(f"Invalid padding magic: {magic!r}, expected {MAGIC_PAD!r}")

    expected = ENVELOPE_HEADER_SIZE + orig_len + pad_len
    if len(data) != expected:
        raise ValueError(f"Padding envelope length mismatch: got {len(data)}, expected {expected}")

    return [data[ENVELOPE_HEADER_SIZE:ENVELOPE_HEADER_SIZE + orig_len]]


class PaddingShaper(TrafficShaper):
    """Adds random padding to frames to vary packet sizes."""

    def __init__(self, min_padding_bytes: int, max_padding_bytes: int,
                 rng: random_module.Random | None = None, enabled: bool = True):
        super().__init__(rng)
        self.min_padding_bytes = min_padding_bytes
        self.max_padding_bytes = max_padding_bytes
        self.enabled = enabled

    def encode_frame(self, frame: bytes) -> list[ShapedChunk]:
        if not self.enabled:
            return [ShapedChunk(data=frame)]
        try:
            shaped = _encode_padding(frame, self.min_padding_bytes, self.max_padding_bytes, self._rng)
            return [ShapedChunk(data=shaped)]
        except Exception:
            # If anything goes wrong, fall back to pass-through
            return [ShapedChunk(data=frame)]

    def decode_chunk(self, chunk: bytes) -> list[bytes]:
        if not self.enabled:
            return [chunk]
        if chunk[:4] == MAGIC_PAD:
            return _decode_padding(chunk)
        return [chunk]

    def flush(self) -> list[ShapedChunk]:
        return []

    def close(self) -> list[ShapedChunk]:
        return []
