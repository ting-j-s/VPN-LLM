"""Tests for src/shaping/factory.py — shaper creation and pipeline composition."""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.shaping.config import ShapingConfig
from src.shaping.base import NoopTrafficShaper, TrafficShaper
from src.shaping.factory import PipelineTrafficShaper, create_traffic_shaper
from src.shaping.padding import PaddingShaper
from src.shaping.aggregation import AggregationShaper
from src.shaping.jitter import JitterShaper


class TestCreateTrafficShaper:
    def test_disabled_returns_noop(self):
        config = ShapingConfig(enabled=False, padding_enabled=True,
                               min_padding_bytes=10, max_padding_bytes=20)
        shaper = create_traffic_shaper(config)
        assert isinstance(shaper, NoopTrafficShaper)

    def test_enabled_no_strategies_returns_noop(self):
        config = ShapingConfig(enabled=True)  # no strategies enabled
        shaper = create_traffic_shaper(config)
        assert isinstance(shaper, NoopTrafficShaper)

    def test_enabled_padding_only(self):
        config = ShapingConfig(
            enabled=True,
            padding_enabled=True,
            min_padding_bytes=10,
            max_padding_bytes=50,
        )
        shaper = create_traffic_shaper(config)
        assert isinstance(shaper, PipelineTrafficShaper)
        assert len(shaper._stages) == 1
        assert isinstance(shaper._stages[0], PaddingShaper)

    def test_enabled_aggregation_only(self):
        config = ShapingConfig(
            enabled=True,
            aggregation_enabled=True,
            aggregation_max_bytes=4096,
        )
        shaper = create_traffic_shaper(config)
        assert isinstance(shaper, PipelineTrafficShaper)
        assert isinstance(shaper._stages[0], AggregationShaper)

    def test_enabled_jitter_only(self):
        config = ShapingConfig(
            enabled=True,
            jitter_enabled=True,
            jitter_min_ms=1.0,
            jitter_max_ms=10.0,
        )
        shaper = create_traffic_shaper(config)
        assert isinstance(shaper, PipelineTrafficShaper)
        assert isinstance(shaper._stages[0], JitterShaper)

    def test_multiple_strategies_in_pipeline_order(self):
        config = ShapingConfig(
            enabled=True,
            aggregation_enabled=True,
            aggregation_max_bytes=4096,
            padding_enabled=True,
            min_padding_bytes=10,
            max_padding_bytes=50,
            jitter_enabled=True,
            jitter_min_ms=1.0,
            jitter_max_ms=10.0,
        )
        shaper = create_traffic_shaper(config)
        assert isinstance(shaper, PipelineTrafficShaper)
        assert len(shaper._stages) == 3
        assert isinstance(shaper._stages[0], AggregationShaper)
        assert isinstance(shaper._stages[1], PaddingShaper)
        assert isinstance(shaper._stages[2], JitterShaper)

    def test_deterministic_with_seed(self):
        config = ShapingConfig(
            enabled=True,
            padding_enabled=True,
            min_padding_bytes=10,
            max_padding_bytes=100,
        )
        s1 = create_traffic_shaper(config, seed=42)
        s2 = create_traffic_shaper(config, seed=42)
        frame = b"test frame data"
        chunks1 = s1.encode_frame(frame)
        chunks2 = s2.encode_frame(frame)
        assert chunks1 == chunks2

    def test_padding_zero_max_returns_noop(self):
        config = ShapingConfig(
            enabled=True,
            padding_enabled=True,
            max_padding_bytes=0,
        )
        shaper = create_traffic_shaper(config)
        assert isinstance(shaper, NoopTrafficShaper)

    def test_aggregation_zero_max_returns_noop(self):
        config = ShapingConfig(
            enabled=True,
            aggregation_enabled=True,
            aggregation_max_bytes=0,
        )
        shaper = create_traffic_shaper(config)
        assert isinstance(shaper, NoopTrafficShaper)

    def test_invalid_config_raises(self):
        config = ShapingConfig(
            enabled=True,
            padding_enabled=True,
            min_padding_bytes=100,
            max_padding_bytes=10,  # invalid
        )
        with pytest.raises(ValueError):
            create_traffic_shaper(config)


class TestPipelineTrafficShaper:
    def test_empty_pipeline_is_noop(self):
        shaper = PipelineTrafficShaper(stages=[])
        frame = b"test"
        chunks = shaper.encode_frame(frame)
        assert len(chunks) == 1
        assert chunks[0].data == frame
        assert shaper.decode_chunk(frame) == [frame]

    def test_padding_aggregation_pipeline_roundtrip(self):
        rng = random.Random(42)
        config = ShapingConfig(
            enabled=True,
            aggregation_enabled=True,
            aggregation_max_bytes=4096,
            padding_enabled=True,
            min_padding_bytes=5,
            max_padding_bytes=20,
        )
        config.validate()
        stages = [
            AggregationShaper(max_bytes=4096, rng=rng, enabled=True),
            PaddingShaper(min_padding_bytes=5, max_padding_bytes=20, rng=rng, enabled=True),
        ]
        shaper = PipelineTrafficShaper(stages=stages, rng=rng)

        frames = [b"frame1", b"frame2", b"frame3larger"]
        for f in frames:
            shaper.encode_frame(f)
        chunks = shaper.flush()

        decoded_all: list[bytes] = []
        for ch in chunks:
            decoded_all.extend(shaper.decode_chunk(ch.data))
        assert decoded_all == frames

    def test_pipeline_decode_in_reverse_order(self):
        rng = random.Random(1)
        padding = PaddingShaper(min_padding_bytes=10, max_padding_bytes=10, rng=rng, enabled=True)
        shaper = PipelineTrafficShaper(stages=[padding], rng=rng)
        frame = b"roundtrip test"
        chunks = shaper.encode_frame(frame)
        decoded = shaper.decode_chunk(chunks[0].data)
        assert decoded == [frame]

    def test_flush_propagates_through_stages(self):
        rng = random.Random(42)
        agg = AggregationShaper(max_bytes=4096, rng=rng, enabled=True)
        padding = PaddingShaper(min_padding_bytes=5, max_padding_bytes=10, rng=rng, enabled=True)
        shaper = PipelineTrafficShaper(stages=[agg, padding], rng=rng)

        shaper.encode_frame(b"a")
        shaper.encode_frame(b"b")
        chunks = shaper.flush()

        # Decode back
        decoded: list[bytes] = []
        for ch in chunks:
            decoded.extend(shaper.decode_chunk(ch.data))
        assert decoded == [b"a", b"b"]
