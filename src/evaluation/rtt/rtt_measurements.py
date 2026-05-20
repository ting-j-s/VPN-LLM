"""RTT measurement and cross-layer RTT scoring.

Defines the core data structures for RTT samples, cross-layer RTT reports,
and the scoring functions used by the DetectionGate.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class RTTMeasurement:
    """Per-layer RTT measurement summary.

    Attributes:
        name: Human-readable label (e.g. "app_echo", "tcp_connect").
        layer: "application" | "transport" | "network" | "synthetic".
        samples_ms: Raw samples in milliseconds.
        min_ms, median_ms, avg_ms, max_ms: Summary statistics.
        sample_count: Number of valid samples.
        notes: Per-measurement annotations.
    """

    name: str
    layer: str
    samples_ms: list[float] = field(default_factory=list)
    min_ms: float | None = None
    median_ms: float | None = None
    avg_ms: float | None = None
    max_ms: float | None = None
    sample_count: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "layer": self.layer,
            "min_ms": self.min_ms,
            "median_ms": self.median_ms,
            "avg_ms": self.avg_ms,
            "max_ms": self.max_ms,
            "sample_count": self.sample_count,
            "notes": self.notes,
        }


@dataclass
class CrossLayerRTTReport:
    """Cross-layer RTT evaluation report.

    Compatible with DetectionReport loading via from_rtt_report().
    """

    detector_name: str = "cross_layer_rtt"
    target: str = "127.0.0.1"
    trace_type: str = "mock"  # "real" | "synthetic" | "mock"
    application_rtt_ms: float | None = None
    transport_rtt_ms: float | None = None
    network_rtt_ms: float | None = None
    app_transport_diff_ms: float | None = None
    app_network_diff_ms: float | None = None
    timing_stability_score: float | None = None
    risk_score: float = 0.0
    risk_level: str = "low"  # "low" | "medium" | "high" | "insufficient_data"
    measurements: list[RTTMeasurement] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "detector_name": self.detector_name,
            "target": self.target,
            "trace_type": self.trace_type,
            "application_rtt_ms": self.application_rtt_ms,
            "transport_rtt_ms": self.transport_rtt_ms,
            "network_rtt_ms": self.network_rtt_ms,
            "app_transport_diff_ms": self.app_transport_diff_ms,
            "app_network_diff_ms": self.app_network_diff_ms,
            "timing_stability_score": self.timing_stability_score,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "measurements": [m.to_dict() for m in self.measurements],
            "notes": self.notes,
            "raw": self.raw,
        }


# ---------------------------------------------------------------------------
# Construction helpers
# ---------------------------------------------------------------------------


def summarize_samples(
    name: str,
    layer: str,
    samples_ms: list[float],
    notes: list[str] | None = None,
) -> RTTMeasurement:
    """Compute min/median/avg/max from a list of RTT samples.

    Returns an RTTMeasurement with all statistics populated.
    An empty sample list produces sample_count=0 and None statistics.
    """
    if not samples_ms:
        return RTTMeasurement(
            name=name,
            layer=layer,
            sample_count=0,
            notes=list(notes or []),
        )

    sorted_samples = sorted(samples_ms)
    n = len(sorted_samples)
    if n % 2 == 1:
        median = sorted_samples[n // 2]
    else:
        median = (sorted_samples[n // 2 - 1] + sorted_samples[n // 2]) / 2.0

    return RTTMeasurement(
        name=name,
        layer=layer,
        samples_ms=list(samples_ms),
        min_ms=round(min(sorted_samples), 3),
        median_ms=round(median, 3),
        avg_ms=round(statistics.mean(sorted_samples), 3),
        max_ms=round(max(sorted_samples), 3),
        sample_count=n,
        notes=list(notes or []),
    )


def compute_rtt_diff(
    app_ms: float | None,
    transport_ms: float | None,
    network_ms: float | None = None,
) -> dict:
    """Compute cross-layer RTT differences.

    Returns a dict with app_transport_diff_ms and optionally app_network_diff_ms.
    """
    result: dict = {
        "app_transport_diff_ms": None,
        "app_network_diff_ms": None,
    }
    if app_ms is not None and transport_ms is not None:
        result["app_transport_diff_ms"] = round(abs(app_ms - transport_ms), 3)
    if app_ms is not None and network_ms is not None:
        result["app_network_diff_ms"] = round(abs(app_ms - network_ms), 3)
    return result


def score_cross_layer_rtt(
    diff_ms: float | None,
    timing_stability: float | None = None,
    threshold_ms: float = 50.0,
) -> tuple[float, str]:
    """Score cross-layer RTT risk.

    Rules:
      - diff_ms is None: insufficient_data
      - diff_ms < 15ms: low
      - 15ms <= diff_ms < threshold_ms (default 50ms): medium
      - diff_ms >= threshold_ms: high

    The timing_stability score (0-1, higher = more stable = more detectable)
    can elevate a borderline medium to high.

    Returns (risk_score 0-1, risk_level).
    """
    if diff_ms is None:
        return 0.0, "insufficient_data"

    if diff_ms < 15.0:
        level = "low"
        base_score = diff_ms / 15.0 * 0.3  # 0 to 0.3
    elif diff_ms < threshold_ms:
        level = "medium"
        base_score = 0.3 + (diff_ms - 15.0) / (threshold_ms - 15.0) * 0.3  # 0.3 to 0.6
    else:
        level = "high"
        base_score = 0.6 + min((diff_ms - threshold_ms) / threshold_ms, 1.0) * 0.4

    base_score = min(base_score, 1.0)

    # Elevate by timing_stability if available
    if timing_stability is not None and timing_stability > 0.7 and level == "medium":
        level = "high"
        base_score = max(base_score, 0.6)

    return round(base_score, 4), level


def timing_stability_score(samples_ms: list[float]) -> float | None:
    """Compute timing stability score (0-1).

    Higher = more stable (less variance), which is worse for fingerprinting.
    Lower = more variable, which is better for evasion.

    Uses the coefficient of variation (std / mean). A CV <= 0.05 gives
    a score of 1.0 (very stable = detectable). CV >= 0.5 gives 0.0.
    Returns None if there are fewer than 2 samples.
    """
    if len(samples_ms) < 2:
        return None

    mean = statistics.mean(samples_ms)
    if mean <= 0:
        return None

    stdev = statistics.stdev(samples_ms)
    cv = stdev / mean

    # Map CV to [0, 1]: cv=0 → 1.0, cv=0.5 → 0.0
    score = 1.0 - min(cv / 0.5, 1.0)
    return round(score, 4)
