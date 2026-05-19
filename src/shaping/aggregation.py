"""Small-frame aggregation strategy for reducing small_packet_ratio.

Envelope format (big-endian):
  MAGIC_AGG(4) + frame_count(2) + [frame_len_i(4)]*N + frame_1 + ... + frame_N

Aggregation buffers frames and emits a single chunk on flush() or when
the accumulated size reaches aggregation_max_bytes.
"""

from __future__ import annotations

import random as random_module
import struct

from .base import ShapedChunk, TrafficShaper

MAGIC_AGG = b"VAGG"
ENVELOPE_HEADER_SIZE = 6  # MAGIC(4) + frame_count(2)


def _encode_aggregation(frames: list[bytes]) -> bytes:
    """Encode multiple frames into an aggregation envelope."""
    parts = [struct.pack(">4s H", MAGIC_AGG, len(frames))]
    for f in frames:
        parts.append(struct.pack(">I", len(f)))
    parts.extend(frames)
    return b"".join(parts)


def _decode_aggregation(data: bytes) -> list[bytes]:
    """Extract original frames from an aggregation envelope."""
    if len(data) < ENVELOPE_HEADER_SIZE:
        raise ValueError(f"Aggregation envelope too short: {len(data)} < {ENVELOPE_HEADER_SIZE}")

    magic, count = struct.unpack(">4s H", data[:ENVELOPE_HEADER_SIZE])
    if magic != MAGIC_AGG:
        raise ValueError(f"Invalid aggregation magic: {magic!r}, expected {MAGIC_AGG!r}")

    offset = ENVELOPE_HEADER_SIZE
    # Read frame lengths
    lens: list[int] = []
    for _ in range(count):
        if offset + 4 > len(data):
            raise ValueError("Aggregation envelope truncated in length table")
        fl = struct.unpack(">I", data[offset:offset + 4])[0]
        lens.append(fl)
        offset += 4

    frames: list[bytes] = []
    for fl in lens:
        if offset + fl > len(data):
            raise ValueError("Aggregation envelope truncated in frame data")
        frames.append(data[offset:offset + fl])
        offset += fl

    return frames


class AggregationShaper(TrafficShaper):
    """Buffers frames and emits aggregated chunks on flush.

    If aggregation_max_bytes > 0, auto-flush when the accumulated size
    reaches the limit. If aggregation_max_bytes == 0, all buffered frames
    are emitted on flush() regardless of total size.
    """

    def __init__(self, max_delay_ms: float = 0.0, max_bytes: int = 0,
                 rng: random_module.Random | None = None, enabled: bool = True):
        super().__init__(rng)
        self.max_delay_ms = max_delay_ms
        self.max_bytes = max_bytes
        self.enabled = enabled
        self._buffer: list[bytes] = []
        self._buffer_size = 0

    def encode_frame(self, frame: bytes) -> list[ShapedChunk]:
        if not self.enabled:
            return [ShapedChunk(data=frame)]

        self._buffer.append(frame)
        self._buffer_size += len(frame)

        if self.max_bytes > 0 and self._buffer_size >= self.max_bytes:
            return self._do_flush()
        return []

    def _do_flush(self) -> list[ShapedChunk]:
        if not self._buffer:
            return []
        if len(self._buffer) == 1:
            chunk = ShapedChunk(data=self._buffer[0])
        else:
            aggregated = _encode_aggregation(self._buffer)
            chunk = ShapedChunk(
                data=aggregated,
                delay_ms=self.max_delay_ms,
                metadata={"frame_count": len(self._buffer)},
            )
        self._buffer = []
        self._buffer_size = 0
        return [chunk]

    def decode_chunk(self, chunk: bytes) -> list[bytes]:
        if not self.enabled:
            return [chunk]
        if chunk[:4] == MAGIC_AGG:
            return _decode_aggregation(chunk)
        return [chunk]

    def flush(self) -> list[ShapedChunk]:
        if not self.enabled:
            return []
        return self._do_flush()

    def close(self) -> list[ShapedChunk]:
        return self.flush()
