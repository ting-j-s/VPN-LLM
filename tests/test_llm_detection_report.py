"""Tests for src.llm.detection.detector_report — DetectionReport and factory functions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.detection.detector_report import (
    DetectionMetric,
    DetectionReport,
    from_fingerprint_report,
    infer_transport_scenario_from_path,
    load_detection_report,
    detection_report_to_dict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fingerprint_report(**overrides):
    base = {
        "packet_count": 100,
        "risk_level": "medium",
        "fingerprint_risk_score": 0.55,
        "small_packet_ratio": 0.3,
        "repeated_length_ratio": 0.2,
        "ngram_entropy": 1.5,
        "dominant_ngram_ratio": 0.25,
        "burst_count": 8,
        "max_burst_size": 1400,
        "dominant_burst_direction_ratio": 0.7,
        "avg_inter_arrival_ms": 12.5,
        "notes": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# DetectionMetric
# ---------------------------------------------------------------------------

class TestDetectionMetric:
    def test_basic_fields(self):
        m = DetectionMetric(name="risk_score", value=0.65, threshold=0.70, passed=True)
        assert m.name == "risk_score"
        assert m.value == 0.65
        assert m.threshold == 0.70
        assert m.passed is True
        assert m.severity == "info"

    def test_to_dict(self):
        m = DetectionMetric(name="test", value=1.0, threshold=0.5, passed=False,
                           severity="fail", explanation="too high")
        d = m.to_dict()
        assert d["name"] == "test"
        assert d["value"] == 1.0
        assert d["passed"] is False
        assert d["severity"] == "fail"


# ---------------------------------------------------------------------------
# DetectionReport
# ---------------------------------------------------------------------------

class TestDetectionReport:
    def test_default_values(self):
        r = DetectionReport()
        assert r.detector_name == "unknown"
        assert r.passed is True
        assert r.metrics == []

    def test_to_dict_json_serializable(self):
        r = DetectionReport(
            detector_name="fingerprint",
            source_path="/tmp/test.json",
            transport="tcp",
            scenario="idle",
            metrics=[DetectionMetric(name="m1", value=0.5)],
        )
        d = r.to_dict()
        assert json.dumps(d)  # must not raise


# ---------------------------------------------------------------------------
# infer_transport_scenario_from_path
# ---------------------------------------------------------------------------

class TestInferTransportScenario:
    def test_from_traces_path(self):
        t, s = infer_transport_scenario_from_path("traces/tcp/idle.report.json")
        assert t == "tcp"
        assert s == "idle"

    def test_from_tls_path(self):
        t, s = infer_transport_scenario_from_path("traces/tls/curl.report.json")
        assert t == "tls"
        assert s == "curl"

    def test_from_websocket_path(self):
        t, s = infer_transport_scenario_from_path("traces/websocket/bulk.report.json")
        assert t == "websocket"
        assert s == "bulk"

    def test_nonstandard_path_returns_none(self):
        t, s = infer_transport_scenario_from_path("/tmp/random.json")
        assert t is None
        assert s is None

    def test_from_ssh_ping(self):
        t, s = infer_transport_scenario_from_path("traces/ssh/ping.report.json")
        assert t == "ssh"
        assert s == "ping"


# ---------------------------------------------------------------------------
# from_fingerprint_report
# ---------------------------------------------------------------------------

class TestFromFingerprintReport:
    def test_full_report(self):
        report = _make_fingerprint_report()
        dr = from_fingerprint_report(report, source_path="traces/tcp/idle.report.json")
        assert dr.detector_name == "fingerprint"
        assert dr.transport == "tcp"
        assert dr.scenario == "idle"
        assert dr.risk_score == 0.55
        assert dr.risk_level == "medium"
        assert len(dr.metrics) >= 8

    def test_missing_fields_enter_notes(self):
        report = _make_fingerprint_report(fingerprint_risk_score="", risk_level="")
        dr = from_fingerprint_report(report)
        assert any("missing field: fingerprint_risk_score" in n for n in dr.notes)
        assert any("missing field: risk_level" in n for n in dr.notes)

    def test_insufficient_data_is_warning(self):
        report = _make_fingerprint_report(
            packet_count=3,
            risk_level="insufficient_data",
            fingerprint_risk_score=0.0,
        )
        dr = from_fingerprint_report(report)
        assert dr.risk_level == "insufficient_data"
        assert dr.passed is False  # warning means not passed

    def test_skipped_risk_level_becomes_insufficient_data(self):
        report = _make_fingerprint_report(
            packet_count=0,
            risk_level="skipped",
        )
        dr = from_fingerprint_report(report)
        assert dr.risk_level == "insufficient_data"

    def test_report_with_notes_list(self):
        report = _make_fingerprint_report(notes=["note1", "note2"])
        dr = from_fingerprint_report(report)
        assert "note1" in dr.notes
        assert "note2" in dr.notes

    def test_report_with_notes_string(self):
        report = _make_fingerprint_report(notes="single note")
        dr = from_fingerprint_report(report)
        assert "single note" in dr.notes


# ---------------------------------------------------------------------------
# load_detection_report
# ---------------------------------------------------------------------------

class TestLoadDetectionReport:
    def test_loads_valid_report(self, tmp_path: Path):
        path = tmp_path / "idle.report.json"
        path.write_text(json.dumps(_make_fingerprint_report(packet_count=42)))
        dr = load_detection_report(path)
        assert dr.source_path == str(path)
        m = [m for m in dr.metrics if m.name == "packet_count"][0]
        assert m.value == 42

    def test_missing_file(self, tmp_path: Path):
        dr = load_detection_report(tmp_path / "nonexistent.report.json")
        assert dr.passed is False
        assert any("not found" in n for n in dr.notes)
        assert dr.trace_type == "skipped"

    def test_invalid_json(self, tmp_path: Path):
        path = tmp_path / "bad.report.json"
        path.write_text("not json")
        dr = load_detection_report(path)
        assert dr.passed is False
        assert any("failed to read" in n for n in dr.notes)


# ---------------------------------------------------------------------------
# detection_report_to_dict
# ---------------------------------------------------------------------------

class TestDetectionReportToDict:
    def test_serializable(self):
        dr = DetectionReport(
            detector_name="fingerprint",
            transport="tcp",
            scenario="idle",
            metrics=[DetectionMetric(name="m1", value=0.5, threshold=0.7,
                                     passed=True, severity="info", explanation="ok")],
            notes=["note1"],
            raw={"key": "value"},
        )
        d = detection_report_to_dict(dr)
        assert d["detector_name"] == "fingerprint"
        assert d["transport"] == "tcp"
        assert len(d["metrics"]) == 1
        assert d["metrics"][0]["name"] == "m1"
