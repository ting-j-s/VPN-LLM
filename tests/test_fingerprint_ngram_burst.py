"""Tests for 3-gram and burst feature extraction."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from src.evaluation.fingerprint.burst_features import (
    Burst,
    extract_burst_features,
    extract_bursts,
    summarize_bursts,
)
from src.evaluation.fingerprint.ngram_features import (
    extract_ngram_features,
    extract_ngrams,
    length_bucket,
    ngram_counts,
    ngram_entropy,
    signed_bucket,
    signed_size,
    top_ngrams,
)
from src.evaluation.fingerprint.pcap_features import (
    FlowPacket,
    summarize_trace,
)
from src.evaluation.fingerprint.report import main as report_main

from .test_fingerprint_features import _csv_text, _packet, _write_csv


# ---------------------------------------------------------------------------
# length_bucket
# ---------------------------------------------------------------------------


class TestLengthBucket:
    def test_boundary_L0(self) -> None:
        assert length_bucket(0) == "L0"

    def test_boundary_L1_low(self) -> None:
        assert length_bucket(1) == "L1"

    def test_boundary_L1_high(self) -> None:
        assert length_bucket(160) == "L1"

    def test_boundary_L2_low(self) -> None:
        assert length_bucket(161) == "L2"

    def test_boundary_L2_high(self) -> None:
        assert length_bucket(600) == "L2"

    def test_boundary_L3_low(self) -> None:
        assert length_bucket(601) == "L3"

    def test_boundary_L3_high(self) -> None:
        assert length_bucket(1210) == "L3"

    def test_boundary_L4_low(self) -> None:
        assert length_bucket(1211) == "L4"

    def test_boundary_L4_high(self) -> None:
        assert length_bucket(1500) == "L4"

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="length must be >= 0"):
            length_bucket(-1)


# ---------------------------------------------------------------------------
# signed_bucket / signed_size
# ---------------------------------------------------------------------------


class TestSignedBucket:
    def test_c2s_positive(self) -> None:
        p = FlowPacket(0.0, "a", "b", 1, 2, "tcp", 100, "C2S")
        assert signed_bucket(p) == "+L1"

    def test_s2c_negative(self) -> None:
        p = FlowPacket(0.0, "a", "b", 1, 2, "tcp", 600, "S2C")
        assert signed_bucket(p) == "-L2"


class TestSignedSize:
    def test_c2s_positive(self) -> None:
        p = FlowPacket(0.0, "a", "b", 1, 2, "tcp", 512, "C2S")
        assert signed_size(p) == 512

    def test_s2c_negative(self) -> None:
        p = FlowPacket(0.0, "a", "b", 1, 2, "tcp", 512, "S2C")
        assert signed_size(p) == -512


# ---------------------------------------------------------------------------
# ngram extraction
# ---------------------------------------------------------------------------


class TestExtractNgrams:
    def test_three_grams_from_four_items(self) -> None:
        items = ["+L2", "-L4", "-L4", "+L1"]
        result = extract_ngrams(items, n=3)
        assert len(result) == 2
        assert result[0] == ("+L2", "-L4", "-L4")
        assert result[1] == ("-L4", "-L4", "+L1")

    def test_fewer_items_than_n(self) -> None:
        items = ["+L1", "+L2"]
        result = extract_ngrams(items, n=3)
        assert result == []

    def test_exact_n_items(self) -> None:
        items = ["+L1", "+L2", "-L3"]
        result = extract_ngrams(items, n=3)
        assert len(result) == 1

    def test_invalid_n_raises(self) -> None:
        with pytest.raises(ValueError, match="n must be >= 1"):
            extract_ngrams(["+L1"], n=0)


# ---------------------------------------------------------------------------
# ngram_counts
# ---------------------------------------------------------------------------


class TestNgramCounts:
    def test_count_format(self) -> None:
        ngrams = [("+L2", "-L4", "-L4"), ("-L4", "-L4", "+L1")]
        counts = ngram_counts(ngrams)
        assert "+L2|-L4|-L4" in counts
        assert counts["+L2|-L4|-L4"] == 1

    def test_empty_list(self) -> None:
        assert ngram_counts([]) == {}


# ---------------------------------------------------------------------------
# ngram_entropy
# ---------------------------------------------------------------------------


class TestNgramEntropy:
    def test_empty_zero(self) -> None:
        assert ngram_entropy({}) == 0.0

    def test_single_ngram_zero_entropy(self) -> None:
        assert ngram_entropy({"A|B|C": 10}) == 0.0

    def test_diverse_higher_than_repeating(self) -> None:
        # All same → entropy 0
        counts_repeat = {"A|A|A": 100}
        # 4 equally frequent → entropy = 2 bits
        counts_diverse = {"A|B|C": 25, "B|C|D": 25, "C|D|E": 25, "D|E|F": 25}
        e_repeat = ngram_entropy(counts_repeat)
        e_diverse = ngram_entropy(counts_diverse)
        assert e_diverse > e_repeat
        assert e_diverse == pytest.approx(2.0, abs=0.01)


# ---------------------------------------------------------------------------
# top_ngrams
# ---------------------------------------------------------------------------


class TestTopNgrams:
    def test_top_k_order_and_ratio(self) -> None:
        counts = {"A|B|C": 5, "B|C|D": 3, "C|D|E": 2}
        top = top_ngrams(counts, k=2)
        assert len(top) == 2
        assert top[0]["ngram"] == "A|B|C"
        assert top[0]["count"] == 5
        assert top[0]["ratio"] == 0.5
        assert top[1]["ngram"] == "B|C|D"
        assert top[1]["ratio"] == 0.3

    def test_empty_returns_empty(self) -> None:
        assert top_ngrams({}) == []


# ---------------------------------------------------------------------------
# extract_ngram_features
# ---------------------------------------------------------------------------


class TestExtractNgramFeatures:
    def test_tls_handshake_like_pattern(self) -> None:
        """Simulate a TLS-handshake-like (size, dir) pattern."""
        pkts = [
            FlowPacket(i * 0.005, "a", "b", 1, 443, "tcp", l, d)
            for i, (l, d) in enumerate(
                [
                    (180, "C2S"),   # +L2
                    (1514, "S2C"),  # -L4
                    (1514, "S2C"),  # -L4
                    (400, "C2S"),   # +L2
                    (1514, "S2C"),  # -L4
                    (1514, "S2C"),  # -L4
                    (100, "C2S"),   # +L1
                    (200, "S2C"),   # -L2
                    (1514, "S2C"),  # -L4
                    (120, "C2S"),   # +L1
                ]
            )
        ]
        feats = extract_ngram_features(pkts, n=3)
        assert len(feats["signed_sizes"]) == 10
        assert len(feats["bucket_sequence"]) == 10
        assert feats["ngram_n"] == 3
        assert feats["ngram_count"] == 8  # 10 - 3 + 1
        assert feats["ngram_entropy"] > 0
        assert feats["unique_ngram_count"] >= 1
        assert "top_ngrams" in feats
        assert isinstance(feats["dominant_ngram_ratio"], float)

    def test_empty_packets(self) -> None:
        feats = extract_ngram_features([], n=3)
        assert feats["signed_sizes"] == []
        assert feats["bucket_sequence"] == []
        assert feats["ngram_count"] == 0
        assert feats["ngram_entropy"] == 0.0
        assert feats["dominant_ngram_ratio"] == 0.0

    def test_unsorted_packets_sorted_internally(self) -> None:
        pkts = [
            FlowPacket(3.0, "a", "b", 1, 2, "tcp", 200, "C2S"),
            FlowPacket(1.0, "a", "b", 1, 2, "tcp", 500, "S2C"),
            FlowPacket(2.0, "a", "b", 1, 2, "tcp", 100, "C2S"),
        ]
        # After sorting: t=1.0→S2C/-L2, t=2.0→C2S/+L1, t=3.0→C2S/+L2
        feats = extract_ngram_features(pkts, n=2)
        assert feats["bucket_sequence"] == ["-L2", "+L1", "+L2"]


# ---------------------------------------------------------------------------
# burst extraction
# ---------------------------------------------------------------------------


class TestExtractBursts:
    def test_same_direction_small_gap_merged(self) -> None:
        pkts = [
            FlowPacket(0.000, "a", "b", 1, 2, "tcp", 100, "C2S"),
            FlowPacket(0.005, "a", "b", 1, 2, "tcp", 200, "C2S"),
            FlowPacket(0.009, "a", "b", 1, 2, "tcp", 150, "C2S"),
        ]
        bursts = extract_bursts(pkts, max_gap_ms=10.0)
        assert len(bursts) == 1
        b = bursts[0]
        assert b.direction == "C2S"
        assert b.packet_count == 3
        assert b.total_length == 450

    def test_direction_change_splits(self) -> None:
        pkts = [
            FlowPacket(0.000, "a", "b", 1, 2, "tcp", 100, "C2S"),
            FlowPacket(0.001, "a", "b", 1, 2, "tcp", 200, "S2C"),
            FlowPacket(0.002, "a", "b", 1, 2, "tcp", 100, "C2S"),
        ]
        bursts = extract_bursts(pkts, max_gap_ms=10.0)
        assert len(bursts) == 3  # each direction change splits
        assert [b.direction for b in bursts] == ["C2S", "S2C", "C2S"]

    def test_gap_exceeding_threshold_splits(self) -> None:
        pkts = [
            FlowPacket(0.000, "a", "b", 1, 2, "tcp", 100, "C2S"),
            FlowPacket(0.015, "a", "b", 1, 2, "tcp", 100, "C2S"),  # 15 ms gap > 10 ms
            FlowPacket(0.020, "a", "b", 1, 2, "tcp", 100, "C2S"),
        ]
        bursts = extract_bursts(pkts, max_gap_ms=10.0)
        assert len(bursts) == 2  # gap at 15 ms splits

    def test_large_max_gap_keeps_merged(self) -> None:
        pkts = [
            FlowPacket(0.000, "a", "b", 1, 2, "tcp", 100, "C2S"),
            FlowPacket(0.015, "a", "b", 1, 2, "tcp", 100, "C2S"),
        ]
        bursts = extract_bursts(pkts, max_gap_ms=20.0)
        assert len(bursts) == 1

    def test_empty_packets(self) -> None:
        assert extract_bursts([], max_gap_ms=10.0) == []

    def test_burst_duration(self) -> None:
        pkts = [
            FlowPacket(0.000, "a", "b", 1, 2, "tcp", 100, "C2S"),
            FlowPacket(0.005, "a", "b", 1, 2, "tcp", 100, "C2S"),
            FlowPacket(0.010, "a", "b", 1, 2, "tcp", 100, "C2S"),
        ]
        bursts = extract_bursts(pkts, max_gap_ms=10.0)
        assert len(bursts) == 1
        assert bursts[0].duration_ms == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# burst summary
# ---------------------------------------------------------------------------


class TestSummarizeBursts:
    def test_statistics_correct(self) -> None:
        bursts = [
            Burst("C2S", 3, 300, 0.0, 0.010),
            Burst("S2C", 5, 1000, 0.015, 0.025),
            Burst("C2S", 2, 200, 0.030, 0.035),
            Burst("S2C", 4, 800, 0.040, 0.050),
        ]
        summary = summarize_bursts(bursts)
        assert summary["burst_count"] == 4
        assert summary["burst_total_lengths"] == [300, 1000, 200, 800]
        assert summary["burst_directions"] == ["C2S", "S2C", "C2S", "S2C"]
        assert summary["max_burst_size"] == 1000
        assert summary["avg_burst_size"] == 575.0
        assert summary["median_burst_size"] == 550  # median of [200, 300, 800, 1000]
        assert summary["p95_burst_size"] == 1000
        assert summary["avg_burst_packet_count"] == 3.5
        assert summary["max_burst_packet_count"] == 5
        assert summary["c2s_burst_count"] == 2
        assert summary["s2c_burst_count"] == 2
        assert summary["direction_switch_count_between_bursts"] == 3
        assert summary["dominant_burst_direction_ratio"] == 0.5

    def test_dominant_direction_biased(self) -> None:
        bursts = [
            Burst("C2S", 3, 300, 0.0, 0.010),
            Burst("C2S", 2, 200, 0.020, 0.025),
            Burst("C2S", 5, 500, 0.030, 0.040),
            Burst("S2C", 1, 100, 0.050, 0.051),
        ]
        summary = summarize_bursts(bursts)
        assert summary["dominant_burst_direction_ratio"] == 0.75

    def test_empty_bursts(self) -> None:
        summary = summarize_bursts([])
        assert summary["burst_count"] == 0
        assert summary["burst_total_lengths"] == []


# ---------------------------------------------------------------------------
# extract_burst_features convenience wrapper
# ---------------------------------------------------------------------------


class TestExtractBurstFeatures:
    def test_integration(self) -> None:
        pkts = [
            FlowPacket(0.000, "a", "b", 1, 2, "tcp", 500, "C2S"),
            FlowPacket(0.005, "a", "b", 1, 2, "tcp", 600, "C2S"),
            FlowPacket(0.010, "a", "b", 1, 2, "tcp", 700, "C2S"),
            FlowPacket(0.020, "a", "b", 1, 2, "tcp", 300, "S2C"),
            FlowPacket(0.025, "a", "b", 1, 2, "tcp", 400, "S2C"),
        ]
        feats = extract_burst_features(pkts, max_gap_ms=10.0)
        assert feats["burst_count"] == 2
        assert feats["c2s_burst_count"] == 1
        assert feats["s2c_burst_count"] == 1


# ---------------------------------------------------------------------------
# summarize_trace integration (ngram + burst fields)
# ---------------------------------------------------------------------------


class TestSummarizeTraceNgramBurst:
    def test_tls_handshake_like_trace(self, tmp_path: Path) -> None:
        """Construct a TLS-handshake-like trace and verify ngram/burst fields."""
        # Pattern: ClientHello(+L2), ServerHello+Cert(-L4,-L4), ClientKeyExchange(+L1),
        #          ServerCCS(-L1), ClientCCS(+L1), AppData...
        pkts = [
            FlowPacket(0.000, "10.0.0.1", "10.0.0.2", 45001, 443, "tcp", 180, "C2S"),
            FlowPacket(0.005, "10.0.0.2", "10.0.0.1", 443, 45001, "tcp", 1514, "S2C"),
            FlowPacket(0.006, "10.0.0.2", "10.0.0.1", 443, 45001, "tcp", 1514, "S2C"),
            FlowPacket(0.012, "10.0.0.1", "10.0.0.2", 45001, 443, "tcp", 400, "C2S"),
            FlowPacket(0.018, "10.0.0.2", "10.0.0.1", 443, 45001, "tcp", 1514, "S2C"),
            FlowPacket(0.019, "10.0.0.2", "10.0.0.1", 443, 45001, "tcp", 1514, "S2C"),
            FlowPacket(0.025, "10.0.0.1", "10.0.0.2", 45001, 443, "tcp", 100, "C2S"),
            FlowPacket(0.030, "10.0.0.2", "10.0.0.1", 443, 45001, "tcp", 200, "S2C"),
        ]
        rows = [
            _packet(
                timestamp=p.timestamp,
                length=p.length,
                direction=p.direction,
                src_port=p.src_port,
                dst_port=p.dst_port,
            )
            for p in pkts
        ]
        path = _write_csv(tmp_path / "tls_like.csv", rows)
        result = summarize_trace(path, first_n=20, ngram_n=3, burst_gap_ms=10.0)

        # ngram fields
        assert "signed_sizes" in result
        assert result["packet_count"] == 8
        assert "ngram_entropy" in result
        assert isinstance(result["ngram_entropy"], float)
        assert "unique_ngram_count" in result
        assert "dominant_ngram_ratio" in result
        assert "top_ngrams" in result
        assert len(result["top_ngrams"]) > 0
        for tng in result["top_ngrams"]:
            assert "ngram" in tng
            assert "count" in tng
            assert "ratio" in tng

        # burst fields
        assert "burst_count" in result
        assert result["burst_count"] >= 1
        assert "burst_total_lengths" in result
        assert "burst_directions" in result
        assert "max_burst_size" in result
        assert "avg_burst_size" in result
        assert "median_burst_size" in result
        assert "p95_burst_size" in result
        assert "dominant_burst_direction_ratio" in result


# ---------------------------------------------------------------------------
# report.py CLI with new args
# ---------------------------------------------------------------------------


class TestReportCliNgramBurst:
    def test_ngram_n_arg(self, tmp_path: Path, capsys: Any) -> None:
        rows = [_packet(timestamp=0.0, length=100, direction="C2S") for _ in range(10)]
        path = _write_csv(tmp_path / "t.csv", rows)
        report_main(["--input", str(path), "--ngram-n", "2"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "top_ngrams" in data

    def test_burst_gap_ms_arg(self, tmp_path: Path, capsys: Any) -> None:
        rows = [
            _packet(timestamp=0.000, length=100, direction="C2S"),
            _packet(timestamp=0.015, length=100, direction="C2S"),
        ]
        path = _write_csv(tmp_path / "t.csv", rows)
        # default gap=10ms → 2 bursts; with gap=20ms → 1 burst
        report_main(["--input", str(path), "--burst-gap-ms", "20"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["burst_count"] == 1

    def test_top_k_truncation(self, tmp_path: Path, capsys: Any) -> None:
        """With --top-k 3, top_ngrams should have at most 3 entries."""
        # Create diverse pattern to get multiple ngrams
        pkts = [
            FlowPacket(i * 0.005, "a", "b", 1, 2, "tcp", 100 + (i % 5) * 275, d)
            for i, d in enumerate(["C2S", "S2C"] * 10)
        ]
        rows = [_packet(timestamp=p.timestamp, length=p.length, direction=p.direction) for p in pkts]
        path = _write_csv(tmp_path / "diverse.csv", rows)
        report_main(["--input", str(path), "--top-k", "3"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data["top_ngrams"]) <= 3
