"""Tests for scripts.summarize_fingerprint_reports — fingerprint report summarizer."""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.summarize_fingerprint_reports import (
    _extract_row,
    _load_report,
    main as summarize_main,
    scan_reports,
    write_summary_csv,
    write_summary_json,
)


def _run_main(*args: str) -> None:
    try:
        summarize_main(list(args))
    except SystemExit:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report(**overrides: Any) -> dict[str, Any]:
    """Build a minimal valid report dict with optional overrides."""
    base: dict[str, Any] = {
        "trace_path": "/tmp/test.csv",
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
        "notes": ["test note"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. _load_report
# ---------------------------------------------------------------------------


class TestLoadReport:
    def test_loads_valid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "report.json"
        path.write_text(json.dumps({"packet_count": 5}))
        result = _load_report(path)
        assert result is not None
        assert result["packet_count"] == 5

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        result = _load_report(tmp_path / "nonexistent.json")
        assert result is None

    def test_returns_none_for_invalid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not json")
        result = _load_report(path)
        assert result is None


# ---------------------------------------------------------------------------
# 2. _extract_row
# ---------------------------------------------------------------------------


class TestExtractRow:
    def test_extracts_all_fields(self) -> None:
        report = _make_report()
        row = _extract_row("tcp", "idle", "real", report)
        assert row["transport"] == "tcp"
        assert row["scenario"] == "idle"
        assert row["trace_type"] == "real"
        assert row["packet_count"] == 100
        assert row["risk_level"] == "medium"
        assert row["fingerprint_risk_score"] == 0.55
        assert row["ngram_entropy"] == 1.5
        assert row["burst_count"] == 8
        assert row["max_burst_size"] == 1400
        assert row["dominant_burst_direction_ratio"] == 0.7
        assert row["avg_inter_arrival_ms"] == 12.5
        assert "test note" in row["notes"]

    def test_missing_report_produces_skipped_row(self) -> None:
        row = _extract_row("ssh", "bulk", "skipped", None)
        assert row["transport"] == "ssh"
        assert row["scenario"] == "bulk"
        assert row["trace_type"] == "skipped"
        assert row["risk_level"] == "skipped"
        assert row["packet_count"] == 0
        assert row["notes"] == "report not found"

    def test_notes_joined(self) -> None:
        report = _make_report(notes=["note1", "note2"])
        row = _extract_row("tcp", "idle", "real", report)
        assert row["notes"] == "note1; note2"

    def test_notes_not_a_list(self) -> None:
        report = _make_report(notes="single note")
        row = _extract_row("tcp", "idle", "real", report)
        assert row["notes"] == "single note"


# ---------------------------------------------------------------------------
# 3. scan_reports
# ---------------------------------------------------------------------------


class TestScanReports:
    def test_scans_existing_reports(self, tmp_path: Path) -> None:
        tcp_dir = tmp_path / "tcp"
        tcp_dir.mkdir(parents=True)
        (tcp_dir / "idle.report.json").write_text(
            json.dumps(_make_report(packet_count=42))
        )
        # Missing report for ping
        rows = scan_reports(
            str(tmp_path),
            transports=["tcp"],
            scenarios=["idle", "ping"],
        )
        assert len(rows) == 2

        idle_row = [r for r in rows if r["scenario"] == "idle"][0]
        assert idle_row["packet_count"] == 42
        assert idle_row["trace_type"] == "synthetic"  # no .csv present

        ping_row = [r for r in rows if r["scenario"] == "ping"][0]
        assert ping_row["trace_type"] == "skipped"
        assert ping_row["risk_level"] == "skipped"

    def test_trace_type_real_when_csv_present(self, tmp_path: Path) -> None:
        tcp_dir = tmp_path / "tcp"
        tcp_dir.mkdir(parents=True)
        (tcp_dir / "idle.report.json").write_text(
            json.dumps(_make_report())
        )
        (tcp_dir / "idle.csv").write_text("timestamp,src,dst,src_port,dst_port,proto,length,direction\n")
        rows = scan_reports(
            str(tmp_path),
            transports=["tcp"],
            scenarios=["idle"],
        )
        assert rows[0]["trace_type"] == "real"

    def test_default_transports_and_scenarios(self, tmp_path: Path) -> None:
        # Should scan tcp,tls,websocket,ssh × idle,ping,curl,bulk,reconnect
        rows = scan_reports(str(tmp_path))
        assert len(rows) == 20  # 4 transports × 5 scenarios
        assert all(r["trace_type"] == "skipped" for r in rows)

    def test_filter_transports(self, tmp_path: Path) -> None:
        rows = scan_reports(
            str(tmp_path),
            transports=["tcp", "tls"],
            scenarios=["idle"],
        )
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# 4. write_summary_json
# ---------------------------------------------------------------------------


class TestWriteSummaryJson:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        rows = [_extract_row("tcp", "idle", "real", _make_report())]
        output = tmp_path / "summary.json"
        write_summary_json(rows, output)
        data = json.loads(output.read_text())
        assert data["entry_count"] == 1
        assert data["entries"][0]["transport"] == "tcp"

    def test_generated_by_field(self, tmp_path: Path) -> None:
        rows: list[dict[str, Any]] = []
        output = tmp_path / "summary.json"
        write_summary_json(rows, output)
        data = json.loads(output.read_text())
        assert "summarize_fingerprint_reports.py" in data["generated_by"]


# ---------------------------------------------------------------------------
# 5. write_summary_csv
# ---------------------------------------------------------------------------


class TestWriteSummaryCsv:
    def test_writes_header_and_rows(self, tmp_path: Path) -> None:
        rows = [
            _extract_row("tcp", "idle", "real", _make_report()),
            _extract_row("tls", "idle", "skipped", None),
        ]
        output = tmp_path / "summary.csv"
        write_summary_csv(rows, output)
        text = output.read_text()
        assert "transport,scenario,trace_type" in text
        assert "tcp,idle,real" in text
        assert "tls,idle,skipped" in text

    def test_csv_has_all_required_columns(self, tmp_path: Path) -> None:
        rows = [_extract_row("tcp", "idle", "real", _make_report())]
        output = tmp_path / "summary.csv"
        write_summary_csv(rows, output)
        reader = csv.DictReader(io.StringIO(output.read_text()))
        fieldnames = reader.fieldnames or []
        required = {
            "transport", "scenario", "trace_type", "packet_count",
            "risk_level", "fingerprint_risk_score", "small_packet_ratio",
            "repeated_length_ratio", "ngram_entropy", "dominant_ngram_ratio",
            "burst_count", "max_burst_size", "dominant_burst_direction_ratio",
            "avg_inter_arrival_ms", "notes",
        }
        assert required.issubset(set(fieldnames))


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_cli_with_json_output(self, tmp_path: Path) -> None:
        tcp_dir = tmp_path / "tcp"
        tcp_dir.mkdir(parents=True)
        (tcp_dir / "idle.report.json").write_text(
            json.dumps(_make_report(packet_count=99))
        )
        json_out = tmp_path / "out.json"
        _run_main(
            "--input-dir", str(tmp_path),
            "--output-json", str(json_out),
            "--transports", "tcp",
            "--scenarios", "idle",
        )
        data = json.loads(json_out.read_text())
        assert data["entries"][0]["packet_count"] == 99

    def test_cli_with_csv_output(self, tmp_path: Path) -> None:
        tcp_dir = tmp_path / "tcp"
        tcp_dir.mkdir(parents=True)
        (tcp_dir / "idle.report.json").write_text(
            json.dumps(_make_report(risk_level="high"))
        )
        csv_out = tmp_path / "out.csv"
        _run_main(
            "--input-dir", str(tmp_path),
            "--output-csv", str(csv_out),
            "--transports", "tcp",
            "--scenarios", "idle",
        )
        text = csv_out.read_text()
        assert "high" in text

    def test_cli_handles_missing_dir(self, capsys: pytest.CaptureFixture[str]) -> None:
        _run_main(
            "--input-dir", "/tmp/nonexistent_traces_xyz",
            "--transports", "tcp",
            "--scenarios", "idle",
        )
        out = capsys.readouterr().out
        # Should still output (skipped entries)
        assert "skipped" in out.lower() or "report not found" in out.lower()
