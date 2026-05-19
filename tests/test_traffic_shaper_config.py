"""Tests for src/shaping/config.py — ShapingConfig validation and serialization."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.shaping.config import ShapingConfig


class TestShapingConfigDefaults:
    def test_all_disabled_by_default(self):
        c = ShapingConfig()
        assert c.enabled is False
        assert c.padding_enabled is False
        assert c.aggregation_enabled is False
        assert c.fragmentation_enabled is False
        assert c.jitter_enabled is False
        assert c.dummy_enabled is False

    def test_default_values_zero(self):
        c = ShapingConfig()
        assert c.min_padding_bytes == 0
        assert c.max_padding_bytes == 0
        assert c.aggregation_max_delay_ms == 0.0
        assert c.aggregation_max_bytes == 0
        assert c.fragmentation_min_size == 0
        assert c.fragmentation_max_chunk_size == 0
        assert c.jitter_min_ms == 0.0
        assert c.jitter_max_ms == 0.0


class TestShapingConfigValidation:
    def test_defaults_valid(self):
        c = ShapingConfig()
        c.validate()  # should not raise

    def test_padding_range_valid(self):
        c = ShapingConfig(padding_enabled=True, min_padding_bytes=10, max_padding_bytes=100)
        c.validate()

    def test_negative_min_padding_raises(self):
        c = ShapingConfig(padding_enabled=True, min_padding_bytes=-1, max_padding_bytes=100)
        with pytest.raises(ValueError, match="min_padding_bytes"):
            c.validate()

    def test_max_less_than_min_padding_raises(self):
        c = ShapingConfig(padding_enabled=True, min_padding_bytes=50, max_padding_bytes=10)
        with pytest.raises(ValueError, match="max_padding_bytes"):
            c.validate()

    def test_negative_aggregation_max_bytes_raises(self):
        c = ShapingConfig(aggregation_enabled=True, aggregation_max_bytes=-1)
        with pytest.raises(ValueError, match="aggregation_max_bytes"):
            c.validate()

    def test_negative_aggregation_delay_raises(self):
        c = ShapingConfig(aggregation_enabled=True, aggregation_max_delay_ms=-1.0)
        with pytest.raises(ValueError, match="aggregation_max_delay_ms"):
            c.validate()

    def test_fragmentation_range_valid(self):
        c = ShapingConfig(fragmentation_enabled=True, fragmentation_min_size=100,
                          fragmentation_max_chunk_size=200)
        c.validate()

    def test_max_chunk_less_than_min_size_raises(self):
        c = ShapingConfig(fragmentation_enabled=True, fragmentation_min_size=200,
                          fragmentation_max_chunk_size=100)
        with pytest.raises(ValueError, match="fragmentation_max_chunk_size"):
            c.validate()

    def test_jitter_range_valid(self):
        c = ShapingConfig(jitter_enabled=True, jitter_min_ms=1.0, jitter_max_ms=10.0)
        c.validate()

    def test_max_jitter_less_than_min_raises(self):
        c = ShapingConfig(jitter_enabled=True, jitter_min_ms=10.0, jitter_max_ms=1.0)
        with pytest.raises(ValueError, match="jitter_max_ms"):
            c.validate()


class TestShapingConfigSerialization:
    def test_to_dict_defaults(self):
        d = ShapingConfig().to_dict()
        assert d["enabled"] is False

    def test_from_dict_defaults(self):
        c = ShapingConfig.from_dict({})
        assert c.enabled is False

    def test_to_dict_roundtrip(self):
        orig = ShapingConfig(
            enabled=True,
            padding_enabled=True,
            min_padding_bytes=10,
            max_padding_bytes=50,
            aggregation_enabled=True,
            aggregation_max_delay_ms=5.0,
            aggregation_max_bytes=4096,
        )
        d = orig.to_dict()
        restored = ShapingConfig.from_dict(d)
        assert restored.enabled == orig.enabled
        assert restored.padding_enabled == orig.padding_enabled
        assert restored.min_padding_bytes == orig.min_padding_bytes
        assert restored.max_padding_bytes == orig.max_padding_bytes
        assert restored.aggregation_enabled == orig.aggregation_enabled
        assert restored.aggregation_max_delay_ms == orig.aggregation_max_delay_ms
        assert restored.aggregation_max_bytes == orig.aggregation_max_bytes

    def test_json_serializable(self):
        c = ShapingConfig(enabled=True)
        json.dumps(c.to_dict())  # should not raise

    def test_from_dict_partial(self):
        c = ShapingConfig.from_dict({"enabled": True, "padding_enabled": True})
        assert c.enabled is True
        assert c.padding_enabled is True
        assert c.aggregation_enabled is False  # default
