"""DetectionReport — unified structure for fingerprint / RTT / probe reports.

Compatible with Phase 1/2/3.5 fingerprint report JSON format.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DetectionMetric:
    """A single detection metric with threshold evaluation."""

    name: str
    value: float | int | str | None
    threshold: float | int | None = None
    passed: bool = True
    severity: str = "info"  # info, warning, fail
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "threshold": self.threshold,
            "passed": self.passed,
            "severity": self.severity,
            "explanation": self.explanation,
        }


@dataclass
class DetectionReport:
    """Unified detection report from any detector source.

    Accepts fingerprint reports, RTT reports, probe reports, and
    before/after shaping comparison reports.
    """

    detector_name: str = "unknown"
    source_path: str | None = None
    trace_type: str = "unknown"  # real, synthetic, skipped, unknown
    transport: str | None = None
    scenario: str | None = None
    passed: bool = True
    risk_score: float | None = None
    risk_level: str | None = None
    metrics: list[DetectionMetric] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector_name": self.detector_name,
            "source_path": self.source_path,
            "trace_type": self.trace_type,
            "transport": self.transport,
            "scenario": self.scenario,
            "passed": self.passed,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "metrics": [m.to_dict() for m in self.metrics],
            "notes": self.notes,
            "raw": self.raw,
        }


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def infer_transport_scenario_from_path(path: str | Path) -> tuple[str | None, str | None]:
    """Infer (transport, scenario) from a path like traces/tcp/idle.report.json.

    Returns (None, None) if the path doesn't match the expected pattern.
    """
    p = Path(path)
    parts = p.parts

    transport = None
    scenario = None

    for i, part in enumerate(parts):
        if part in ("tcp", "tls", "websocket", "ssh"):
            transport = part
        if part in ("idle", "ping", "curl", "bulk", "reconnect"):
            scenario = part

    # Fallback: parse from filename like tcp_idle.report.json or tcp.idle.report.json
    if transport is None or scenario is None:
        stem = p.stem  # e.g. "idle.report" or "idle"
        for sep in (".", "_"):
            name_part = stem.split(sep)[0]
            if name_part in ("idle", "ping", "curl", "bulk", "reconnect"):
                scenario = name_part
                break

    # Try basename of parent for transport
    if transport is None:
        parent_name = p.parent.name
        if parent_name in ("tcp", "tls", "websocket", "ssh"):
            transport = parent_name

    return transport, scenario


def from_fingerprint_report(report: dict[str, Any],
                            source_path: str | None = None) -> DetectionReport:
    """Create a DetectionReport from a Phase 1/2/3.5 fingerprint report dict.

    Missing fields are recorded in notes rather than causing errors.
    """
    transport: str | None = None
    scenario_value: str | None = None
    if source_path:
        transport, scenario_value = infer_transport_scenario_from_path(source_path)

    notes: list[str] = []
    metrics: list[DetectionMetric] = []

    # --- packet_count ---
    packet_count = report.get("packet_count")
    if packet_count is None:
        notes.append("missing field: packet_count")
    else:
        metrics.append(DetectionMetric(
            name="packet_count",
            value=packet_count,
            threshold=None,
            passed=True,
            severity="info",
            explanation=f"packet_count={packet_count}",
        ))

    # --- risk_score ---
    risk_score = report.get("fingerprint_risk_score")
    if risk_score is None or risk_score == "":
        notes.append("missing field: fingerprint_risk_score")
        risk_score = None
    else:
        try:
            risk_score = float(risk_score)
        except (TypeError, ValueError):
            notes.append(f"fingerprint_risk_score is not numeric: {risk_score}")
            risk_score = None

    # --- risk_level ---
    risk_level = report.get("risk_level")
    if risk_level is None or risk_level == "":
        notes.append("missing field: risk_level")
        risk_level = None
    elif isinstance(risk_level, str) and risk_level.lower() in ("skipped", "insufficient_data"):
        notes.append(f"trace was skipped or insufficient data (risk_level={risk_level})")
        metrics.append(DetectionMetric(
            name="risk_level",
            value=risk_level,
            threshold=None,
            passed=False,
            severity="warning",
            explanation=f"risk_level={risk_level} — insufficient data or skipped",
        ))
        risk_level = "insufficient_data"

    # --- repeatled_length_ratio ---
    repeated = report.get("repeated_length_ratio")
    if repeated is None or repeated == "":
        notes.append("missing field: repeated_length_ratio")
    else:
        try:
            metrics.append(DetectionMetric(
                name="repeated_length_ratio",
                value=float(repeated),
                threshold=None,
                passed=True,
                severity="info",
                explanation=f"repeated_length_ratio={repeated}",
            ))
        except (TypeError, ValueError):
            notes.append(f"repeated_length_ratio is not numeric: {repeated}")

    # --- small_packet_ratio ---
    small = report.get("small_packet_ratio")
    if small is None or small == "":
        notes.append("missing field: small_packet_ratio")
    else:
        try:
            metrics.append(DetectionMetric(
                name="small_packet_ratio",
                value=float(small),
                threshold=None,
                passed=True,
                severity="info",
                explanation=f"small_packet_ratio={small}",
            ))
        except (TypeError, ValueError):
            notes.append(f"small_packet_ratio is not numeric: {small}")

    # --- ngram_entropy ---
    entropy = report.get("ngram_entropy")
    if entropy is None or entropy == "":
        notes.append("missing field: ngram_entropy")
    else:
        try:
            metrics.append(DetectionMetric(
                name="ngram_entropy",
                value=float(entropy),
                threshold=None,
                passed=True,
                severity="info",
                explanation=f"ngram_entropy={entropy}",
            ))
        except (TypeError, ValueError):
            notes.append(f"ngram_entropy is not numeric: {entropy}")

    # --- dominant_ngram_ratio ---
    dom_ngram = report.get("dominant_ngram_ratio")
    if dom_ngram is None or dom_ngram == "":
        notes.append("missing field: dominant_ngram_ratio")
    else:
        try:
            metrics.append(DetectionMetric(
                name="dominant_ngram_ratio",
                value=float(dom_ngram),
                threshold=None,
                passed=True,
                severity="info",
                explanation=f"dominant_ngram_ratio={dom_ngram}",
            ))
        except (TypeError, ValueError):
            notes.append(f"dominant_ngram_ratio is not numeric: {dom_ngram}")

    # --- burst_count ---
    burst_count = report.get("burst_count")
    if burst_count is not None and burst_count != "":
        try:
            metrics.append(DetectionMetric(
                name="burst_count",
                value=int(burst_count),
                threshold=None,
                passed=True,
                severity="info",
                explanation=f"burst_count={burst_count}",
            ))
        except (TypeError, ValueError):
            notes.append(f"burst_count is not numeric: {burst_count}")

    # --- max_burst_size ---
    max_burst = report.get("max_burst_size")
    if max_burst is not None and max_burst != "":
        try:
            metrics.append(DetectionMetric(
                name="max_burst_size",
                value=int(max_burst),
                threshold=None,
                passed=True,
                severity="info",
                explanation=f"max_burst_size={max_burst}",
            ))
        except (TypeError, ValueError):
            notes.append(f"max_burst_size is not numeric: {max_burst}")

    # --- dominant_burst_direction_ratio ---
    dom_burst_dir = report.get("dominant_burst_direction_ratio")
    if dom_burst_dir is not None and dom_burst_dir != "":
        try:
            metrics.append(DetectionMetric(
                name="dominant_burst_direction_ratio",
                value=float(dom_burst_dir),
                threshold=None,
                passed=True,
                severity="info",
                explanation=f"dominant_burst_direction_ratio={dom_burst_dir}",
            ))
        except (TypeError, ValueError):
            notes.append(f"dominant_burst_direction_ratio is not numeric: {dom_burst_dir}")

    # --- avg_inter_arrival_ms ---
    avg_ia = report.get("avg_inter_arrival_ms")
    if avg_ia is not None and avg_ia != "":
        try:
            metrics.append(DetectionMetric(
                name="avg_inter_arrival_ms",
                value=float(avg_ia),
                threshold=None,
                passed=True,
                severity="info",
                explanation=f"avg_inter_arrival_ms={avg_ia}",
            ))
        except (TypeError, ValueError):
            notes.append(f"avg_inter_arrival_ms is not numeric: {avg_ia}")

    # --- Report notes ---
    report_notes = report.get("notes", [])
    if isinstance(report_notes, list):
        notes.extend(report_notes)
    elif isinstance(report_notes, str) and report_notes:
        notes.append(report_notes)

    # Determine passed state
    passed = True
    insufficient = any(
        m.name == "risk_level" and m.severity == "warning"
        for m in metrics
    )
    if insufficient:
        passed = False

    # Determine trace_type from risk_level or packet_count
    trace_type = "unknown"
    if risk_level == "insufficient_data" or (packet_count is not None and packet_count == 0):
        trace_type = "skipped"
    elif source_path and os.path.exists(source_path.replace(".report.json", ".csv")):
        trace_type = "real"
    elif report:
        trace_type = "synthetic"

    return DetectionReport(
        detector_name="fingerprint",
        source_path=source_path,
        trace_type=trace_type,
        transport=transport,
        scenario=scenario_value,
        passed=passed,
        risk_score=risk_score,
        risk_level=risk_level,
        metrics=metrics,
        notes=notes,
        raw=dict(report),
    )


def from_probe_report(report: dict[str, Any],
                      source_path: str | None = None) -> DetectionReport:
    """Create a DetectionReport from a probe resistance report dict.

    Extracts probe-specific metrics (probe_response_variance,
    malformed_close_time_variance) and preserves the full raw dict
    including behavior_summary and per-scenario results.
    """
    notes: list[str] = []
    metrics: list[DetectionMetric] = []

    detector_name = report.get("detector_name", "active_probe_resistance")
    risk_score = report.get("risk_score") or report.get("fingerprint_risk_score")
    risk_level = report.get("risk_level")
    # Use the probe report's inner "raw" as the DetectionReport raw
    raw = dict(report.get("raw", report))

    # Extract probe-specific metrics
    for m in report.get("metrics", []):
        if not isinstance(m, dict):
            continue
        name = m.get("name", "")
        value = m.get("value")
        if name in ("probe_response_variance", "malformed_close_time_variance"):
            metrics.append(DetectionMetric(
                name=name,
                value=value,
                threshold=None,
                passed=True,
                severity="info",
                explanation=m.get("explanation", f"{name}={value}"),
            ))

    # Also extract from raw if metrics not in standard format
    if not any(m.name == "probe_response_variance" for m in metrics):
        pv = report.get("probe_response_variance")
        if pv is not None:
            try:
                metrics.append(DetectionMetric(
                    name="probe_response_variance",
                    value=float(pv),
                    threshold=None,
                    passed=True,
                    severity="info",
                    explanation=f"probe_response_variance={pv}",
                ))
            except (TypeError, ValueError):
                notes.append(f"probe_response_variance not numeric: {pv}")

    if not any(m.name == "malformed_close_time_variance" for m in metrics):
        mcv = report.get("malformed_close_time_variance")
        if mcv is not None:
            try:
                metrics.append(DetectionMetric(
                    name="malformed_close_time_variance",
                    value=float(mcv),
                    threshold=None,
                    passed=True,
                    severity="info",
                    explanation=f"malformed_close_time_variance={mcv}",
                ))
            except (TypeError, ValueError):
                notes.append(f"malformed_close_time_variance not numeric: {mcv}")

    # Report notes
    report_notes = report.get("notes", [])
    if isinstance(report_notes, list):
        notes.extend(report_notes)
    elif isinstance(report_notes, str) and report_notes:
        notes.append(report_notes)

    trace_type = report.get("trace_type", "synthetic")

    return DetectionReport(
        detector_name=detector_name,
        source_path=source_path,
        trace_type=trace_type,
        transport=report.get("transport", "tcp"),
        scenario=report.get("scenario", "probe"),
        passed=report.get("passed", True),
        risk_score=float(risk_score) if risk_score is not None else None,
        risk_level=risk_level,
        metrics=metrics,
        notes=notes,
        raw=raw,
    )


def from_rtt_report(report: dict[str, Any],
                    source_path: str | None = None) -> DetectionReport:
    """Create a DetectionReport from a cross-layer RTT report dict.

    Extracts RTT-specific metrics (app_transport_diff_ms, app_network_diff_ms,
    timing_stability_score, rtt_risk_score) and preserves measurements in raw.
    """
    notes: list[str] = []
    metrics: list[DetectionMetric] = []

    detector_name = report.get("detector_name", "cross_layer_rtt")
    risk_score = report.get("risk_score")
    risk_level = report.get("risk_level")
    raw = dict(report.get("raw", report))

    # Extract RTT metrics from the metrics list
    for m in report.get("metrics", []):
        if not isinstance(m, dict):
            continue
        name = m.get("name", "")
        value = m.get("value")
        if name in ("app_transport_diff_ms", "app_network_diff_ms",
                     "timing_stability_score", "rtt_risk_score",
                     "application_rtt_ms", "transport_rtt_ms", "network_rtt_ms"):
            metrics.append(DetectionMetric(
                name=name,
                value=value,
                threshold=None,
                passed=True,
                severity="info",
                explanation=m.get("explanation", f"{name}={value}"),
            ))

    # Report notes
    report_notes = report.get("notes", [])
    if isinstance(report_notes, list):
        notes.extend(report_notes)
    elif isinstance(report_notes, str) and report_notes:
        notes.append(report_notes)

    trace_type = report.get("trace_type", "synthetic")

    return DetectionReport(
        detector_name=detector_name,
        source_path=source_path,
        trace_type=trace_type,
        transport=report.get("transport", "tcp"),
        scenario=report.get("scenario", "rtt"),
        passed=report.get("passed", True),
        risk_score=float(risk_score) if risk_score is not None else None,
        risk_level=risk_level,
        metrics=metrics,
        notes=notes,
        raw=raw,
    )


def load_detection_report(path: str | Path) -> DetectionReport:
    """Load a fingerprint report JSON and convert to DetectionReport."""
    p = Path(path)
    if not p.is_file():
        return DetectionReport(
            detector_name="fingerprint",
            source_path=str(p),
            trace_type="skipped",
            passed=False,
            notes=[f"report file not found: {path}"],
        )
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return DetectionReport(
            detector_name="fingerprint",
            source_path=str(p),
            trace_type="unknown",
            passed=False,
            notes=[f"failed to read report: {e}"],
        )
    # Route to appropriate factory based on detector_name
    detector_name = raw.get("detector_name", "fingerprint")
    if detector_name in ("active_probe_resistance", "probe"):
        return from_probe_report(raw, source_path=str(p))
    if detector_name == "cross_layer_rtt":
        return from_rtt_report(raw, source_path=str(p))
    return from_fingerprint_report(raw, source_path=str(p))


def detection_report_to_dict(report: DetectionReport) -> dict[str, Any]:
    """Convert a DetectionReport to a JSON-serializable dict."""
    return report.to_dict()
