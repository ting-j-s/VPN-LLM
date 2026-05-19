"""Tests for src.llm.detection.gate — DetectionThresholds and gate evaluation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.detection.detector_report import (
    DetectionMetric,
    DetectionReport,
    from_fingerprint_report,
)
from src.llm.detection.gate import (
    DetectionThresholds,
    evaluate_detection_report,
    evaluate_fingerprint_json,
    evaluate_report_file,
    evaluate_report_files,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_metric(name, value, passed=True, severity="info"):
    return DetectionMetric(name=name, value=value, passed=passed, severity=severity)


def _make_report(metrics=None, risk_score=0.4, risk_level="low", packet_count=100, **kwargs):
    if metrics is None:
        metrics = [
            _make_metric("packet_count", packet_count),
            _make_metric("repeated_length_ratio", 0.2),
            _make_metric("small_packet_ratio", 0.1),
            _make_metric("ngram_entropy", 2.5),
            _make_metric("dominant_ngram_ratio", 0.15),
            _make_metric("dominant_burst_direction_ratio", 0.5),
        ]
    return DetectionReport(
        detector_name="fingerprint",
        transport="tcp",
        scenario="idle",
        risk_score=risk_score,
        risk_level=risk_level,
        metrics=metrics,
        trace_type="real",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# DetectionThresholds
# ---------------------------------------------------------------------------

class TestDetectionThresholds:
    def test_default_values(self):
        t = DetectionThresholds()
        assert t.max_risk_score == 0.70
        assert t.max_repeated_length_ratio == 0.60
        assert t.min_packet_count == 5
        assert t.fail_on_insufficient_data is False

    def test_to_dict(self):
        t = DetectionThresholds(max_risk_score=0.50)
        d = t.to_dict()
        assert d["max_risk_score"] == 0.50


# ---------------------------------------------------------------------------
# evaluate_detection_report
# ---------------------------------------------------------------------------

class TestEvaluateDetectionReport:
    def test_low_risk_pass(self):
        report = _make_report(risk_score=0.3)
        result = evaluate_detection_report(report)
        assert result.passed is True

    def test_high_risk_score_fail(self):
        report = _make_report(risk_score=0.85)
        result = evaluate_detection_report(report, DetectionThresholds(max_risk_score=0.70))
        assert result.passed is False
        failed = [m for m in result.metrics if not m.passed and m.severity == "fail"]
        assert any("fingerprint_risk_score" in m.name for m in failed)

    def test_repeated_length_ratio_fail(self):
        report = _make_report(metrics=[
            _make_metric("repeated_length_ratio", 0.85),
        ])
        result = evaluate_detection_report(report, DetectionThresholds(max_repeated_length_ratio=0.60))
        assert result.passed is False

    def test_small_packet_ratio_fail(self):
        report = _make_report(metrics=[
            _make_metric("small_packet_ratio", 0.75),
        ])
        result = evaluate_detection_report(report, DetectionThresholds(max_small_packet_ratio=0.60))
        assert result.passed is False

    def test_dominant_ngram_ratio_fail(self):
        report = _make_report(metrics=[
            _make_metric("dominant_ngram_ratio", 0.65),
        ])
        result = evaluate_detection_report(report, DetectionThresholds(max_dominant_ngram_ratio=0.50))
        assert result.passed is False

    def test_dominant_burst_direction_ratio_fail(self):
        report = _make_report(metrics=[
            _make_metric("dominant_burst_direction_ratio", 0.95),
        ])
        result = evaluate_detection_report(report, DetectionThresholds(max_dominant_burst_direction_ratio=0.80))
        assert result.passed is False

    def test_min_ngram_entropy_fail(self):
        report = _make_report(metrics=[
            _make_metric("ngram_entropy", 0.5),
        ])
        result = evaluate_detection_report(report, DetectionThresholds(min_ngram_entropy=1.0))
        assert result.passed is False

    def test_insufficient_data_warning(self):
        report = _make_report(
            metrics=[_make_metric("risk_level", "insufficient_data", passed=False, severity="warning")],
            risk_level="insufficient_data",
        )
        result = evaluate_detection_report(report, DetectionThresholds(fail_on_insufficient_data=False))
        assert result.passed is False  # still not a clean pass
        warnings = [m for m in result.metrics if m.severity == "warning"]
        assert len(warnings) >= 1

    def test_fail_on_insufficient_data_true(self):
        report = _make_report(
            metrics=[_make_metric("risk_level", "insufficient_data", passed=False, severity="warning")],
            risk_level="insufficient_data",
        )
        result = evaluate_detection_report(report, DetectionThresholds(fail_on_insufficient_data=True))
        fails = [m for m in result.metrics if m.severity == "fail"]
        assert len(fails) >= 1

    def test_missing_report_fail(self):
        report = DetectionReport(trace_type="skipped", passed=False)
        result = evaluate_detection_report(report)
        assert result.passed is False
        assert any("report_missing" in m.name for m in result.metrics)

    def test_max_burst_size_fail(self):
        report = _make_report(metrics=[
            _make_metric("max_burst_size", 10000),
        ])
        result = evaluate_detection_report(report, DetectionThresholds(max_max_burst_size=5000))
        assert result.passed is False

    def test_none_threshold_skips_check(self):
        report = _make_report(metrics=[
            _make_metric("repeated_length_ratio", 0.85),
        ])
        # max_repeated_length_ratio=None → skip check
        result = evaluate_detection_report(report, DetectionThresholds(max_repeated_length_ratio=None))
        assert result.passed is True

    def test_avg_inter_arrival_ms_deviation_fail(self):
        report = _make_report(metrics=[
            _make_metric("avg_inter_arrival_ms", 50.0),
        ])
        result = evaluate_detection_report(report, DetectionThresholds(max_avg_inter_arrival_ms_deviation=20.0))
        assert result.passed is False

    def test_rtt_diff_ms_fail(self):
        report = _make_report(metrics=[
            _make_metric("rtt_diff_ms", 30.0),
        ])
        result = evaluate_detection_report(report, DetectionThresholds(max_rtt_diff_ms=10.0))
        assert result.passed is False

    def test_probe_response_variance_fail(self):
        report = _make_report(metrics=[
            _make_metric("probe_response_variance", 5.0),
        ])
        result = evaluate_detection_report(report, DetectionThresholds(max_probe_response_variance=2.0))
        assert result.passed is False

    def test_malformed_close_time_variance_fail(self):
        report = _make_report(metrics=[
            _make_metric("malformed_close_time_variance", 3.0),
        ])
        result = evaluate_detection_report(report, DetectionThresholds(max_malformed_close_time_variance=1.0))
        assert result.passed is False


# ---------------------------------------------------------------------------
# evaluate_fingerprint_json
# ---------------------------------------------------------------------------

class TestEvaluateFingerprintJson:
    def test_passes_low_risk(self):
        data = {
            "packet_count": 100,
            "risk_level": "low",
            "fingerprint_risk_score": 0.3,
            "small_packet_ratio": 0.2,
            "repeated_length_ratio": 0.1,
            "ngram_entropy": 2.0,
            "dominant_ngram_ratio": 0.2,
        }
        result = evaluate_fingerprint_json(data)
        assert result.passed is True


# ---------------------------------------------------------------------------
# evaluate_report_file
# ---------------------------------------------------------------------------

class TestEvaluateReportFile:
    def test_missing_file_fails(self, tmp_path: Path):
        result = evaluate_report_file(tmp_path / "nonexistent.json")
        assert result.passed is False


# ---------------------------------------------------------------------------
# evaluate_report_files
# ---------------------------------------------------------------------------

class TestEvaluateReportFiles:
    def test_all_pass(self, tmp_path: Path):
        p1 = tmp_path / "a.report.json"
        p1.write_text('{"packet_count": 100, "risk_level": "low", "fingerprint_risk_score": 0.3}')
        p2 = tmp_path / "b.report.json"
        p2.write_text('{"packet_count": 200, "risk_level": "low", "fingerprint_risk_score": 0.2}')
        results = evaluate_report_files([str(p1), str(p2)])
        assert len(results) == 2
        assert all(r.passed for r in results)

    def test_mixed_pass_fail(self, tmp_path: Path):
        p1 = tmp_path / "pass.report.json"
        p1.write_text('{"packet_count": 100, "risk_level": "low", "fingerprint_risk_score": 0.3}')
        p2 = tmp_path / "fail.report.json"
        p2.write_text('{"packet_count": 100, "risk_level": "low", "fingerprint_risk_score": 0.95}')
        results = evaluate_report_files(
            [str(p1), str(p2)],
            DetectionThresholds(max_risk_score=0.70),
        )
        assert results[0].passed is True
        assert results[1].passed is False
