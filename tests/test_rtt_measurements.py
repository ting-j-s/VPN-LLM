"""Tests for RTT measurement data structures and scoring functions."""

import pytest
from src.evaluation.rtt.rtt_measurements import (
    RTTMeasurement,
    CrossLayerRTTReport,
    summarize_samples,
    compute_rtt_diff,
    score_cross_layer_rtt,
    timing_stability_score,
)


class TestSummarizeSamples:
    """Test summarize_samples statistics computation."""

    def test_basic_statistics(self):
        samples = [1.0, 2.0, 3.0, 4.0, 5.0]
        m = summarize_samples("test", "application", samples)
        assert m.name == "test"
        assert m.layer == "application"
        assert m.min_ms == 1.0
        assert m.median_ms == 3.0
        assert m.avg_ms == 3.0
        assert m.max_ms == 5.0
        assert m.sample_count == 5

    def test_even_count_median(self):
        samples = [1.0, 2.0, 3.0, 4.0]
        m = summarize_samples("test", "transport", samples)
        assert m.median_ms == 2.5
        assert m.sample_count == 4

    def test_single_sample(self):
        samples = [5.0]
        m = summarize_samples("test", "network", samples)
        assert m.min_ms == 5.0
        assert m.median_ms == 5.0
        assert m.avg_ms == 5.0
        assert m.max_ms == 5.0
        assert m.sample_count == 1

    def test_empty_samples(self):
        m = summarize_samples("test", "application", [])
        assert m.sample_count == 0
        assert m.min_ms is None
        assert m.median_ms is None
        assert m.avg_ms is None
        assert m.max_ms is None

    def test_notes_preserved(self):
        m = summarize_samples("test", "application", [1.0], notes=["note1", "note2"])
        assert m.notes == ["note1", "note2"]

    def test_decimal_rounding(self):
        samples = [1.234, 2.567]
        m = summarize_samples("test", "application", samples)
        assert m.min_ms == 1.234
        assert m.avg_ms == 1.901  # (1.234 + 2.567) / 2 = 1.9005 → rounds to 1.901


class TestComputeRTTDiff:
    """Test compute_rtt_diff output."""

    def test_both_provided(self):
        diffs = compute_rtt_diff(42.0, 8.0)
        assert diffs["app_transport_diff_ms"] == 34.0
        assert diffs["app_network_diff_ms"] is None

    def test_app_only(self):
        diffs = compute_rtt_diff(42.0, None)
        assert diffs["app_transport_diff_ms"] is None
        assert diffs["app_network_diff_ms"] is None

    def test_with_network(self):
        diffs = compute_rtt_diff(42.0, 8.0, 5.0)
        assert diffs["app_transport_diff_ms"] == 34.0
        assert diffs["app_network_diff_ms"] == 37.0

    def test_none_app(self):
        diffs = compute_rtt_diff(None, 8.0)
        assert diffs["app_transport_diff_ms"] is None


class TestScoreCrossLayerRTT:
    """Test cross-layer RTT scoring."""

    def test_low_risk_tiny_diff(self):
        score, level = score_cross_layer_rtt(5.0)
        assert level == "low"
        assert score < 0.3

    def test_low_risk_boundary(self):
        score, level = score_cross_layer_rtt(14.0)
        assert level == "low"

    def test_medium_risk(self):
        score, level = score_cross_layer_rtt(30.0)
        assert level == "medium"
        assert 0.3 <= score < 0.6

    def test_medium_risk_boundary(self):
        score, level = score_cross_layer_rtt(49.0)
        assert level == "medium"

    def test_high_risk(self):
        score, level = score_cross_layer_rtt(55.0)
        assert level == "high"
        assert score >= 0.6

    def test_high_risk_very_large(self):
        score, level = score_cross_layer_rtt(200.0)
        assert level == "high"
        assert score >= 0.6

    def test_insufficient_data_none_diff(self):
        score, level = score_cross_layer_rtt(None)
        assert level == "insufficient_data"
        assert score == 0.0

    def test_stability_elevates_medium_to_high(self):
        score, level = score_cross_layer_rtt(30.0, timing_stability=0.8)
        assert level == "high"
        assert score >= 0.6

    def test_stability_does_not_elevate_low(self):
        score, level = score_cross_layer_rtt(10.0, timing_stability=0.9)
        assert level == "low"

    def test_custom_threshold(self):
        score, level = score_cross_layer_rtt(80.0, threshold_ms=100.0)
        assert level == "medium"

    def test_score_capped_at_one(self):
        score, level = score_cross_layer_rtt(10000.0)
        assert score <= 1.0


class TestTimingStabilityScore:
    """Test timing_stability_score computation."""

    def test_stable_small_samples(self):
        score = timing_stability_score([10.0, 10.1, 9.9, 10.0, 10.05])
        assert score is not None
        assert score > 0.8  # very stable → high score

    def test_variable_samples(self):
        score = timing_stability_score([1.0, 5.0, 10.0, 20.0, 50.0])
        assert score is not None
        assert score < 0.5  # highly variable → low score

    def test_fewer_than_two_samples(self):
        assert timing_stability_score([]) is None
        assert timing_stability_score([5.0]) is None

    def test_zero_mean(self):
        assert timing_stability_score([0.0, 0.0]) is None

    def test_perfectly_stable_all_same(self):
        score = timing_stability_score([5.0, 5.0, 5.0, 5.0, 5.0])
        assert score == 1.0  # cv=0 → highest stability


class TestCrossLayerRTTReport:
    """Test CrossLayerRTTReport dataclass."""

    def test_defaults(self):
        report = CrossLayerRTTReport()
        assert report.detector_name == "cross_layer_rtt"
        assert report.target == "127.0.0.1"
        assert report.trace_type == "mock"
        assert report.risk_level == "low"
        assert report.risk_score == 0.0
        assert report.measurements == []

    def test_to_dict(self):
        m = summarize_samples("app", "application", [1.0, 2.0, 3.0])
        report = CrossLayerRTTReport(
            application_rtt_ms=2.0,
            transport_rtt_ms=0.5,
            app_transport_diff_ms=1.5,
            risk_score=0.1,
            risk_level="low",
            measurements=[m],
            notes=["test"],
        )
        d = report.to_dict()
        assert d["detector_name"] == "cross_layer_rtt"
        assert d["application_rtt_ms"] == 2.0
        assert d["transport_rtt_ms"] == 0.5
        assert d["app_transport_diff_ms"] == 1.5
        assert d["risk_score"] == 0.1
        assert d["risk_level"] == "low"
        assert len(d["measurements"]) == 1
        assert d["notes"] == ["test"]
        assert "raw" in d
