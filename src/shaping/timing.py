"""Timing policy and controller for RTT-aware countermeasures.

Provides:
- TimingPolicy: configuration for delay/jitter (default disabled)
- TimingController: runtime object that applies delay per ShapedChunk
- DummyFramePolicy: configuration for synthetic dummy frames (default disabled)

Design principles:
- Default disabled for all timing countermeasures.
- metadata_only mode sets chunk.delay_ms without sleeping (test-friendly).
- runtime_sleep mode only available when explicitly configured.
- All randomness uses injectable random.Random for reproducibility.
- No background threads — all decisions are synchronous per-chunk.
"""

from __future__ import annotations

import random as random_module
import time as time_module
from dataclasses import dataclass, field
from typing import Callable

from .base import ShapedChunk


# ---------------------------------------------------------------------------
# TimingPolicy
# ---------------------------------------------------------------------------


@dataclass
class TimingPolicy:
    """Configuration for per-frame delay / jitter.

    Attributes:
        enabled: Master switch. When False, no delay is applied.
        mode: "metadata_only" — set chunk.delay_ms, do not sleep.
              "runtime_sleep" — call sleep_fn with delay_ms. Must be explicitly
              configured; the constructor raises if mode is runtime_sleep
              without an explicit caller opt-in.
        min_delay_ms: Minimum delay in milliseconds.
        max_delay_ms: Maximum delay in milliseconds.
        target_min_rtt_ms: Optional target minimum RTT for pacing; if set,
            the controller may increase delay to meet this floor.
        randomize_intervals: If True, randomize delay within [min, max].
            If False, delay is deterministic (= min_delay_ms when enabled).
        seed: Optional seed for reproducible randomization.
    """

    enabled: bool = False
    mode: str = "metadata_only"
    min_delay_ms: float = 0.0
    max_delay_ms: float = 0.0
    target_min_rtt_ms: float | None = None
    randomize_intervals: bool = False
    seed: int | None = None

    _VALID_MODES = frozenset({"metadata_only", "runtime_sleep"})

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate configuration. Raises ValueError on invalid values."""
        if self.mode not in self._VALID_MODES:
            raise ValueError(
                f"Invalid timing mode {self.mode!r}; must be one of {sorted(self._VALID_MODES)}"
            )
        if self.min_delay_ms < 0:
            raise ValueError("min_delay_ms must be >= 0")
        if self.max_delay_ms < self.min_delay_ms:
            raise ValueError(
                f"max_delay_ms ({self.max_delay_ms}) must be >= "
                f"min_delay_ms ({self.min_delay_ms})"
            )
        if self.target_min_rtt_ms is not None and self.target_min_rtt_ms < 0:
            raise ValueError("target_min_rtt_ms must be >= 0")


SleepFn = Callable[[float], None]


# ---------------------------------------------------------------------------
# TimingController
# ---------------------------------------------------------------------------


class TimingController:
    """Runtime controller for applying TimingPolicy to ShapedChunks.

    The controller is designed to be instantiated per TrafficShaper session.
    It reads policy parameters and decides whether to set delay_ms metadata,
    sleep, or do nothing — depending on mode and the sleep_fn provided.

    Usage in core _send_shaped:

        for chunk in chunks:
            timing.apply(chunk)          # sets delay_ms and/or sleeps
            transport.send(chunk.data)

    The sleep_fn is injectable for testing:

        TimingController(policy, sleep_fn=fake_sleep)
    """

    def __init__(
        self,
        policy: TimingPolicy,
        sleep_fn: SleepFn | None = None,
    ):
        policy.validate()
        self._policy = policy
        self._rng = random_module.Random(policy.seed) if policy.seed is not None else random_module.Random(42)
        self._sleep_fn: SleepFn = sleep_fn or time_module.sleep

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def policy(self) -> TimingPolicy:
        return self._policy

    @property
    def sleep_fn(self) -> SleepFn:
        return self._sleep_fn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_delay_ms(self) -> float:
        """Return the delay in ms for the current chunk, or 0.0 if disabled.

        Does NOT modify any chunk — callers can use this independently.
        """
        if not self._policy.enabled:
            return 0.0

        if self._policy.randomize_intervals and self._policy.max_delay_ms > self._policy.min_delay_ms:
            delay = self._rng.uniform(self._policy.min_delay_ms, self._policy.max_delay_ms)
        else:
            delay = self._policy.min_delay_ms

        return max(delay, 0.0)

    def apply(self, chunk: ShapedChunk) -> None:
        """Apply timing policy to a single ShapedChunk.

        Sets chunk.delay_ms to the computed delay.
        If mode is runtime_sleep, calls self.sleep_fn(delay_ms / 1000.0).

        Does NOT sleep for dummy chunks or when disabled.
        """
        delay_ms = self.compute_delay_ms()
        chunk.delay_ms = delay_ms

        if self._policy.enabled and self._policy.mode == "runtime_sleep" and delay_ms > 0:
            self._sleep_fn(delay_ms / 1000.0)

    def apply_with_frame_type(self, chunk: ShapedChunk, frame_type_str: str) -> None:
        """Apply timing policy, skipping delay for non-DATA frames.

        HEARTBEAT, AUTH, and CLOSE frames are never delayed.
        If delay is skipped, chunk.delay_ms is still set but sleep is not called
        (even in runtime_sleep mode).
        """
        delay_ms = self.compute_delay_ms()
        chunk.delay_ms = delay_ms

        if frame_type_str.upper() == "DATA":
            if self._policy.enabled and self._policy.mode == "runtime_sleep" and delay_ms > 0:
                self._sleep_fn(delay_ms / 1000.0)


# ---------------------------------------------------------------------------
# DummyFramePolicy
# ---------------------------------------------------------------------------


@dataclass
class DummyFramePolicy:
    """Configuration for synthetic dummy frame generation.

    This is a lightweight hook, NOT a background thread. Callers call
    maybe_generate_dummy() to get zero or more ShapedChunk marked is_dummy=True.

    Design:
    - Default disabled (enabled=False).
    - Probability check on each call to maybe_generate_dummy().
    - Dummy chunk size is randomly chosen in [1, max_dummy_bytes].
    - All randomness uses injectable seed.
    - No automatic send — caller decides when to send dummy chunks.

    Real dummy traffic scheduling is deferred to a later phase.
    """

    enabled: bool = False
    max_dummy_bytes: int = 128
    probability: float = 0.0
    seed: int | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate configuration."""
        if self.max_dummy_bytes < 1:
            raise ValueError("max_dummy_bytes must be >= 1")
        if not (0.0 <= self.probability <= 1.0):
            raise ValueError("probability must be in [0.0, 1.0]")

    def maybe_generate_dummy(self, rng: random_module.Random | None = None) -> list[ShapedChunk]:
        """Generate zero or one dummy ShapedChunk, probabilistically.

        Returns an empty list when disabled or when the random check fails.

        Args:
            rng: Optional random.Random for reproducibility.
        """
        if not self.enabled:
            return []

        _rng = rng or random_module.Random(self.seed or 42)

        if self.probability <= 0.0:
            return []
        if self._should_emit(_rng):
            size = _rng.randint(1, self.max_dummy_bytes)
            dummy_data = bytes(_rng.getrandbits(8) for _ in range(size))
            return [ShapedChunk(data=dummy_data, is_dummy=True)]
        return []

    def _should_emit(self, rng: random_module.Random) -> bool:
        if self.probability >= 1.0:
            return True
        return rng.random() < self.probability
