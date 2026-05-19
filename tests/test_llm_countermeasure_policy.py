"""Tests for src.llm.detection.countermeasure_policy — CountermeasureHint mapping."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.detection.detector_report import DetectionMetric, DetectionReport
from src.llm.detection.gate import DetectionThresholds, evaluate_detection_report
from src.llm.detection.countermeasure_policy import (
    CountermeasureHint,
    suggest_countermeasures,
    get_hint_for_metric,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_metric(name, value, passed=True, severity="info"):
    return DetectionMetric(name=name, value=value, passed=passed, severity=severity)


def _make_report_with_failed_metrics(*metric_names_and_values):
    """Create a report with specific failed metrics."""
    metrics = []
    for name, value in metric_names_and_values:
        metrics.append(DetectionMetric(
            name=name, value=value, threshold=None,
            passed=False, severity="fail",
            explanation=f"{name}={value} exceeds threshold",
        ))
    return DetectionReport(
        detector_name="fingerprint",
        transport="tcp",
        scenario="idle",
        passed=False,
        metrics=metrics,
        risk_score=0.85,
    )


# ---------------------------------------------------------------------------
# CountermeasureHint
# ---------------------------------------------------------------------------

class TestCountermeasureHint:
    def test_basic_fields(self):
        h = CountermeasureHint(
            metric_name="repeated_length_ratio",
            problem="test problem",
            recommended_changes=["change1", "change2"],
            affected_layers=["frame codec"],
            tradeoffs=["tradeoff1"],
            avoid=["avoid1"],
        )
        assert h.metric_name == "repeated_length_ratio"
        assert len(h.recommended_changes) == 2
        assert len(h.avoid) == 1

    def test_to_dict(self):
        h = CountermeasureHint(metric_name="test", problem="p")
        d = h.to_dict()
        assert d["metric_name"] == "test"


# ---------------------------------------------------------------------------
# suggest_countermeasures
# ---------------------------------------------------------------------------

class TestSuggestCountermeasures:
    def test_repeated_length_ratio(self):
        report = _make_report_with_failed_metrics(
            ("repeated_length_ratio", 0.85),
        )
        hints = suggest_countermeasures(report)
        assert len(hints) == 1
        h = hints[0]
        assert h.metric_name == "repeated_length_ratio"
        assert any("padding" in c.lower() for c in h.recommended_changes)
        assert "frame codec" in h.affected_layers
        assert len(h.tradeoffs) > 0
        assert len(h.avoid) > 0

    def test_small_packet_ratio(self):
        report = _make_report_with_failed_metrics(
            ("small_packet_ratio", 0.75),
        )
        hints = suggest_countermeasures(report)
        assert len(hints) == 1
        h = hints[0]
        assert h.metric_name == "small_packet_ratio"
        assert any("aggregat" in c.lower() for c in h.recommended_changes)
        assert len(h.avoid) > 0

    def test_dominant_ngram_ratio(self):
        report = _make_report_with_failed_metrics(
            ("dominant_ngram_ratio", 0.65),
        )
        hints = suggest_countermeasures(report)
        assert len(hints) == 1
        h = hints[0]
        assert h.metric_name == "dominant_ngram_ratio"
        assert any("split" in c.lower() or "random" in c.lower() for c in h.recommended_changes)

    def test_ngram_entropy(self):
        report = _make_report_with_failed_metrics(
            ("ngram_entropy", 0.5),
        )
        hints = suggest_countermeasures(report)
        assert len(hints) == 1
        h = hints[0]
        assert h.metric_name == "ngram_entropy"
        assert any("random" in c.lower() or "dummy" in c.lower() for c in h.recommended_changes)

    def test_max_burst_size(self):
        report = _make_report_with_failed_metrics(
            ("max_burst_size", 10000),
        )
        hints = suggest_countermeasures(report)
        assert len(hints) == 1
        h = hints[0]
        assert h.metric_name == "max_burst_size"
        assert any("fragment" in c.lower() or "pac" in c.lower() for c in h.recommended_changes)

    def test_dominant_burst_direction_ratio(self):
        report = _make_report_with_failed_metrics(
            ("dominant_burst_direction_ratio", 0.95),
        )
        hints = suggest_countermeasures(report)
        assert len(hints) >= 1
        h = next(h for h in hints if h.metric_name == "dominant_burst_direction_ratio")
        assert any("pacing" in c.lower() or "dummy" in c.lower() for c in h.recommended_changes)

    def test_rtt_diff_ms(self):
        report = _make_report_with_failed_metrics(
            ("rtt_diff_ms", 30.0),
        )
        hints = suggest_countermeasures(report)
        assert len(hints) == 1
        h = hints[0]
        assert h.metric_name == "rtt_diff_ms"
        assert any("rtt" in c.lower() or "latency" in c.lower() for c in h.recommended_changes)

    def test_probe_response_variance(self):
        report = _make_report_with_failed_metrics(
            ("probe_response_variance", 5.0),
        )
        hints = suggest_countermeasures(report)
        assert len(hints) == 1
        h = hints[0]
        assert h.metric_name == "probe_response_variance"
        assert any("timeout" in c.lower() or "silent" in c.lower() or "close" in c.lower()
                  for c in h.recommended_changes)

    def test_multiple_failed_metrics(self):
        report = _make_report_with_failed_metrics(
            ("repeated_length_ratio", 0.85),
            ("small_packet_ratio", 0.75),
            ("ngram_entropy", 0.3),
        )
        hints = suggest_countermeasures(report)
        assert len(hints) == 3
        names = {h.metric_name for h in hints}
        assert names == {"repeated_length_ratio", "small_packet_ratio", "ngram_entropy"}

    def test_no_failed_metrics_returns_empty(self):
        report = DetectionReport(
            detector_name="fingerprint",
            passed=True,
            metrics=[],
        )
        hints = suggest_countermeasures(report)
        assert hints == []

    def test_each_hint_has_tradeoffs_and_avoid(self):
        """Every metric-specific hint must have non-empty tradeoffs and avoid."""
        report = _make_report_with_failed_metrics(
            ("repeated_length_ratio", 0.85),
            ("small_packet_ratio", 0.75),
            ("dominant_ngram_ratio", 0.65),
            ("ngram_entropy", 0.3),
            ("max_burst_size", 10000),
            ("dominant_burst_direction_ratio", 0.95),
            ("avg_inter_arrival_ms", 50),
            ("rtt_diff_ms", 30),
            ("probe_response_variance", 5),
        )
        hints = suggest_countermeasures(report)
        for h in hints:
            assert len(h.tradeoffs) > 0, f"{h.metric_name}: tradeoffs empty"
            assert len(h.avoid) > 0, f"{h.metric_name}: avoid empty"


# ---------------------------------------------------------------------------
# get_hint_for_metric
# ---------------------------------------------------------------------------

class TestGetHintForMetric:
    def test_known_metric(self):
        h = get_hint_for_metric("repeated_length_ratio")
        assert h is not None
        assert h.metric_name == "repeated_length_ratio"

    def test_unknown_metric(self):
        h = get_hint_for_metric("nonexistent_metric")
        assert h is None
