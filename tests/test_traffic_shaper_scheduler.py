"""Tests for src/shaping/scheduler.py — SendScheduler frame-type-aware flush policy."""

from __future__ import annotations

import random

import pytest

from src.common.frame import FrameType, create_frame, encode_frame
from src.shaping.aggregation import AggregationShaper, MAGIC_AGG
from src.shaping.base import NoopTrafficShaper, ShapedChunk, TrafficShaper
from src.shaping.factory import PipelineTrafficShaper, create_traffic_shaper
from src.shaping.config import ShapingConfig
from src.shaping.jitter import JitterShaper
from src.shaping.padding import PaddingShaper
from src.shaping.scheduler import SendScheduler


# ---------------------------------------------------------------------------
# 1. Noop scheduler — passthrough
# ---------------------------------------------------------------------------

class TestSendSchedulerNoop:
    def test_data_passthrough(self):
        sched = SendScheduler(NoopTrafficShaper())
        chunks = sched.send(b"hello-data", FrameType.DATA)
        assert len(chunks) == 1
        assert chunks[0].data == b"hello-data"

    def test_heartbeat_passthrough(self):
        sched = SendScheduler(NoopTrafficShaper())
        chunks = sched.send(b"hello-hb", FrameType.HEARTBEAT)
        assert len(chunks) == 1
        assert chunks[0].data == b"hello-hb"

    def test_auth_passthrough(self):
        sched = SendScheduler(NoopTrafficShaper())
        chunks = sched.send(b"auth-data", FrameType.AUTH)
        assert len(chunks) == 1
        assert chunks[0].data == b"auth-data"

    def test_flush_returns_empty_for_noop(self):
        sched = SendScheduler(NoopTrafficShaper())
        assert sched.flush() == []

    def test_close_returns_empty_for_noop(self):
        sched = SendScheduler(NoopTrafficShaper())
        assert sched.close() == []


# ---------------------------------------------------------------------------
# 2. Data buffering with aggregation
# ---------------------------------------------------------------------------

class TestSendSchedulerAggregationBuffering:
    def test_data_frames_are_buffered(self):
        rng = random.Random(1)
        agg = AggregationShaper(max_bytes=4096, max_delay_ms=50, rng=rng, enabled=True)
        sched = SendScheduler(agg)
        result = sched.send(b"small-frame", FrameType.DATA)
        assert result == []  # buffered, not emitted

    def test_data_flush_on_size_threshold(self):
        rng = random.Random(1)
        # Small max_bytes so single frame doesn't fill, two frames do
        agg = AggregationShaper(max_bytes=20, max_delay_ms=50, rng=rng, enabled=True)
        sched = SendScheduler(agg)
        sched.send(b"A" * 10, FrameType.DATA)  # buffered (10 < 20)
        chunks = sched.send(b"B" * 15, FrameType.DATA)  # triggers auto-flush (10+15 >= 20)
        assert len(chunks) == 1
        assert MAGIC_AGG in chunks[0].data

    def test_flush_emits_buffered_data(self):
        rng = random.Random(1)
        agg = AggregationShaper(max_bytes=4096, rng=rng, enabled=True)
        sched = SendScheduler(agg)
        sched.send(b"frame-1", FrameType.DATA)
        sched.send(b"frame-2", FrameType.DATA)
        assert len(agg._buffer) == 2

        flushed = sched.flush()
        assert len(flushed) >= 1
        assert len(agg._buffer) == 0
        # Single aggregated chunk containing both frames
        assert MAGIC_AGG in flushed[0].data

    def test_close_emits_buffered_data(self):
        rng = random.Random(1)
        agg = AggregationShaper(max_bytes=4096, rng=rng, enabled=True)
        sched = SendScheduler(agg)
        sched.send(b"frame-1", FrameType.DATA)
        sched.send(b"frame-2", FrameType.DATA)
        closed = sched.close()
        assert len(closed) >= 1
        assert len(agg._buffer) == 0


# ---------------------------------------------------------------------------
# 3. Control frames trigger pre-flush
# ---------------------------------------------------------------------------

class TestSendSchedulerControlFrameFlush:
    def test_heartbeat_flushes_buffered_data(self):
        rng = random.Random(1)
        agg = AggregationShaper(max_bytes=4096, rng=rng, enabled=True)
        sched = SendScheduler(agg)
        sched.send(b"data-1", FrameType.DATA)
        sched.send(b"data-2", FrameType.DATA)
        assert len(agg._buffer) == 2

        chunks = sched.send(b"hb-frame", FrameType.HEARTBEAT)
        # Should get: flushed chunk(s) + hb chunk
        assert len(chunks) >= 2  # aggregated DATA + hb
        assert len(agg._buffer) == 0  # buffer drained
        # Last chunk should be hb
        assert chunks[-1].data == b"hb-frame"

    def test_auth_flushes_buffered_data(self):
        rng = random.Random(1)
        agg = AggregationShaper(max_bytes=4096, rng=rng, enabled=True)
        sched = SendScheduler(agg)
        sched.send(b"pre-auth-data", FrameType.DATA)
        assert len(agg._buffer) == 1

        chunks = sched.send(b"auth-frame", FrameType.AUTH)
        assert len(chunks) >= 2
        assert len(agg._buffer) == 0
        assert chunks[-1].data == b"auth-frame"

    def test_multiple_control_frames_in_sequence(self):
        rng = random.Random(1)
        agg = AggregationShaper(max_bytes=4096, rng=rng, enabled=True)
        sched = SendScheduler(agg)
        sched.send(b"data-only", FrameType.DATA)
        assert len(agg._buffer) == 1

        # First control frame flushes
        chunks1 = sched.send(b"hb-1", FrameType.HEARTBEAT)
        assert len(agg._buffer) == 0
        # Second control frame has nothing to flush
        chunks2 = sched.send(b"hb-2", FrameType.HEARTBEAT)
        assert chunks2 == [ShapedChunk(data=b"hb-2")]


# ---------------------------------------------------------------------------
# 4. Pipeline shaper integration
# ---------------------------------------------------------------------------

class TestSendSchedulerPipeline:
    def test_pipeline_aggregation_padding_control_flush(self):
        rng = random.Random(42)
        config = ShapingConfig(
            enabled=True,
            aggregation_enabled=True,
            aggregation_max_bytes=4096,
            padding_enabled=True,
            min_padding_bytes=4,
            max_padding_bytes=8,
        )
        shaper = create_traffic_shaper(config, seed=42)
        assert isinstance(shaper, PipelineTrafficShaper)
        sched = SendScheduler(shaper)

        # Buffer DATA
        result = sched.send(b"data-1", FrameType.DATA)
        assert result == []  # buffered in aggregation stage

        # Control frame triggers flush through pipeline
        chunks = sched.send(b"heartbeat", FrameType.HEARTBEAT)
        assert len(chunks) >= 2  # flushed aggregate + hb
        # Both chunks should have been through padding (VPAD envelope)
        for ch in chunks:
            assert ch.data[:4] == b"VPAD"

    def test_pipeline_no_aggregation_no_buffering(self):
        rng = random.Random(42)
        config = ShapingConfig(
            enabled=True,
            padding_enabled=True,
            min_padding_bytes=4,
            max_padding_bytes=4,
        )
        shaper = create_traffic_shaper(config, seed=42)
        sched = SendScheduler(shaper)
        chunks = sched.send(b"data", FrameType.DATA)
        assert len(chunks) == 1
        assert chunks[0].data[:4] == b"VPAD"


# ---------------------------------------------------------------------------
# 5. Aggregation disabled — no buffering
# ---------------------------------------------------------------------------

class TestSendSchedulerDisabledAggregation:
    def test_disabled_aggregation_passthrough_data(self):
        rng = random.Random(1)
        agg = AggregationShaper(max_bytes=4096, rng=rng, enabled=False)
        sched = SendScheduler(agg)
        chunks = sched.send(b"some-data", FrameType.DATA)
        assert len(chunks) == 1
        assert chunks[0].data == b"some-data"


# ---------------------------------------------------------------------------
# 6. Jitter metadata — no sleep
# ---------------------------------------------------------------------------

class TestSendSchedulerJitter:
    def test_jitter_sets_delay_metadata_only(self):
        rng = random.Random(1)
        jitter = JitterShaper(min_ms=10, max_ms=50, rng=rng, enabled=True)
        sched = SendScheduler(jitter)
        chunks = sched.send(b"test", FrameType.DATA)
        assert len(chunks) == 1
        assert chunks[0].delay_ms >= 10
        assert chunks[0].delay_ms <= 50

    def test_jitter_control_frame_also_gets_delay(self):
        rng = random.Random(1)
        jitter = JitterShaper(min_ms=5, max_ms=10, rng=rng, enabled=True)
        sched = SendScheduler(jitter)
        chunks = sched.send(b"hb", FrameType.HEARTBEAT)
        assert len(chunks) == 1
        assert chunks[0].delay_ms >= 5
