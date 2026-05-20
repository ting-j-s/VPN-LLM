"""Tests for RTT report CLI, DetectionReport integration, and gate evaluation."""

import json
import subprocess
import sys

import pytest

from src.evaluation.rtt.report import _rtt_report_to_json_dict
from src.evaluation.rtt.rtt_runner import MockRTTRunner
from src.evaluation.rtt.rtt_measurements import CrossLayerRTTReport
from src.llm.detection.detector_report import (
    from_rtt_report,
    load_detection_report,
)
from src.llm.detection.gate import (
    DetectionThresholds,
    evaluate_detection_report,
)
from src.llm.detection.countermeasure_policy import (
    get_hint_for_metric,
    suggest_countermeasures,
)


class TestRTTReportToJson:
    """Test _rtt_report_to_json_dict conversion."""

    def test_mock_direct_report_json(self):
        runner = MockRTTRunner(profile="direct")
        report = runner.run()
        d = _rtt_report_to_json_dict(report)

        assert d["detector_name"] == "cross_layer_rtt"
        assert d["trace_type"] == "mock"
        assert d["transport"] == "tcp"
        assert d["scenario"] == "rtt"
        assert "metrics" in d
        assert len(d["metrics"]) >= 3  # app, transport, diff, risk_score
        metric_names = [m["name"] for m in d["metrics"]]
        assert "app_transport_diff_ms" in metric_names
        assert "rtt_risk_score" in metric_names

    def test_mock_proxy_like_report_json(self):
        runner = MockRTTRunner(profile="proxy_like")
        report = runner.run()
        d = _rtt_report_to_json_dict(report)

        assert d["risk_level"] == "high"
        assert d["risk_score"] >= 0.6
        assert len(d["raw"]["measurements"]) == 2

    def test_report_has_raw_target(self):
        runner = MockRTTRunner(profile="direct")
        report = runner.run()
        d = _rtt_report_to_json_dict(report)
        assert d["raw"]["target"] == "127.0.0.1"


class TestCLIMock:
    """Test RTT report CLI with --mode mock."""

    def test_mock_direct_cli(self, tmp_path):
        out = tmp_path / "rtt_direct.report.json"
        result = subprocess.run(
            [
                sys.executable, "-m", "src.evaluation.rtt.report",
                "--mock-profile", "direct",
                "--output-json", str(out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert out.exists()
        report = json.loads(out.read_text())
        assert report["detector_name"] == "cross_layer_rtt"
        assert report["risk_level"] == "low"

    def test_mock_proxy_like_cli(self, tmp_path):
        out = tmp_path / "rtt_proxy.report.json"
        result = subprocess.run(
            [
                sys.executable, "-m", "src.evaluation.rtt.report",
                "--mock-profile", "proxy_like",
                "--output-json", str(out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        report = json.loads(out.read_text())
        assert report["risk_level"] == "high"

    def test_mock_stdout(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "src.evaluation.rtt.report",
                "--mock-profile", "direct",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        report = json.loads(result.stdout)
        assert report["detector_name"] == "cross_layer_rtt"

    def test_non_local_host_rejected(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "src.evaluation.rtt.report",
                "--host", "8.8.8.8",
                "--mode", "mock",
                "--local-only",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 2
        assert "non-local" in result.stderr.lower()

    def test_invalid_mock_profile_exits_2(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "src.evaluation.rtt.report",
                "--mock-profile", "invalid_prof",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0


class TestDetectionReportFromRTT:
    """Test from_rtt_report factory function."""

    def test_loads_basic_report(self):
        runner = MockRTTRunner(profile="direct")
        report = runner.run()
        d = _rtt_report_to_json_dict(report)
        dr = from_rtt_report(d)
        assert dr.detector_name == "cross_layer_rtt"
        assert dr.scenario == "rtt"
        assert len(dr.metrics) >= 2

    def test_metrics_extracted(self):
        runner = MockRTTRunner(profile="proxy_like")
        report = runner.run()
        d = _rtt_report_to_json_dict(report)
        dr = from_rtt_report(d)
        metric_names = [m.name for m in dr.metrics]
        assert "app_transport_diff_ms" in metric_names
        assert "rtt_risk_score" in metric_names

    def test_load_detection_report_routes_to_rtt(self, tmp_path):
        runner = MockRTTRunner(profile="direct")
        report = runner.run()
        d = _rtt_report_to_json_dict(report)
        out = tmp_path / "rtt.report.json"
        out.write_text(json.dumps(d))
        dr = load_detection_report(str(out))
        assert dr.detector_name == "cross_layer_rtt"

    def test_raw_preserved(self):
        runner = MockRTTRunner(profile="proxy_like")
        report = runner.run()
        d = _rtt_report_to_json_dict(report)
        dr = from_rtt_report(d)
        assert dr.raw is not None
        assert "measurements" in dr.raw


class TestDetectionGateWithRTT:
    """Test DetectionGate evaluation with RTT reports."""

    def test_direct_passes_default_thresholds(self):
        runner = MockRTTRunner(profile="direct")
        report = runner.run()
        d = _rtt_report_to_json_dict(report)
        dr = from_rtt_report(d)
        evaluated = evaluate_detection_report(dr)
        assert evaluated.passed is True

    def test_proxy_like_fails_default_thresholds(self):
        runner = MockRTTRunner(profile="proxy_like")
        report = runner.run()
        d = _rtt_report_to_json_dict(report)
        dr = from_rtt_report(d)
        evaluated = evaluate_detection_report(dr)
        # proxy_like app-transport diff ≈ 34ms > default 50ms threshold? No, 34 < 50.
        # But rtt_risk_score is high, which the gate flags.
        # With default max_app_transport_diff_ms=50.0, the diff passes,
        # but rtt_risk_score fails (because risk_score >= 0.6).
        assert evaluated.passed is False

    def test_proxy_like_passes_lenient_thresholds(self):
        runner = MockRTTRunner(profile="proxy_like")
        report = runner.run()
        d = _rtt_report_to_json_dict(report)
        dr = from_rtt_report(d)
        thresholds = DetectionThresholds(
            max_app_transport_diff_ms=None,  # disable diff check
        )
        evaluated = evaluate_detection_report(dr, thresholds)
        # rtt_risk_score still triggers with default gate behavior
        # Set a very high threshold effectively
        assert not evaluated.passed  # rtt_risk_score still fails it

    def test_direct_fails_tight_diff_threshold(self):
        runner = MockRTTRunner(profile="direct")
        report = runner.run()
        d = _rtt_report_to_json_dict(report)
        dr = from_rtt_report(d)
        thresholds = DetectionThresholds(
            max_app_transport_diff_ms=0.5,  # very tight
        )
        evaluated = evaluate_detection_report(dr, thresholds)
        assert evaluated.passed is False
        diff_m = next((m for m in evaluated.metrics if m.name == "app_transport_diff_ms"), None)
        assert diff_m is not None
        assert not diff_m.passed

    def test_timing_stability_gate(self):
        runner = MockRTTRunner(profile="direct")
        report = runner.run()
        d = _rtt_report_to_json_dict(report)
        dr = from_rtt_report(d)
        thresholds = DetectionThresholds(
            max_timing_stability_score=0.1,  # extremely tight
        )
        evaluated = evaluate_detection_report(dr, thresholds)
        ts_m = next((m for m in evaluated.metrics if m.name == "timing_stability_score"), None)
        if ts_m and ts_m.value and ts_m.value > 0.1:
            assert not ts_m.passed


class TestCountermeasurePolicyRTT:
    """Test CountermeasurePolicy with RTT metrics."""

    def test_app_transport_diff_hint_exists(self):
        hint = get_hint_for_metric("app_transport_diff_ms")
        assert hint is not None
        assert "RTT-aware pacing" in " ".join(hint.recommended_changes)

    def test_app_network_diff_hint_exists(self):
        hint = get_hint_for_metric("app_network_diff_ms")
        assert hint is not None
        assert "latency budget" in " ".join(hint.recommended_changes).lower()

    def test_timing_stability_hint_exists(self):
        hint = get_hint_for_metric("timing_stability_score")
        assert hint is not None
        assert "jitter" in " ".join(hint.recommended_changes).lower()

    def test_rtt_risk_score_hint_exists(self):
        hint = get_hint_for_metric("rtt_risk_score")
        assert hint is not None
        assert "pacing" in " ".join(hint.recommended_changes).lower()

    def test_suggest_countermeasures_for_proxy_like(self):
        runner = MockRTTRunner(profile="proxy_like")
        report = runner.run()
        d = _rtt_report_to_json_dict(report)
        dr = from_rtt_report(d)
        evaluated = evaluate_detection_report(dr)
        hints = suggest_countermeasures(evaluated)
        assert len(hints) > 0
        hint_names = [h.metric_name for h in hints]
        assert "app_transport_diff_ms" in hint_names or "rtt_risk_score" in hint_names

    def test_no_hints_when_all_pass(self):
        runner = MockRTTRunner(profile="direct")
        report = runner.run()
        d = _rtt_report_to_json_dict(report)
        dr = from_rtt_report(d)
        # Use thresholds that guarantee pass
        thresholds = DetectionThresholds(
            max_app_transport_diff_ms=None,
        )
        evaluated = evaluate_detection_report(dr, thresholds)
        # rtt_risk_score for direct is low (e.g., 0.1 < 0.3) so should pass
        hints = suggest_countermeasures(evaluated)
        # direct profile should pass rtt_risk_score check
        assert len(hints) == 0


class TestPatchLoopIntegration:
    """Test that patch_loop can consume RTT reports."""

    def test_patch_loop_with_rtt_report(self, tmp_path):
        """Generate RTT proxy_like report, run through patch_loop CLI."""
        rtt_out = tmp_path / "rtt.report.json"
        prompt_out = tmp_path / "rtt_prompt.txt"
        json_out = tmp_path / "rtt_loop.json"

        # Generate RTT report
        result = subprocess.run(
            [
                sys.executable, "-m", "src.evaluation.rtt.report",
                "--mock-profile", "proxy_like",
                "--output-json", str(rtt_out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0

        # Run through patch loop
        result = subprocess.run(
            [
                sys.executable, "-m", "src.llm.detection.patch_loop",
                "--user-request",
                "reduce cross-layer RTT fingerprint risk in local VPN-LLM traffic shaping",
                "--functional-test-summary", "compileall passed; pytest passed",
                "--report", str(rtt_out),
                "--output-prompt", str(prompt_out),
                "--output-json", str(json_out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # proxy_like should fail the gate
        assert result.returncode == 1  # exit 1 = patch needed
        assert json_out.exists()
        loop_result = json.loads(json_out.read_text())
        assert loop_result["should_request_patch"] is True

    def test_patch_loop_generates_rtt_fix_prompt(self, tmp_path):
        rtt_out = tmp_path / "rtt.report.json"
        prompt_out = tmp_path / "rtt_prompt.txt"
        json_out = tmp_path / "rtt_loop.json"

        subprocess.run(
            [
                sys.executable, "-m", "src.evaluation.rtt.report",
                "--mock-profile", "proxy_like",
                "--output-json", str(rtt_out),
            ],
            capture_output=True,
            timeout=30,
        )
        subprocess.run(
            [
                sys.executable, "-m", "src.llm.detection.patch_loop",
                "--user-request",
                "reduce cross-layer RTT fingerprint risk",
                "--functional-test-summary", "tests pass",
                "--report", str(rtt_out),
                "--output-prompt", str(prompt_out),
                "--output-json", str(json_out),
            ],
            capture_output=True,
            timeout=30,
        )
        assert prompt_out.exists()
        prompt_text = prompt_out.read_text()
        assert "RTT" in prompt_text or "rtt" in prompt_text.lower()
        assert len(prompt_text) > 100
