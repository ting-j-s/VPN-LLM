"""Tests for src.evaluation.fingerprint — offline fingerprintability evaluation."""

from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from src.evaluation.fingerprint.pcap_features import (
    FlowFeatures,
    FlowPacket,
    _compute_risk,
    extract_features,
    load_trace_csv,
    summarize_trace,
)
from src.evaluation.fingerprint.report import main as report_main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _csv_text(rows: list[dict[str, Any]]) -> str:
    """Turn a list of dicts into CSV text with header."""
    if not rows:
        return "timestamp,src,dst,src_port,dst_port,proto,length,direction\n"
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "timestamp",
            "src",
            "dst",
            "src_port",
            "dst_port",
            "proto",
            "length",
            "direction",
        ],
    )
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text(_csv_text(rows), encoding="utf-8")
    return path


def _packet(**kw: Any) -> dict[str, Any]:
    """Default packet factory with sensible defaults."""
    defaults: dict[str, Any] = {
        "timestamp": 0.0,
        "src": "10.0.0.1",
        "dst": "10.0.0.2",
        "src_port": 44321,
        "dst_port": 8080,
        "proto": "tcp",
        "length": 100,
        "direction": "C2S",
    }
    defaults.update(kw)
    return defaults


# ---------------------------------------------------------------------------
# FlowPacket
# ---------------------------------------------------------------------------


class TestFlowPacket:
    def test_valid_creation(self) -> None:
        p = FlowPacket(0.0, "a", "b", 1, 2, "tcp", 100, "C2S")
        assert p.length == 100
        assert p.direction == "C2S"

    def test_invalid_direction_raises(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            FlowPacket(0.0, "a", "b", 1, 2, "tcp", 100, "INVALID")

    def test_s2c_direction_valid(self) -> None:
        p = FlowPacket(0.0, "a", "b", 1, 2, "tcp", 100, "S2C")
        assert p.direction == "S2C"


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------


class TestLoadTraceCsv:
    def test_load_valid_csv(self, tmp_path: Path) -> None:
        rows = [
            _packet(timestamp=0.0, length=100, direction="C2S"),
            _packet(timestamp=0.1, length=200, direction="S2C"),
        ]
        path = _write_csv(tmp_path / "t.csv", rows)
        pkts = load_trace_csv(path)
        assert len(pkts) == 2
        assert isinstance(pkts[0].timestamp, float)
        assert isinstance(pkts[0].src_port, int)
        assert isinstance(pkts[0].length, int)

    def test_empty_csv_yields_empty_list(self, tmp_path: Path) -> None:
        path = _write_csv(tmp_path / "empty.csv", [])
        pkts = load_trace_csv(path)
        assert pkts == []

    def test_invalid_direction_raises(self, tmp_path: Path) -> None:
        rows = [_packet(timestamp=0.0, direction="BAD")]
        path = _write_csv(tmp_path / "bad.csv", rows)
        with pytest.raises(ValueError, match="direction"):
            load_trace_csv(path)

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_trace_csv("/nonexistent/trace.csv")

    def test_field_types(self, tmp_path: Path) -> None:
        rows = [_packet(timestamp=1.5, src_port=54321, dst_port=443, length=512)]
        path = _write_csv(tmp_path / "types.csv", rows)
        pkts = load_trace_csv(path)
        p = pkts[0]
        assert p.timestamp == 1.5
        assert isinstance(p.timestamp, float)
        assert p.src_port == 54321
        assert isinstance(p.src_port, int)
        assert p.dst_port == 443
        assert isinstance(p.dst_port, int)
        assert p.length == 512
        assert isinstance(p.length, int)
        assert p.proto == "tcp"


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


class TestExtractFeatures:
    def test_empty_packets(self) -> None:
        feats = extract_features([])
        assert feats.packet_count == 0
        assert feats.risk_level == "insufficient_data"
        assert "empty" in feats.notes[0].lower()

    def test_fewer_than_5_packets(self) -> None:
        pkts = [FlowPacket(i * 0.1, "a", "b", 1, 2, "tcp", 100, "C2S") for i in range(3)]
        feats = extract_features(pkts)
        assert feats.risk_level == "insufficient_data"
        assert feats.packet_count == 3

    def test_single_direction_all_c2s(self) -> None:
        pkts = [FlowPacket(i * 0.01, "a", "b", 1, 2, "tcp", 64, "C2S") for i in range(20)]
        feats = extract_features(pkts)
        assert feats.c2s_packet_count == 20
        assert feats.s2c_packet_count == 0
        assert feats.direction_switch_count == 0

    def test_single_direction_all_s2c(self) -> None:
        pkts = [FlowPacket(i * 0.01, "a", "b", 1, 2, "tcp", 64, "S2C") for i in range(20)]
        feats = extract_features(pkts)
        assert feats.c2s_packet_count == 0
        assert feats.s2c_packet_count == 20

    def test_repeating_small_packets_medium_or_high(self) -> None:
        """High-frequency repeating small packets → elevated risk."""
        pkts = [
            FlowPacket(i * 0.005, "a", "b", 1, 2, "tcp", 60, d)
            for i, d in enumerate(["C2S", "S2C"] * 25)
        ]
        feats = extract_features(pkts, first_n=30)
        assert feats.risk_level in ("medium", "high")
        assert feats.fingerprint_risk_score > 0.3
        # Small packets dominate
        assert feats.small_packet_ratio > 0.5
        # Lengths repeat heavily
        assert feats.repeated_length_ratio > 0.5

    def test_random_length_bidirectional_lower_than_repeating(self) -> None:
        """Random-length bidirectional trace should score lower than repeating."""
        # Repeating small-packet trace
        pkts_repeat = [
            FlowPacket(i * 0.005, "a", "b", 1, 2, "tcp", 60, d)
            for i, d in enumerate(["C2S", "S2C"] * 25)
        ]
        feats_repeat = extract_features(pkts_repeat, first_n=30)

        # Random-length bidirectional trace
        import random
        rng = random.Random(42)
        pkts_random = [
            FlowPacket(i * 0.020, "a", "b", 1, 2, "tcp", rng.randint(80, 1500), rng.choice(["C2S", "S2C"]))
            for i in range(50)
        ]
        feats_random = extract_features(pkts_random, first_n=30)

        assert feats_repeat.fingerprint_risk_score > feats_random.fingerprint_risk_score, (
            f"repeat={feats_repeat.fingerprint_risk_score} should exceed "
            f"random={feats_random.fingerprint_risk_score}"
        )

    def test_unsorted_timestamps_sorted_internally(self) -> None:
        """Timestamps given out of order should be sorted before extraction."""
        pkts = [
            FlowPacket(3.0, "a", "b", 1, 2, "tcp", 100, "C2S"),
            FlowPacket(1.0, "a", "b", 1, 2, "tcp", 100, "S2C"),
            FlowPacket(2.0, "a", "b", 1, 2, "tcp", 100, "C2S"),
        ]
        feats = extract_features(pkts, first_n=3)
        # After sorting: [1.0, 2.0, 3.0] → directions S2C, C2S, C2S
        assert feats.first_n_directions == ["S2C", "C2S", "C2S"]

    def test_inter_arrival_computation(self) -> None:
        pkts = [
            FlowPacket(0.000, "a", "b", 1, 2, "tcp", 100, "C2S"),
            FlowPacket(0.010, "a", "b", 1, 2, "tcp", 100, "S2C"),
            FlowPacket(0.030, "a", "b", 1, 2, "tcp", 100, "C2S"),
            FlowPacket(0.060, "a", "b", 1, 2, "tcp", 100, "S2C"),
            FlowPacket(0.100, "a", "b", 1, 2, "tcp", 100, "C2S"),
        ]
        feats = extract_features(pkts)
        # Inter-arrivals: 10ms, 20ms, 30ms, 40ms
        assert feats.avg_inter_arrival_ms == pytest.approx(25.0)
        assert feats.median_inter_arrival_ms == pytest.approx(25.0)

    def test_burst_detection(self) -> None:
        """Packets tightly clustered should yield high burst count."""
        pkts = [
            FlowPacket(0.000, "a", "b", 1, 2, "tcp", 64, "C2S"),
            FlowPacket(0.002, "a", "b", 1, 2, "tcp", 64, "C2S"),
            FlowPacket(0.004, "a", "b", 1, 2, "tcp", 64, "C2S"),
            FlowPacket(0.006, "a", "b", 1, 2, "tcp", 64, "C2S"),
            FlowPacket(0.008, "a", "b", 1, 2, "tcp", 64, "C2S"),
            FlowPacket(0.009, "a", "b", 1, 2, "tcp", 64, "C2S"),
            FlowPacket(1.000, "a", "b", 1, 2, "tcp", 64, "C2S"),
        ]
        feats = extract_features(pkts)
        assert feats.max_burst_packets_10ms >= 6

    def test_first_n_smaller_than_total(self) -> None:
        """first_n_lengths and directions should be capped at first_n."""
        pkts = [FlowPacket(i * 0.1, "a", "b", 1, 2, "tcp", i * 10 + 40, "C2S") for i in range(100)]
        feats = extract_features(pkts, first_n=10)
        assert len(feats.first_n_lengths) == 10
        assert len(feats.first_n_directions) == 10


# ---------------------------------------------------------------------------
# summarise_trace (integration helper)
# ---------------------------------------------------------------------------


class TestSummarizeTrace:
    def test_returns_dict_with_keys(self, tmp_path: Path) -> None:
        rows = [
            _packet(timestamp=0.0, length=100, direction="C2S"),
            _packet(timestamp=0.1, length=200, direction="S2C"),
        ]
        path = _write_csv(tmp_path / "s.csv", rows)
        result = summarize_trace(path, first_n=20)
        assert isinstance(result, dict)
        assert result["packet_count"] == 2
        assert "trace_path" in result
        assert "risk_level" in result
        assert "fingerprint_risk_score" in result


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------


class TestRiskScoring:
    def test_insufficient_data(self) -> None:
        score, level, notes = _compute_risk(
            n=3,
            small_packet_ratio=0.5,
            repeated_length_ratio=0.0,
            avg_inter_arrival_ms=10.0,
            stdev_inter_arrival_ms=2.0,
            max_burst=2,
            first_n_directions=["C2S", "S2C", "C2S"],
            direction_switches=2,
            inter_arrivals_ms=[10.0, 20.0],
        )
        assert level == "insufficient_data"
        assert score == 0.0

    def test_low_risk_random_like(self) -> None:
        score, level, notes = _compute_risk(
            n=100,
            small_packet_ratio=0.2,
            repeated_length_ratio=0.1,
            avg_inter_arrival_ms=50.0,
            stdev_inter_arrival_ms=40.0,
            max_burst=2,
            first_n_directions=["C2S", "C2S", "S2C", "C2S", "S2C", "S2C"] * 5,
            direction_switches=14,
            inter_arrivals_ms=[35.0 + (i % 30) * 5.0 for i in range(99)],
        )
        assert level == "low"
        assert score < 0.35

    def test_high_risk_repeating_small_packets(self) -> None:
        score, level, notes = _compute_risk(
            n=100,
            small_packet_ratio=0.95,
            repeated_length_ratio=0.90,
            avg_inter_arrival_ms=5.0,
            stdev_inter_arrival_ms=0.5,
            max_burst=20,
            first_n_directions=["C2S", "S2C"] * 15,
            direction_switches=29,
            inter_arrivals_ms=[5.0] * 99,
        )
        assert level == "high"
        assert score >= 0.7


# ---------------------------------------------------------------------------
# report.py CLI
# ---------------------------------------------------------------------------


class TestReportCli:
    def test_stdout_output(self, tmp_path: Path, capsys: Any) -> None:
        rows = [
            _packet(timestamp=0.0, length=100, direction="C2S"),
            _packet(timestamp=0.1, length=200, direction="S2C"),
        ]
        path = _write_csv(tmp_path / "r.csv", rows)
        report_main(["--input", str(path)])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["packet_count"] == 2
        assert data["risk_level"] == "insufficient_data"

    def test_file_output(self, tmp_path: Path) -> None:
        rows = [
            _packet(timestamp=0.0, length=100, direction="C2S"),
            _packet(timestamp=0.1, length=200, direction="S2C"),
        ]
        in_path = _write_csv(tmp_path / "in.csv", rows)
        out_path = tmp_path / "out.json"
        report_main(["--input", str(in_path), "--output", str(out_path)])
        assert out_path.is_file()
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["packet_count"] == 2

    def test_missing_input_exits_nonzero(self, capsys: Any) -> None:
        with pytest.raises(SystemExit) as exc_info:
            report_main(["--input", "/nonexistent/trace.csv"])
        assert exc_info.value.code == 1

    def test_bad_csv_exits_nonzero(self, tmp_path: Path, capsys: Any) -> None:
        path = tmp_path / "bad.csv"
        path.write_text("timestamp,src,dst,src_port,dst_port,proto,length,direction\n"
                        "0.0,10.0.0.1,10.0.0.2,1,2,tcp,100,BAD\n", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            report_main(["--input", str(path)])
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# FlowFeatures repr / roundtrip
# ---------------------------------------------------------------------------


class TestFlowFeaturesRoundtrip:
    def test_json_serialisable(self) -> None:
        feats = FlowFeatures(
            packet_count=10,
            first_n_lengths=[64, 128, 256],
            first_n_directions=["C2S", "S2C", "C2S"],
            small_packet_ratio=0.5,
            repeated_length_ratio=0.3,
            unique_length_count=5,
            avg_inter_arrival_ms=12.5,
            median_inter_arrival_ms=10.0,
            stdev_inter_arrival_ms=3.2,
            max_burst_packets_10ms=4,
            direction_switch_count=15,
            c2s_packet_count=6,
            s2c_packet_count=4,
            fingerprint_risk_score=0.42,
            risk_level="medium",
            notes=["test"],
        )
        data = json.dumps(
            {
                "packet_count": feats.packet_count,
                "first_n_lengths": feats.first_n_lengths,
                "first_n_directions": feats.first_n_directions,
                "risk_level": feats.risk_level,
                "risk_score": feats.fingerprint_risk_score,
                "notes": feats.notes,
            }
        )
        assert isinstance(data, str)
        roundtrip = json.loads(data)
        assert roundtrip["risk_level"] == "medium"
        assert roundtrip["risk_score"] == 0.42
