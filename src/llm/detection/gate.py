"""DetectionGate — evaluate DetectionReport against configurable thresholds.

Implements multi-metric gate evaluation with support for:
- OpenVPN-style fingerprint metrics (packet size, direction, timing)
- Encapsulated TLS metrics (ngram, burst, small-packet)
- CalcuLatency-style RTT cross-layer metrics
- Probe/malformed response consistency metrics
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .detector_report import (
    DetectionMetric,
    DetectionReport,
    from_fingerprint_report,
    load_detection_report,
)


@dataclass
class DetectionThresholds:
    """Configurable thresholds for detection gate evaluation."""

    max_risk_score: float = 0.70
    max_repeated_length_ratio: float | None = 0.60
    max_small_packet_ratio: float | None = 0.60
    min_ngram_entropy: float | None = None
    max_dominant_ngram_ratio: float | None = 0.50
    max_dominant_burst_direction_ratio: float | None = 0.80
    max_max_burst_size: int | None = None
    min_packet_count: int = 5
    fail_on_insufficient_data: bool = False
    max_avg_inter_arrival_ms_deviation: float | None = None
    max_rtt_diff_ms: float | None = None
    max_app_transport_diff_ms: float | None = 50.0
    max_app_network_diff_ms: float | None = None
    max_timing_stability_score: float | None = None
    max_probe_response_variance: float | None = None
    max_malformed_close_time_variance: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_risk_score": self.max_risk_score,
            "max_repeated_length_ratio": self.max_repeated_length_ratio,
            "max_small_packet_ratio": self.max_small_packet_ratio,
            "min_ngram_entropy": self.min_ngram_entropy,
            "max_dominant_ngram_ratio": self.max_dominant_ngram_ratio,
            "max_dominant_burst_direction_ratio": self.max_dominant_burst_direction_ratio,
            "max_max_burst_size": self.max_max_burst_size,
            "min_packet_count": self.min_packet_count,
            "fail_on_insufficient_data": self.fail_on_insufficient_data,
            "max_avg_inter_arrival_ms_deviation": self.max_avg_inter_arrival_ms_deviation,
            "max_rtt_diff_ms": self.max_rtt_diff_ms,
            "max_app_transport_diff_ms": self.max_app_transport_diff_ms,
            "max_app_network_diff_ms": self.max_app_network_diff_ms,
            "max_timing_stability_score": self.max_timing_stability_score,
            "max_probe_response_variance": self.max_probe_response_variance,
            "max_malformed_close_time_variance": self.max_malformed_close_time_variance,
        }


# ---------------------------------------------------------------------------
# Gate evaluation rules
# ---------------------------------------------------------------------------


def _find_metric(metrics: list[DetectionMetric], name: str) -> DetectionMetric | None:
    for m in metrics:
        if m.name == name:
            return m
    return None


def _fail_metric(metric: DetectionMetric, reason: str, threshold: float | int) -> None:
    metric.passed = False
    metric.severity = "fail"
    metric.threshold = threshold
    metric.explanation = reason


def _warn_metric(metric: DetectionMetric, reason: str, threshold: float | int) -> None:
    metric.passed = False
    metric.severity = "warning"
    metric.threshold = threshold
    metric.explanation = reason


def evaluate_detection_report(
    report: DetectionReport,
    thresholds: DetectionThresholds | None = None,
) -> DetectionReport:
    """Evaluate a DetectionReport against thresholds, mutating metrics in place.

    Returns the same report (mutated) with updated metric passed/severity fields
    and an overall passed flag.
    """
    if thresholds is None:
        thresholds = DetectionThresholds()

    # Missing report entirely → fail
    if report.trace_type == "skipped" and not report.metrics:
        report.passed = False
        report.metrics.append(DetectionMetric(
            name="report_missing",
            value=None,
            threshold=None,
            passed=False,
            severity="fail",
            explanation="report file not found or unreadable",
        ))
        return report

    overall_passed = True

    # --- risk_score ---
    m = _find_metric(report.metrics, "risk_level")
    if m and m.value == "insufficient_data":
        if thresholds.fail_on_insufficient_data:
            _fail_metric(m, f"insufficient_data and fail_on_insufficient_data=True", 0)
            overall_passed = False
        else:
            _warn_metric(m, f"insufficient_data (packet_count < {thresholds.min_packet_count})",
                         thresholds.min_packet_count)
            overall_passed = False  # Still not a clean pass

    # If no metrics at all and no risk_level warning, that's a problem
    if not report.metrics:
        report.metrics.append(DetectionMetric(
            name="no_metrics",
            value=None,
            threshold=None,
            passed=False,
            severity="fail",
            explanation="report contains no evaluable metrics",
        ))
        report.passed = False
        return report

    # --- packet_count ---
    m = _find_metric(report.metrics, "packet_count")
    if m and isinstance(m.value, (int, float)):
        if m.value < thresholds.min_packet_count:
            if thresholds.fail_on_insufficient_data:
                _fail_metric(m,
                    f"packet_count={m.value} < min_packet_count={thresholds.min_packet_count}",
                    thresholds.min_packet_count)
                overall_passed = False
            else:
                _warn_metric(m,
                    f"packet_count={m.value} < min_packet_count={thresholds.min_packet_count}",
                    thresholds.min_packet_count)

    # --- risk_score --- (may also be a separate metric from the report)
    if report.risk_score is not None and report.risk_score > thresholds.max_risk_score:
        # Add a metric for this if not already present
        existing = _find_metric(report.metrics, "fingerprint_risk_score")
        if existing:
            _fail_metric(existing,
                f"risk_score={report.risk_score} > max_risk_score={thresholds.max_risk_score}",
                thresholds.max_risk_score)
        else:
            report.metrics.append(DetectionMetric(
                name="fingerprint_risk_score",
                value=report.risk_score,
                threshold=thresholds.max_risk_score,
                passed=False,
                severity="fail",
                explanation=f"risk_score={report.risk_score} > max_risk_score={thresholds.max_risk_score}",
            ))
        overall_passed = False

    # --- repeated_length_ratio ---
    if thresholds.max_repeated_length_ratio is not None:
        m = _find_metric(report.metrics, "repeated_length_ratio")
        if m and isinstance(m.value, (int, float)):
            if m.value > thresholds.max_repeated_length_ratio:
                _fail_metric(m,
                    f"repeated_length_ratio={m.value} > max={thresholds.max_repeated_length_ratio}",
                    thresholds.max_repeated_length_ratio)
                overall_passed = False

    # --- small_packet_ratio ---
    if thresholds.max_small_packet_ratio is not None:
        m = _find_metric(report.metrics, "small_packet_ratio")
        if m and isinstance(m.value, (int, float)):
            if m.value > thresholds.max_small_packet_ratio:
                _fail_metric(m,
                    f"small_packet_ratio={m.value} > max={thresholds.max_small_packet_ratio}",
                    thresholds.max_small_packet_ratio)
                overall_passed = False

    # --- ngram_entropy ---
    if thresholds.min_ngram_entropy is not None:
        m = _find_metric(report.metrics, "ngram_entropy")
        if m and isinstance(m.value, (int, float)):
            if m.value < thresholds.min_ngram_entropy:
                _fail_metric(m,
                    f"ngram_entropy={m.value} < min={thresholds.min_ngram_entropy}",
                    thresholds.min_ngram_entropy)
                overall_passed = False

    # --- dominant_ngram_ratio ---
    if thresholds.max_dominant_ngram_ratio is not None:
        m = _find_metric(report.metrics, "dominant_ngram_ratio")
        if m and isinstance(m.value, (int, float)):
            if m.value > thresholds.max_dominant_ngram_ratio:
                _fail_metric(m,
                    f"dominant_ngram_ratio={m.value} > max={thresholds.max_dominant_ngram_ratio}",
                    thresholds.max_dominant_ngram_ratio)
                overall_passed = False

    # --- dominant_burst_direction_ratio ---
    if thresholds.max_dominant_burst_direction_ratio is not None:
        m = _find_metric(report.metrics, "dominant_burst_direction_ratio")
        if m and isinstance(m.value, (int, float)):
            if m.value > thresholds.max_dominant_burst_direction_ratio:
                _fail_metric(m,
                    f"dominant_burst_direction_ratio={m.value} > max={thresholds.max_dominant_burst_direction_ratio}",
                    thresholds.max_dominant_burst_direction_ratio)
                overall_passed = False

    # --- max_burst_size ---
    if thresholds.max_max_burst_size is not None:
        m = _find_metric(report.metrics, "max_burst_size")
        if m and isinstance(m.value, (int, float)):
            if m.value > thresholds.max_max_burst_size:
                _fail_metric(m,
                    f"max_burst_size={m.value} > max={thresholds.max_max_burst_size}",
                    thresholds.max_max_burst_size)
                overall_passed = False

    # --- avg_inter_arrival_ms deviation ---
    if thresholds.max_avg_inter_arrival_ms_deviation is not None:
        m = _find_metric(report.metrics, "avg_inter_arrival_ms")
        if m and isinstance(m.value, (int, float)):
            if m.value > thresholds.max_avg_inter_arrival_ms_deviation:
                _fail_metric(m,
                    f"avg_inter_arrival_ms={m.value} exceeds deviation max={thresholds.max_avg_inter_arrival_ms_deviation}",
                    thresholds.max_avg_inter_arrival_ms_deviation)
                overall_passed = False

    # --- RTT diff (legacy, may coexist with app_transport_diff_ms) ---
    if thresholds.max_rtt_diff_ms is not None:
        m = _find_metric(report.metrics, "rtt_diff_ms")
        if m and isinstance(m.value, (int, float)):
            if m.value > thresholds.max_rtt_diff_ms:
                _fail_metric(m,
                    f"rtt_diff_ms={m.value} > max={thresholds.max_rtt_diff_ms}",
                    thresholds.max_rtt_diff_ms)
                overall_passed = False

    # --- app_transport_diff_ms ---
    if thresholds.max_app_transport_diff_ms is not None:
        m = _find_metric(report.metrics, "app_transport_diff_ms")
        if m and isinstance(m.value, (int, float)):
            if m.value > thresholds.max_app_transport_diff_ms:
                _fail_metric(m,
                    f"app_transport_diff_ms={m.value} > max={thresholds.max_app_transport_diff_ms}",
                    thresholds.max_app_transport_diff_ms)
                overall_passed = False

    # --- app_network_diff_ms ---
    if thresholds.max_app_network_diff_ms is not None:
        m = _find_metric(report.metrics, "app_network_diff_ms")
        if m and isinstance(m.value, (int, float)):
            if m.value > thresholds.max_app_network_diff_ms:
                _fail_metric(m,
                    f"app_network_diff_ms={m.value} > max={thresholds.max_app_network_diff_ms}",
                    thresholds.max_app_network_diff_ms)
                overall_passed = False

    # --- timing_stability_score ---
    if thresholds.max_timing_stability_score is not None:
        m = _find_metric(report.metrics, "timing_stability_score")
        if m and isinstance(m.value, (int, float)):
            if m.value > thresholds.max_timing_stability_score:
                _fail_metric(m,
                    f"timing_stability_score={m.value} > max={thresholds.max_timing_stability_score}",
                    thresholds.max_timing_stability_score)
                overall_passed = False

    # --- rtt_risk_score ---
    m = _find_metric(report.metrics, "rtt_risk_score")
    if m and isinstance(m.value, (int, float)):
        if m.value >= 0.6:  # high
            _fail_metric(m,
                f"rtt_risk_score={m.value} >= 0.6 (high risk)",
                0.6)
            overall_passed = False
        elif m.value >= 0.3:  # medium
            _warn_metric(m,
                f"rtt_risk_score={m.value} >= 0.3 (medium risk)",
                0.3)
            overall_passed = False

    # --- probe response variance ---
    if thresholds.max_probe_response_variance is not None:
        m = _find_metric(report.metrics, "probe_response_variance")
        if m and isinstance(m.value, (int, float)):
            if m.value > thresholds.max_probe_response_variance:
                _fail_metric(m,
                    f"probe_response_variance={m.value} > max={thresholds.max_probe_response_variance}",
                    thresholds.max_probe_response_variance)
                overall_passed = False

    # --- malformed close time variance ---
    if thresholds.max_malformed_close_time_variance is not None:
        m = _find_metric(report.metrics, "malformed_close_time_variance")
        if m and isinstance(m.value, (int, float)):
            if m.value > thresholds.max_malformed_close_time_variance:
                _fail_metric(m,
                    f"malformed_close_time_variance={m.value} > max={thresholds.max_malformed_close_time_variance}",
                    thresholds.max_malformed_close_time_variance)
                overall_passed = False

    report.passed = overall_passed
    return report


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def evaluate_fingerprint_json(
    report_json: dict[str, Any],
    thresholds: DetectionThresholds | None = None,
) -> DetectionReport:
    """Evaluate a fingerprint report dict through the detection gate."""
    report = from_fingerprint_report(report_json)
    return evaluate_detection_report(report, thresholds)


def evaluate_report_file(
    path: str | Path,
    thresholds: DetectionThresholds | None = None,
) -> DetectionReport:
    """Load and evaluate a single report file."""
    report = load_detection_report(path)
    return evaluate_detection_report(report, thresholds)


def evaluate_report_files(
    paths: list[str | Path],
    thresholds: DetectionThresholds | None = None,
    mode: str = "all",
) -> list[DetectionReport]:
    """Evaluate multiple report files.

    Args:
        paths: List of report file paths.
        thresholds: Detection thresholds.
        mode: "all" to evaluate each individually and return list.

    Returns:
        List of evaluated DetectionReports.
    """
    results = []
    for path in paths:
        result = evaluate_report_file(path, thresholds)
        results.append(result)
    return results
