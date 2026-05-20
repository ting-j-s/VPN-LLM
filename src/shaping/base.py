"""TrafficShaper abstract interface and basic types.

All shapers must be deterministic-test-friendly: they accept an optional
random.Random instance for reproducible behavior.
"""

from __future__ import annotations

import random as random_module
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ShapedChunk:
    """A shaped output chunk that may carry metadata for the scheduler.

    Attributes:
        data: The encoded chunk bytes to send over the transport.
        is_dummy: True if this chunk is a dummy (no real payload).
        delay_ms: Suggested delay before sending (0 = send immediately).
        metadata: Optional metadata for debugging / scheduling.
    """

    data: bytes
    is_dummy: bool = False
    delay_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class TrafficShaper(ABC):
    """Abstract traffic shaper.

    Lifecycle: encode_frame() zero or more times, then flush() to emit
    aggregated chunks. decode_chunk() reverses the encoding.

    All implementations must be reversible: for any sequence of frames,
    decode_chunk() on each shaped chunk emitted by encode_frame() + flush()
    must reconstruct the original frames in order.

    Attributes:
        timing_controller: Optional TimingController for RTT-aware delay/jitter.
            When set, callers should invoke timing_controller.apply(chunk)
            before sending each chunk. Default None → no timing applied.
    """

    def __init__(self, rng: random_module.Random | None = None):
        self._rng = rng or random_module.Random(42)
        self.timing_controller: object | None = None  # TimingController, set by factory

    @abstractmethod
    def encode_frame(self, frame: bytes) -> list[ShapedChunk]:
        """Shape a single outgoing frame.

        May return 0 chunks (frame buffered for aggregation), 1 chunk
        (pass-through or individually shaped), or multiple chunks
        (fragmentation).

        Args:
            frame: Encoded frame bytes from the caller.

        Returns:
            List of ShapedChunk ready for transport send.
        """
        ...

    @abstractmethod
    def decode_chunk(self, chunk: bytes) -> list[bytes]:
        """Reverse the shaping on a received chunk.

        Args:
            chunk: Shaped chunk bytes received from transport.

        Returns:
            List of original frame bytes.
        """
        ...

    @abstractmethod
    def flush(self) -> list[ShapedChunk]:
        """Emit any buffered frames as shaped chunks.

        Returns:
            List of ShapedChunk ready for transport send.
        """
        ...

    @abstractmethod
    def close(self) -> list[ShapedChunk]:
        """Final flush and cleanup. After close(), no more calls expected.

        Returns:
            Any remaining shaped chunks.
        """
        ...

    @property
    def rng(self) -> random_module.Random:
        return self._rng


class NoopTrafficShaper(TrafficShaper):
    """Default no-op shaper. One frame in → one chunk out, no modification."""

    def encode_frame(self, frame: bytes) -> list[ShapedChunk]:
        return [ShapedChunk(data=frame)]

    def decode_chunk(self, chunk: bytes) -> list[bytes]:
        return [chunk]

    def flush(self) -> list[ShapedChunk]:
        return []

    def close(self) -> list[ShapedChunk]:
        return []
