"""Tests for Phase 7B timing countermeasures: TimingPolicy, TimingController,
DummyFramePolicy, ShapingConfig extension, and factory integration."""

import time as _time

import pytest

from src.shaping.base import NoopTrafficShaper, ShapedChunk
from src.shaping.config import ShapingConfig
from src.shaping.factory import create_traffic_shaper
from src.shaping.timing import (
    DummyFramePolicy,
    TimingController,
    TimingPolicy,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_policy():
    return TimingPolicy()


@pytest.fixture
def fake_sleep():
    """Collect sleep calls without actually sleeping."""
    calls: list[float] = []

    def _fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    return _fake_sleep, calls


# ---------------------------------------------------------------------------
# TimingPolicy
# ---------------------------------------------------------------------------


class TestTimingPolicyDefaults:
    """Default TimingPolicy is fully disabled."""

    def test_default_disabled(self):
        policy = TimingPolicy()
        assert policy.enabled is False
        assert policy.mode == "metadata_only"
        assert policy.min_delay_ms == 0.0
        assert policy.max_delay_ms == 0.0

    def test_validation_passes_default(self):
        policy = TimingPolicy()
        policy.validate()  # does not raise

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid timing mode"):
            TimingPolicy(mode="invalid_mode")

    def test_negative_min_delay_raises(self):
        with pytest.raises(ValueError, match="min_delay_ms must be >= 0"):
            TimingPolicy(enabled=True, min_delay_ms=-1.0)

    def test_max_less_than_min_raises(self):
        with pytest.raises(ValueError, match="max_delay_ms"):
            TimingPolicy(enabled=True, min_delay_ms=10.0, max_delay_ms=5.0)

    def test_negative_target_min_rtt_raises(self):
        with pytest.raises(ValueError, match="target_min_rtt_ms must be >= 0"):
            TimingPolicy(enabled=True, target_min_rtt_ms=-1.0)

    def test_runtime_sleep_valid_with_explicit_mode(self):
        policy = TimingPolicy(enabled=True, mode="runtime_sleep", min_delay_ms=1.0, max_delay_ms=10.0)
        policy.validate()  # does not raise


class TestTimingPolicyEnabled:
    """TimingPolicy with enabled=True."""

    def test_metadata_only_mode_valid(self):
        policy = TimingPolicy(enabled=True, mode="metadata_only", min_delay_ms=1.0, max_delay_ms=10.0)
        policy.validate()

    def test_runtime_sleep_mode_valid(self):
        policy = TimingPolicy(enabled=True, mode="runtime_sleep", min_delay_ms=1.0, max_delay_ms=10.0)
        policy.validate()


# ---------------------------------------------------------------------------
# TimingController
# ---------------------------------------------------------------------------


class TestTimingController:
    """Verify TimingController behavior."""

    def test_compute_delay_disabled_returns_zero(self, default_policy):
        tc = TimingController(default_policy)
        assert tc.compute_delay_ms() == 0.0

    def test_compute_delay_enabled_deterministic(self):
        policy = TimingPolicy(enabled=True, min_delay_ms=5.0, max_delay_ms=20.0, randomize_intervals=False)
        tc = TimingController(policy)
        assert tc.compute_delay_ms() == 5.0

    def test_compute_delay_randomized_reproducible(self):
        policy1 = TimingPolicy(
            enabled=True, min_delay_ms=5.0, max_delay_ms=20.0,
            randomize_intervals=True, seed=123
        )
        policy2 = TimingPolicy(
            enabled=True, min_delay_ms=5.0, max_delay_ms=20.0,
            randomize_intervals=True, seed=123
        )
        tc1 = TimingController(policy1)
        tc2 = TimingController(policy2)
        # Should produce same sequence
        for _ in range(20):
            assert tc1.compute_delay_ms() == tc2.compute_delay_ms()

    def test_compute_delay_in_range(self):
        policy = TimingPolicy(
            enabled=True, min_delay_ms=5.0, max_delay_ms=20.0,
            randomize_intervals=True, seed=42
        )
        tc = TimingController(policy)
        for _ in range(100):
            d = tc.compute_delay_ms()
            assert 5.0 <= d <= 20.0

    def test_apply_metadata_only_no_sleep(self, default_policy, fake_sleep):
        sleep_fn, calls = fake_sleep
        policy = TimingPolicy(enabled=True, mode="metadata_only", min_delay_ms=5.0, max_delay_ms=10.0)
        tc = TimingController(policy, sleep_fn=sleep_fn)
        chunk = ShapedChunk(data=b"test")
        tc.apply(chunk)
        assert chunk.delay_ms >= 5.0
        assert len(calls) == 0  # metadata_only does not sleep

    def test_apply_runtime_sleep_calls_sleep(self, fake_sleep):
        sleep_fn, calls = fake_sleep
        policy = TimingPolicy(enabled=True, mode="runtime_sleep", min_delay_ms=5.0, max_delay_ms=5.0)
        tc = TimingController(policy, sleep_fn=sleep_fn)
        chunk = ShapedChunk(data=b"test")
        tc.apply(chunk)
        assert chunk.delay_ms == 5.0
        assert len(calls) == 1
        assert abs(calls[0] - 0.005) < 1e-9  # 5ms → 0.005s

    def test_apply_runtime_sleep_zero_delay_no_call(self, fake_sleep):
        sleep_fn, calls = fake_sleep
        policy = TimingPolicy(enabled=True, mode="runtime_sleep", min_delay_ms=0.0, max_delay_ms=0.0)
        tc = TimingController(policy, sleep_fn=sleep_fn)
        chunk = ShapedChunk(data=b"test")
        tc.apply(chunk)
        assert chunk.delay_ms == 0.0
        assert len(calls) == 0  # no sleep for zero delay

    def test_apply_disabled_no_sleep(self, fake_sleep):
        sleep_fn, calls = fake_sleep
        policy = TimingPolicy()
        tc = TimingController(policy, sleep_fn=sleep_fn)
        chunk = ShapedChunk(data=b"test")
        tc.apply(chunk)
        assert chunk.delay_ms == 0.0
        assert len(calls) == 0

    def test_apply_with_frame_type_skips_non_data(self, fake_sleep):
        """HEARTBEAT/AUTH/CLOSE frames are never delayed."""
        sleep_fn, calls = fake_sleep
        policy = TimingPolicy(enabled=True, mode="runtime_sleep", min_delay_ms=5.0, max_delay_ms=5.0)
        tc = TimingController(policy, sleep_fn=sleep_fn)

        for ft in ("HEARTBEAT", "AUTH", "CLOSE"):
            chunk = ShapedChunk(data=b"test")
            tc.apply_with_frame_type(chunk, ft)
            assert chunk.delay_ms == 5.0  # metadata set
            assert len(calls) == 0  # but NOT slept

        # Reset and check DATA does sleep
        chunk = ShapedChunk(data=b"data")
        tc.apply_with_frame_type(chunk, "DATA")
        assert chunk.delay_ms == 5.0
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# DummyFramePolicy
# ---------------------------------------------------------------------------


class TestDummyFramePolicy:
    """Verify DummyFramePolicy behavior."""

    def test_default_disabled_returns_empty(self):
        policy = DummyFramePolicy()
        assert policy.enabled is False
        assert policy.maybe_generate_dummy() == []

    def test_enabled_probability_zero_returns_empty(self):
        policy = DummyFramePolicy(enabled=True, probability=0.0)
        assert policy.maybe_generate_dummy() == []

    def test_enabled_probability_one_always_returns_chunk(self):
        policy = DummyFramePolicy(enabled=True, probability=1.0, max_dummy_bytes=32, seed=42)
        chunks = policy.maybe_generate_dummy()
        assert len(chunks) == 1
        assert chunks[0].is_dummy is True
        assert 1 <= len(chunks[0].data) <= 32

    def test_fixed_seed_reproducible(self):
        import random as _random
        policy = DummyFramePolicy(enabled=True, probability=1.0, max_dummy_bytes=64, seed=99)
        rng1 = _random.Random(99)
        rng2 = _random.Random(99)
        result1 = policy.maybe_generate_dummy(rng=rng1)
        result2 = policy.maybe_generate_dummy(rng=rng2)
        assert result1[0].data == result2[0].data

    def test_invalid_max_dummy_bytes_raises(self):
        with pytest.raises(ValueError, match="max_dummy_bytes must be >= 1"):
            DummyFramePolicy(max_dummy_bytes=0)

    def test_invalid_probability_raises(self):
        with pytest.raises(ValueError, match="probability must be"):
            DummyFramePolicy(probability=1.5)


# ---------------------------------------------------------------------------
# ShapingConfig timing extension
# ---------------------------------------------------------------------------


class TestShapingConfigExtension:
    """Verify ShapingConfig timing fields."""

    def test_default_timing_disabled(self):
        config = ShapingConfig()
        assert config.timing_enabled is False
        assert config.timing_mode == "metadata_only"
        assert config.timing_min_delay_ms == 0.0
        assert config.timing_max_delay_ms == 0.0

    def test_config_from_dict_old_compat(self):
        """Old config without timing fields should load with defaults."""
        old = {"enabled": True, "padding_enabled": True, "min_padding_bytes": 0, "max_padding_bytes": 100}
        config = ShapingConfig.from_dict(old)
        assert config.enabled is True
        assert config.padding_enabled is True
        assert config.timing_enabled is False  # default

    def test_config_from_dict_timing(self):
        d = {"timing_enabled": True, "timing_mode": "metadata_only", "timing_min_delay_ms": 5.0, "timing_max_delay_ms": 15.0}
        config = ShapingConfig.from_dict(d)
        assert config.timing_enabled is True
        assert config.timing_mode == "metadata_only"
        assert config.timing_min_delay_ms == 5.0
        assert config.timing_max_delay_ms == 15.0

    def test_config_roundtrip_timing(self):
        config = ShapingConfig(
            timing_enabled=True, timing_mode="metadata_only",
            timing_min_delay_ms=5.0, timing_max_delay_ms=15.0,
            timing_randomize_intervals=True,
        )
        d = config.to_dict()
        config2 = ShapingConfig.from_dict(d)
        assert config2.timing_enabled is True
        assert config2.timing_mode == "metadata_only"
        assert config2.timing_min_delay_ms == 5.0
        assert config2.timing_max_delay_ms == 15.0
        assert config2.timing_randomize_intervals is True

    def test_invalid_timing_mode_raises(self):
        config = ShapingConfig(timing_enabled=True, timing_mode="bad_mode")
        with pytest.raises(ValueError, match="timing_mode"):
            config.validate()

    def test_invalid_timing_delay_range_raises(self):
        config = ShapingConfig(timing_enabled=True, timing_min_delay_ms=10.0, timing_max_delay_ms=5.0)
        with pytest.raises(ValueError, match="timing_max_delay_ms"):
            config.validate()

    def test_negative_timing_target_rtt_raises(self):
        config = ShapingConfig(timing_enabled=True, timing_target_min_rtt_ms=-5.0)
        with pytest.raises(ValueError, match="timing_target_min_rtt_ms"):
            config.validate()


# ---------------------------------------------------------------------------
# Factory integration
# ---------------------------------------------------------------------------


class TestFactoryTimingIntegration:
    """Verify factory creates TimingController when timing_enabled."""

    def test_noop_shaper_no_timing_by_default(self):
        config = ShapingConfig()
        shaper = create_traffic_shaper(config)
        assert isinstance(shaper, NoopTrafficShaper)
        assert shaper.timing_controller is None

    def test_timing_enabled_attaches_controller(self):
        config = ShapingConfig(
            enabled=True,
            padding_enabled=True,
            min_padding_bytes=1,
            max_padding_bytes=10,
            timing_enabled=True,
            timing_min_delay_ms=5.0,
            timing_max_delay_ms=10.0,
        )
        shaper = create_traffic_shaper(config)
        assert shaper.timing_controller is not None
        tc = shaper.timing_controller
        assert tc.policy.enabled is True
        assert tc.policy.mode == "metadata_only"  # default

    def test_timing_no_sleep_by_default(self):
        """TimingController in metadata_only mode does NOT use real sleep."""
        config = ShapingConfig(
            enabled=True,
            padding_enabled=True,
            min_padding_bytes=1,
            max_padding_bytes=10,
            timing_enabled=True,
            timing_min_delay_ms=100.0,  # 100ms would be noticeable if slept
            timing_max_delay_ms=100.0,
        )
        shaper = create_traffic_shaper(config)
        tc = shaper.timing_controller
        assert tc is not None
        # apply should not call sleep in metadata_only mode
        chunk = ShapedChunk(data=b"test")
        tc.apply(chunk)
        assert chunk.delay_ms == 100.0
        # No real time elapsed — sleep was not called

    def test_timing_disabled_no_controller(self):
        config = ShapingConfig(timing_enabled=False)
        shaper = create_traffic_shaper(config)
        assert shaper.timing_controller is None


# ---------------------------------------------------------------------------
# Full baseline regression
# ---------------------------------------------------------------------------


class TestBaselineRegression:
    """Verify old shaper behavior is unchanged when timing is disabled."""

    def test_noop_shaper_unchanged(self):
        config = ShapingConfig()
        shaper = create_traffic_shaper(config)
        assert isinstance(shaper, NoopTrafficShaper)
        chunks = shaper.encode_frame(b"hello")
        assert len(chunks) == 1
        assert chunks[0].data == b"hello"
        assert chunks[0].delay_ms == 0.0

    def test_padding_shaper_unchanged(self):
        config = ShapingConfig(
            enabled=True,
            padding_enabled=True,
            min_padding_bytes=10,
            max_padding_bytes=10,
        )
        shaper = create_traffic_shaper(config, seed=42)
        chunks = shaper.encode_frame(b"hello")
        assert len(chunks) == 1
        assert len(chunks[0].data) > len(b"hello")  # padding applied
