"""Tests for generate_integrated_evaluation_summary.py.

All tests use the module's functions directly (no subprocess).
Missing inputs must not crash — only mark sections as missing.
"""

import json
from pathlib import Path

import pytest

from scripts.generate_integrated_evaluation_summary import (
    _collect_fingerprint_summary,
    _collect_probe_summary,
    _collect_rtt_summary,
    _collect_shaping_summary,
    generate_summary,
    render_markdown,
)


# ---------------------------------------------------------------------------
# Missing inputs must not crash
# ---------------------------------------------------------------------------


class TestMissingInputs:
    def test_fingerprint_missing_returns_missing_status(self):
        result = _collect_fingerprint_summary(Path("/nonexistent/summary.json"))
        assert result["status"] == "missing"

    def test_probe_missing_returns_missing_status(self):
        result = _collect_probe_summary(Path("/nonexistent/probe.json"))
        assert result["status"] == "missing"

    def test_rtt_missing_returns_missing_status(self):
        result = _collect_rtt_summary(Path("/nonexistent/rtt.json"))
        assert result["status"] == "missing"

    def test_shaping_missing_returns_missing_status(self):
        result = _collect_shaping_summary(Path("/nonexistent/shaping.json"))
        assert result["status"] == "missing"

    def test_generate_summary_all_missing_does_not_crash(self):
        summary = generate_summary(
            fingerprint_path="/nonexistent/summary.json",
            probe_path="/nonexistent/probe.json",
            rtt_path="/nonexistent/rtt.json",
            shaping_path="/nonexistent/shaping.json",
        )
        assert "gates" in summary
        assert "fingerprint" in summary
        assert "active_probe" in summary
        assert "cross_layer_rtt" in summary
        assert "traffic_shaping" in summary
        for gate_name, gs in summary["gates"].items():
            assert gs["available"] is False


# ---------------------------------------------------------------------------
# Fingerprint summary
# ---------------------------------------------------------------------------


class TestFingerprintSummary:
    def test_reads_real_summary(self):
        fp = _collect_fingerprint_summary(Path("traces/summary.json"))
        assert fp["status"] == "ok"
        assert fp["total_entries"] > 0
        assert "risk_levels" in fp
        assert "transport_scenario_pairs" in fp

    def test_real_entries_have_expected_fields(self):
        fp = _collect_fingerprint_summary(Path("traces/summary.json"))
        for e in fp["transport_scenario_pairs"]:
            assert "transport" in e
            assert "scenario" in e
            assert "risk_level" in e


# ---------------------------------------------------------------------------
# Probe summary
# ---------------------------------------------------------------------------


class TestProbeSummary:
    def test_mock_probe_report_readable(self):
        # Generate a mock probe report first
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            tmp_path = tf.name
        try:
            subprocess.run(
                [
                    "python3", "-m", "src.evaluation.probe.report",
                    "--mock", "--output-json", tmp_path,
                ],
                capture_output=True, timeout=30,
            )
            result = _collect_probe_summary(Path(tmp_path))
            if result["status"] == "ok":
                assert result["total_scenarios"] > 0
        finally:
            Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# RTT summary
# ---------------------------------------------------------------------------


class TestRTTSummary:
    def test_rtt_mock_report_readable(self):
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            tmp_path = tf.name
        try:
            subprocess.run(
                [
                    "python3", "-m", "src.evaluation.rtt.report",
                    "--mock-profile", "proxy_like",
                    "--output-json", tmp_path,
                ],
                capture_output=True, timeout=30,
            )
            result = _collect_rtt_summary(Path(tmp_path))
            assert result["status"] == "ok"
            assert result["app_transport_diff_ms"] is not None
            assert result["risk_level"] is not None
        finally:
            Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Shaping summary
# ---------------------------------------------------------------------------

def _make_shaping_json(path):
    """Write a minimal synthetic_comparison.json for testing."""
    data = {
        "generated_by": "test_fixture",
        "seed": 42,
        "before": {"fingerprint_risk_score": 0.60},
        "after_padding": {"fingerprint_risk_score": 0.48},
        "after_padding_aggregation": {"fingerprint_risk_score": 0.35},
        "deltas": {"test_delta": "value"},
    }
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    return path


class TestShapingSummary:
    def test_reads_synthetic_comparison(self, tmp_path):
        shaping_path = _make_shaping_json(tmp_path / "synthetic_comparison.json")
        shaping = _collect_shaping_summary(Path(shaping_path))
        assert shaping["status"] == "ok"
        assert shaping["before_risk"] is not None
        assert "deltas" in shaping

    def test_missing_path_returns_missing(self):
        result = _collect_shaping_summary(Path("/nonexistent/shaping.json"))
        assert result["status"] == "missing"


# ---------------------------------------------------------------------------
# generate_summary integration
# ---------------------------------------------------------------------------


class TestGenerateSummary:
    def test_produces_all_gate_sections(self, tmp_path):
        shaping_path = _make_shaping_json(tmp_path / "synthetic_comparison.json")
        summary = generate_summary(
            fingerprint_path="traces/summary.json",
            shaping_path=str(shaping_path),
        )
        assert set(summary["gates"].keys()) == {
            "fingerprint", "active_probe", "cross_layer_rtt", "traffic_shaping",
        }
        assert summary["fingerprint"]["status"] == "ok"
        assert summary["traffic_shaping"]["status"] == "ok"

    def test_json_structure(self, tmp_path):
        shaping_path = _make_shaping_json(tmp_path / "synthetic_comparison.json")
        summary = generate_summary(
            fingerprint_path="traces/summary.json",
            shaping_path=str(shaping_path),
        )
        assert "generated_at" in summary
        assert "generated_by" in summary
        assert "test_baseline" in summary
        # Verify can round-trip through JSON
        encoded = json.dumps(summary, default=str)
        decoded = json.loads(encoded)
        assert decoded["gates"]["fingerprint"]["available"] is True


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------


class TestMarkdownOutput:
    def test_markdown_contains_all_four_gates(self, tmp_path):
        shaping_path = _make_shaping_json(tmp_path / "synthetic_comparison.json")
        summary = generate_summary(
            fingerprint_path="traces/summary.json",
            shaping_path=str(shaping_path),
        )
        md = render_markdown(summary)
        assert "Fingerprint Gate" in md
        assert "Active Probe Gate" in md
        assert "Cross-Layer RTT Gate" in md
        assert "Traffic Shaping" in md
        assert "Current Limitations" in md

    def test_markdown_with_all_missing(self):
        summary = generate_summary(
            fingerprint_path="/nonexistent/summary.json",
            probe_path="/nonexistent/probe.json",
            rtt_path="/nonexistent/rtt.json",
            shaping_path="/nonexistent/shaping.json",
        )
        md = render_markdown(summary)
        assert "Fingerprint Gate" in md
        assert "missing" in md.lower() or "Missing" in md

    def test_markdown_renders_risk_table(self, tmp_path):
        shaping_path = _make_shaping_json(tmp_path / "synthetic_comparison.json")
        summary = generate_summary(
            fingerprint_path="traces/summary.json",
            shaping_path=str(shaping_path),
        )
        md = render_markdown(summary)
        assert "| Transport | Scenario |" in md
