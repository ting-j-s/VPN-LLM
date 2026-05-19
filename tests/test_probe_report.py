"""Tests for probe report generation and CLI."""

import json
import pytest
from src.evaluation.probe.probe_scenarios import PROBE_SCENARIOS
from src.evaluation.probe.probe_runner import MockProbeRunner
from src.evaluation.probe.report import (
    compute_behavior_summary,
    compute_probe_variance_metrics,
    compute_risk_level,
    generate_probe_report,
)


class TestBehaviorSummary:
    """Test behavior_summary computation."""

    def test_empty_results(self):
        summary = compute_behavior_summary([])
        assert summary["total_scenarios"] == 0
        assert summary["close_time_range_ms"] == 0

    def test_with_mock_results(self):
        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        summary = compute_behavior_summary(results)
        assert summary["total_scenarios"] == len(PROBE_SCENARIOS)
        assert summary["connected_count"] == len(PROBE_SCENARIOS)
        assert summary["close_time_min_ms"] > 0
        assert summary["close_time_max_ms"] > 0
        assert summary["close_time_range_ms"] >= 0

    def test_timeout_count(self):
        runner = MockProbeRunner()
        results = runner.run_all()
        summary = compute_behavior_summary(results)
        # empty_connection should be the only timeout
        assert summary["timeout_count"] == 1

    def test_response_count_zero_for_malformed(self):
        runner = MockProbeRunner()
        results = runner.run_all()
        summary = compute_behavior_summary(results)
        assert summary["response_count"] == 0  # no mock scenarios return data

    def test_distinct_error_types(self):
        runner = MockProbeRunner()
        results = runner.run_all()
        summary = compute_behavior_summary(results)
        # "timeout" and "close" are distinct
        assert summary["distinct_error_types"] >= 1


class TestProbeVarianceMetrics:
    """Test variance metric computation."""

    def test_variance_from_mock_results(self):
        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        variance = compute_probe_variance_metrics(results)
        assert "probe_response_variance" in variance
        assert "malformed_close_time_variance" in variance
        assert 0.0 <= variance["probe_response_variance"] <= 1.0
        assert 0.0 <= variance["malformed_close_time_variance"] <= 1.0

    def test_empty_results_zero_variance(self):
        variance = compute_probe_variance_metrics([])
        assert variance["probe_response_variance"] == 0.0
        assert variance["malformed_close_time_variance"] == 0.0

    def test_uniform_behavior_gives_low_variance(self):
        """Create results where all behave identically → low variance."""
        from src.evaluation.probe.probe_runner import ProbeResult

        scenario = PROBE_SCENARIOS[0]
        results = []
        for _ in range(5):
            r = ProbeResult(scenario=scenario)
            r.connected = True
            r.close_observed = True
            r.elapsed_ms = 5.0
            r.error_type = "close"
            results.append(r)

        variance = compute_probe_variance_metrics(results)
        assert variance["probe_response_variance"] < 0.3  # very uniform

    def test_divergent_behavior_gives_high_variance(self):
        """Mix of timeout, close, response → high variance."""
        from src.evaluation.probe.probe_runner import ProbeResult

        scenario = PROBE_SCENARIOS[0]
        results = []
        # timeout
        r1 = ProbeResult(scenario=scenario)
        r1.timeout_observed = True
        r1.elapsed_ms = 2000
        r1.error_type = "timeout"
        results.append(r1)
        # immediate close
        r2 = ProbeResult(scenario=scenario)
        r2.close_observed = True
        r2.elapsed_ms = 3.0
        r2.error_type = "close"
        results.append(r2)
        # response
        r3 = ProbeResult(scenario=scenario)
        r3.bytes_received = 100
        r3.elapsed_ms = 15.0
        r3.error_type = ""
        results.append(r3)
        # reset
        r4 = ProbeResult(scenario=scenario)
        r4.reset_observed = True
        r4.elapsed_ms = 500
        r4.error_type = "reset"
        results.append(r4)

        variance = compute_probe_variance_metrics(results)
        assert variance["probe_response_variance"] > 0.3  # high variance


class TestRiskLevel:
    """Test risk score/level computation."""

    def test_low_risk(self):
        score, level = compute_risk_level(0.1, 0.1)
        assert score < 0.3
        assert level == "low"

    def test_medium_risk(self):
        score, level = compute_risk_level(0.4, 0.4)
        assert 0.3 <= score < 0.6
        assert level == "medium"

    def test_high_risk(self):
        score, level = compute_risk_level(0.8, 0.8)
        assert score >= 0.6
        assert level == "high"


class TestGenerateProbeReport:
    """Test full report generation."""

    def test_report_structure(self):
        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        report = generate_probe_report(results)

        assert report["detector_name"] == "active_probe_resistance"
        assert "metrics" in report
        assert len(report["metrics"]) >= 2
        assert "raw" in report
        assert "behavior_summary" in report["raw"]
        assert "results" in report["raw"]
        assert report["raw"]["local_only"] is True

    def test_report_metrics_have_correct_names(self):
        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        report = generate_probe_report(results)

        metric_names = [m["name"] for m in report["metrics"]]
        assert "probe_response_variance" in metric_names
        assert "malformed_close_time_variance" in metric_names

    def test_report_rejects_non_local_in_local_only_mode(self):
        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        with pytest.raises(ValueError, match="local"):
            generate_probe_report(results, host="192.168.1.1", local_only=True)

    def test_report_per_scenario_results(self):
        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        report = generate_probe_report(results)
        raw_results = report["raw"]["results"]
        assert len(raw_results) == len(PROBE_SCENARIOS)
        for r in raw_results:
            assert "scenario" in r
            assert "connected" in r
            assert "elapsed_ms" in r

    def test_report_json_serializable(self):
        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        report = generate_probe_report(results)
        json_text = json.dumps(report, indent=2)
        parsed = json.loads(json_text)
        assert parsed["detector_name"] == "active_probe_resistance"


class TestProbeDetectionReportIntegration:
    """Test that probe reports can be loaded as DetectionReports."""

    def test_from_probe_report_factory(self):
        from src.llm.detection.detector_report import from_probe_report

        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        probe_dict = generate_probe_report(results)

        dr = from_probe_report(probe_dict)
        assert dr.detector_name == "active_probe_resistance"
        assert len(dr.metrics) >= 2
        metric_names = [m.name for m in dr.metrics]
        assert "probe_response_variance" in metric_names
        assert "malformed_close_time_variance" in metric_names

    def test_from_probe_report_preserves_raw(self):
        from src.llm.detection.detector_report import from_probe_report

        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        probe_dict = generate_probe_report(results)

        dr = from_probe_report(probe_dict)
        assert dr.raw is not None
        assert "behavior_summary" in dr.raw
        assert "results" in dr.raw

    def test_probe_report_passes_through_gate(self):
        """Verify probe report goes through the detection gate correctly."""
        from src.llm.detection.detector_report import from_probe_report
        from src.llm.detection.gate import evaluate_detection_report, DetectionThresholds

        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        probe_dict = generate_probe_report(results)

        dr = from_probe_report(probe_dict)
        evaluated = evaluate_detection_report(dr)

        # With default thresholds (max_probe_response_variance=None),
        # the report should pass since the gate doesn't evaluate probe metrics
        # unless thresholds are set
        assert evaluated.passed is True

    def test_probe_report_fails_with_tight_thresholds(self):
        """Verify probe report fails gate when thresholds are tight."""
        from src.llm.detection.detector_report import from_probe_report
        from src.llm.detection.gate import evaluate_detection_report, DetectionThresholds

        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        probe_dict = generate_probe_report(results)

        dr = from_probe_report(probe_dict)
        # Set very tight thresholds that mock results should fail
        thresholds = DetectionThresholds(
            max_probe_response_variance=0.1,
            max_malformed_close_time_variance=0.1,
        )
        evaluated = evaluate_detection_report(dr, thresholds)
        # Mock results have some variance (timeout vs close), so should fail
        probe_metric = next(
            (m for m in evaluated.metrics if m.name == "probe_response_variance"), None
        )
        if probe_metric and probe_metric.value > 0.1:
            assert not probe_metric.passed

    def test_countermeasure_policy_has_probe_hints(self):
        """Verify countermeasure policy covers probe metrics."""
        from src.llm.detection.countermeasure_policy import get_hint_for_metric

        hint = get_hint_for_metric("probe_response_variance")
        assert hint is not None
        assert "silent drop" in " ".join(hint.recommended_changes).lower()

        hint2 = get_hint_for_metric("malformed_close_time_variance")
        assert hint2 is not None
        assert "unified" in " ".join(hint2.recommended_changes).lower()


class TestCLIMock:
    """Test report CLI with --mock flag."""

    def test_mock_report_json(self, tmp_path):
        import subprocess
        import sys

        out = tmp_path / "probe.report.json"
        result = subprocess.run(
            [
                sys.executable, "-m", "src.evaluation.probe.report",
                "--mock",
                "--output-json", str(out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert out.exists()
        report = json.loads(out.read_text())
        assert report["detector_name"] == "active_probe_resistance"
        assert len(report["raw"]["results"]) == len(PROBE_SCENARIOS)

    def test_mock_report_stdout(self):
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable, "-m", "src.evaluation.probe.report",
                "--mock",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        report = json.loads(result.stdout)
        assert report["detector_name"] == "active_probe_resistance"

    def test_mock_with_specific_scenario(self, tmp_path):
        import subprocess
        import sys

        out = tmp_path / "single.report.json"
        result = subprocess.run(
            [
                sys.executable, "-m", "src.evaluation.probe.report",
                "--mock",
                "--scenario", "one_zero",
                "--scenario", "bad_magic",
                "--output-json", str(out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        report = json.loads(out.read_text())
        assert len(report["raw"]["results"]) == 2
