"""Traffic shaper factory — assemble shaping pipeline from config.

Default pipeline (when enabled):
   frame → aggregation → padding → jitter (metadata only)

Fragmentation is NOT in the default pipeline — its decode path requires
cross-chunk reassembly that must be validated before production use.
"""

from __future__ import annotations

import random as random_module

from .base import NoopTrafficShaper, ShapedChunk, TrafficShaper
from .config import ShapingConfig
from .padding import PaddingShaper
from .aggregation import AggregationShaper
from .jitter import JitterShaper
from .timing import TimingPolicy, TimingController


class PipelineTrafficShaper(TrafficShaper):
    """Composite shaper that chains multiple strategies in sequence.

    Pipeline: aggregation → padding → jitter

    Each stage passes its ShapedChunks through to the next stage.
    Decode reverses the order.
    """

    def __init__(self, stages: list[TrafficShaper], rng: random_module.Random | None = None):
        super().__init__(rng)
        self._stages = stages

    def encode_frame(self, frame: bytes) -> list[ShapedChunk]:
        chunks: list[ShapedChunk] = [ShapedChunk(data=frame)]
        for stage in self._stages:
            next_chunks: list[ShapedChunk] = []
            for ch in chunks:
                next_chunks.extend(stage.encode_frame(ch.data))
            chunks = next_chunks
        return chunks

    def decode_chunk(self, chunk: bytes) -> list[bytes]:
        # Decode in reverse order
        frames = [chunk]
        for stage in reversed(self._stages):
            next_frames: list[bytes] = []
            for f in frames:
                next_frames.extend(stage.decode_chunk(f))
            frames = next_frames
        return frames

    def flush(self) -> list[ShapedChunk]:
        chunks: list[ShapedChunk] = []
        for stage in self._stages:
            flushed = stage.flush()
            if flushed:
                # Apply remaining (downstream) stages to flushed chunks
                for ch in flushed:
                    partial = [ch]
                    for downstream in self._stages[self._stages.index(stage) + 1:]:
                        partial = [s for p in partial for s in downstream.encode_frame(p.data)]
                    chunks.extend(partial)
        return chunks

    def close(self) -> list[ShapedChunk]:
        chunks: list[ShapedChunk] = []
        for stage in self._stages:
            chunks.extend(stage.close())
        return chunks


def create_traffic_shaper(config: ShapingConfig,
                          seed: int | None = None) -> TrafficShaper:
    """Create a TrafficShaper from configuration.

    Args:
        config: ShapingConfig with strategy settings.
        seed: Random seed for deterministic behavior. None = fixed seed 42.

    Returns:
        TrafficShaper instance. NoopTrafficShaper if config.enabled is False.
    """
    config.validate()

    if not config.enabled:
        return NoopTrafficShaper()

    rng = random_module.Random(seed if seed is not None else 42)
    stages: list[TrafficShaper] = []

    if config.aggregation_enabled and config.aggregation_max_bytes > 0:
        stages.append(AggregationShaper(
            max_delay_ms=config.aggregation_max_delay_ms,
            max_bytes=config.aggregation_max_bytes,
            rng=rng,
            enabled=True,
        ))

    if config.padding_enabled and config.max_padding_bytes > 0:
        stages.append(PaddingShaper(
            min_padding_bytes=config.min_padding_bytes,
            max_padding_bytes=config.max_padding_bytes,
            rng=rng,
            enabled=True,
        ))

    if config.jitter_enabled and config.jitter_max_ms > 0:
        stages.append(JitterShaper(
            min_ms=config.jitter_min_ms,
            max_ms=config.jitter_max_ms,
            rng=rng,
            enabled=True,
        ))

    if not stages:
        return _attach_timing(NoopTrafficShaper(), config)

    return _attach_timing(PipelineTrafficShaper(stages=stages, rng=rng), config)


def _attach_timing(shaper: TrafficShaper, config: ShapingConfig) -> TrafficShaper:
    """Attach a TimingController to the shaper if timing_enabled.

    Does NOT modify the constructor — just sets an attribute.
    In metadata_only mode, chunk.delay_ms is set but no sleep occurs.
    In runtime_sleep mode, delay is applied via time.sleep.
    """
    if config.timing_enabled:
        policy = TimingPolicy(
            enabled=True,
            mode=config.timing_mode,
            min_delay_ms=config.timing_min_delay_ms,
            max_delay_ms=config.timing_max_delay_ms,
            target_min_rtt_ms=config.timing_target_min_rtt_ms,
            randomize_intervals=config.timing_randomize_intervals,
        )
        shaper.timing_controller = TimingController(policy)
    return shaper
