"""Send scheduler — coordinates traffic shaper with frame-type-aware flush policy.

Design:
- DATA frames may be buffered by aggregation-enabled shapers.
- Control frames (HEARTBEAT, AUTH, CLOSE) trigger a pre-flush of buffered
  DATA before themselves, so control messages are never delayed by aggregation.
- Jitter delay_ms is metadata only — no real sleep.
- The scheduler is stateless; buffer state lives in the underlying shaper.
"""

from __future__ import annotations

from ..common.frame import FrameType
from .base import ShapedChunk, TrafficShaper


class SendScheduler:
    """Thin coordinator that applies frame-type-aware flush policy.

    Does NOT introduce its own timer or thread. All flush decisions are
    synchronous: size-threshold inside AggregationShaper, pre-control
    flush here, and explicit flush() / close().
    """

    def __init__(self, shaper: TrafficShaper):
        self._shaper = shaper

    @property
    def shaper(self) -> TrafficShaper:
        return self._shaper

    def send(self, encoded_frame: bytes, frame_type: FrameType) -> list[ShapedChunk]:
        """Produce transport-ready chunks for *encoded_frame*.

        Returns chunks to send immediately. May be empty if the frame was
        buffered (aggregation) — the caller should NOT interpret an empty
        return as an error.

        Control frames (HEARTBEAT, AUTH, CLOSE) trigger a pre-flush of
        buffered DATA and are themselves flushed immediately so they are
        never delayed by aggregation.
        """
        if frame_type != FrameType.DATA:
            # Flush buffered DATA before control/management frames
            flushed = self._shaper.flush()
            # Encode the control frame — may be buffered by aggregation
            chunks = self._shaper.encode_frame(encoded_frame)
            if not chunks:
                # Control frame was buffered — flush it immediately
                chunks = self._shaper.flush()
            return flushed + chunks

        return self._shaper.encode_frame(encoded_frame)

    def flush(self) -> list[ShapedChunk]:
        """Flush any buffered frames (e.g. aggregation buffer)."""
        return self._shaper.flush()

    def close(self) -> list[ShapedChunk]:
        """Close the shaper, emitting any remaining buffered data."""
        return self._shaper.close()
