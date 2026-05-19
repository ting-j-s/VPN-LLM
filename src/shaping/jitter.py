"""Jitter skeleton for timing gate mitigation.

Does NOT perform real sleep. Only annotates ShapedChunk.delay_ms with
a random delay in [jitter_min_ms, jitter_max_ms]. The actual scheduler
(Phase 5B+) will use these annotations to pace sends.

Current status: SKELETON — delay metadata only, no real sleep.
"""

from __future__ import annotations

import random as random_module

from .base import ShapedChunk, TrafficShaper


class JitterShaper(TrafficShaper):
    """Annotates chunks with random delay metadata. No real sleep."""

    def __init__(self, min_ms: float, max_ms: float,
                 rng: random_module.Random | None = None, enabled: bool = True):
        super().__init__(rng)
        if min_ms < 0:
            raise ValueError(f"jitter_min_ms must be >= 0, got {min_ms}")
        if max_ms < min_ms:
            raise ValueError(f"jitter_max_ms ({max_ms}) must be >= jitter_min_ms ({min_ms})")
        self.min_ms = min_ms
        self.max_ms = max_ms
        self.enabled = enabled

    def encode_frame(self, frame: bytes) -> list[ShapedChunk]:
        chunk = ShapedChunk(data=frame)
        if self.enabled and self.max_ms > 0:
            delay = self._rng.uniform(self.min_ms, self.max_ms)
            chunk.delay_ms = delay
        return [chunk]

    def decode_chunk(self, chunk: bytes) -> list[bytes]:
        return [chunk]

    def flush(self) -> list[ShapedChunk]:
        return []

    def close(self) -> list[ShapedChunk]:
        return []
