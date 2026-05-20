"""CountermeasurePolicy — map failed detection metrics to LLM patch directions.

Each mapping is derived from the four detection paper families:
1. OpenVPN-style fingerprint — packet size/direction/timing patterns
2. Encapsulated TLS — ngram, burst, small-packet, TLS-over-TLS shape
3. CalcuLatency — cross-layer RTT, application vs transport latency
4. Passive cross-layer RTT — session misalignment, timing fingerprint
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .detector_report import DetectionMetric, DetectionReport


@dataclass
class CountermeasureHint:
    """A single countermeasure direction for a failed metric."""

    metric_name: str
    problem: str
    recommended_changes: list[str] = field(default_factory=list)
    affected_layers: list[str] = field(default_factory=list)
    tradeoffs: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "metric_name": self.metric_name,
            "problem": self.problem,
            "recommended_changes": self.recommended_changes,
            "affected_layers": self.affected_layers,
            "tradeoffs": self.tradeoffs,
            "avoid": self.avoid,
        }


# ---------------------------------------------------------------------------
# Metric → CountermeasureHint mappings
# ---------------------------------------------------------------------------


_HINT_MAP: dict[str, CountermeasureHint] = {
    "repeated_length_ratio": CountermeasureHint(
        metric_name="repeated_length_ratio",
        problem="High repeated-length ratio — packet sizes are too uniform, "
                "making the traffic shape predictable (OpenVPN/Encapsulated-TLS fingerprint).",
        recommended_changes=[
            "add random padding to frame payloads",
            "implement length bucket randomization",
            "split large frames into variable-size fragments",
            "randomize padding length per frame (not fixed-size)",
        ],
        affected_layers=[
            "frame codec",
            "traffic shaper",
        ],
        tradeoffs=[
            "bandwidth overhead from padding",
            "slight CPU cost for randomization",
        ],
        avoid=[
            "fixed-size padding only (still predictable)",
            "padding that creates new fixed-size patterns",
        ],
    ),
    "small_packet_ratio": CountermeasureHint(
        metric_name="small_packet_ratio",
        problem="High small-packet ratio — many small packets create a "
                "distinctive traffic signature (OpenVPN keepalive/ACK pattern).",
        recommended_changes=[
            "aggregate small frames into larger transport writes",
            "add delayed flush with jittered timer",
            "coalesce heartbeat/keepalive into data frames",
            "batch pending writes before flush",
        ],
        affected_layers=[
            "traffic scheduler",
            "frame codec",
        ],
        tradeoffs=[
            "latency increase from aggregation delay",
            "potential throughput reduction for interactive traffic",
        ],
        avoid=[
            "deterministic flush interval",
            "always flushing after every frame",
        ],
    ),
    "dominant_ngram_ratio": CountermeasureHint(
        metric_name="dominant_ngram_ratio",
        problem="High dominant 3-gram ratio — packet size-direction sequences "
                "repeat in a stable pattern that is easily fingerprintable "
                "(Encapsulated TLS ngram analysis).",
        recommended_changes=[
            "split stable chunks into variable-length segments",
            "add randomized padding to break fixed size patterns",
            "shuffle safe scheduling boundaries",
            "break one-VPN-frame-per-outer-write coupling",
        ],
        affected_layers=[
            "traffic shaper",
            "transport adapter",
        ],
        tradeoffs=[
            "more buffering required",
            "increased complexity in write scheduling",
        ],
        avoid=[
            "one VPN frame equals one outer write (creates stable patterns)",
            "fixed-size chunk boundaries",
        ],
    ),
    "ngram_entropy": CountermeasureHint(
        metric_name="ngram_entropy",
        problem="Low ngram entropy — packet sequences lack diversity, "
                "indicating a deterministic write pattern (Encapsulated TLS).",
        recommended_changes=[
            "randomize chunk sizes within a configurable range",
            "insert dummy frames at randomized intervals",
            "multiplex scheduling to interleave different frame types",
            "add noise to frame timing and sizing",
        ],
        affected_layers=[
            "traffic shaper",
        ],
        tradeoffs=[
            "bandwidth overhead from dummy traffic",
            "latency overhead from scheduling randomization",
        ],
        avoid=[
            "deterministic dummy traffic cadence",
            "predictable interleaving patterns",
        ],
    ),
    "max_burst_size": CountermeasureHint(
        metric_name="max_burst_size",
        problem="High max burst size — large unidirectional bursts are "
                "distinctive and detectable (burst analysis in Encapsulated TLS).",
        recommended_changes=[
            "fragment large writes into smaller paced chunks",
            "add pacing between consecutive writes",
            "smooth burst profile with token-bucket pacing",
            "limit maximum bytes per write batch",
        ],
        affected_layers=[
            "scheduler",
            "transport adapter",
        ],
        tradeoffs=[
            "throughput reduction from pacing",
            "increased RTT for large payloads",
        ],
        avoid=[
            "huge single-direction write bursts",
            "unlimited write batching",
        ],
    ),
    "dominant_burst_direction_ratio": CountermeasureHint(
        metric_name="dominant_burst_direction_ratio",
        problem="High dominant burst direction ratio — traffic is heavily "
                "unidirectional, creating a distinctive asymmetric pattern "
                "(Encapsulated TLS burst analysis).",
        recommended_changes=[
            "add bidirectional pacing to balance direction distribution",
            "use reverse dummy traffic in controlled experiments",
            "schedule periodic reverse-direction writes",
        ],
        affected_layers=[
            "scheduler",
        ],
        tradeoffs=[
            "extra traffic from reverse-direction writes",
            "bandwidth cost of dummy reverse traffic",
        ],
        avoid=[
            "obviously periodic reverse dummy traffic",
            "symmetric dummy traffic that doubles bandwidth",
        ],
    ),
    "avg_inter_arrival_ms": CountermeasureHint(
        metric_name="avg_inter_arrival_ms",
        problem="Overly stable inter-arrival timing — consistent packet spacing "
                "is a detectable timing fingerprint (OpenVPN timing analysis).",
        recommended_changes=[
            "add jitter to inter-packet timing",
            "randomize write timers within a configurable range",
            "use non-uniform scheduling intervals",
        ],
        affected_layers=[
            "scheduler",
        ],
        tradeoffs=[
            "RTT increase from jitter",
            "potential throughput reduction",
        ],
        avoid=[
            "large fixed delays (creates new timing pattern)",
            "constant-interval scheduling",
        ],
    ),
    "rtt_diff_ms": CountermeasureHint(
        metric_name="rtt_diff_ms",
        problem="High RTT difference between application and transport layers — "
                "cross-layer timing discrepancy enables CalcuLatency-style detection.",
        recommended_changes=[
            "implement RTT-aware pacing to align layer latencies",
            "add latency budget tracking in scheduler",
            "evaluate performance tradeoff of latency normalization",
            "reduce transport-layer buffering to minimize artificial delay",
        ],
        affected_layers=[
            "scheduler",
            "evaluation",
        ],
        tradeoffs=[
            "performance cost of latency alignment",
            "may require transport-level buffering changes",
        ],
        avoid=[
            "hiding RTT by adding constant excessive delay",
            "ignoring application-layer latency in scheduling",
        ],
    ),
    "probe_response_variance": CountermeasureHint(
        metric_name="probe_response_variance",
        problem="High variance in probe response behavior — malformed or "
                "unexpected inputs trigger distinct responses, enabling "
                "active probe fingerprinting (OpenVPN behavior analysis).",
        recommended_changes=[
            "unify timeout handling across all input types",
            "implement silent drop for malformed frames",
            "adopt constant close policy regardless of error type",
            "avoid returning distinct error codes to remote peer",
        ],
        affected_layers=[
            "server core",
            "frame decoder",
        ],
        tradeoffs=[
            "harder debugging without distinct error responses",
            "may mask legitimate protocol errors",
        ],
        avoid=[
            "returning distinct errors to remote peer",
            "different timeout durations per error type",
        ],
    ),
    "malformed_close_time_variance": CountermeasureHint(
        metric_name="malformed_close_time_variance",
        problem="Variable close timing on malformed input — different error "
                "types produce different close delays, enabling probe-based "
                "fingerprinting (OpenVPN/DPI behavior analysis).",
        recommended_changes=[
            "use a single, unified close delay for all error types",
            "implement uniform garbage-collection timeout",
            "avoid immediate close for some errors and delayed close for others",
        ],
        affected_layers=[
            "server core",
            "frame decoder",
        ],
        tradeoffs=[
            "slightly slower error recovery",
            "uniform delay may waste resources on trivial errors",
        ],
        avoid=[
            "variable close timing per error type",
            "immediate vs delayed close distinction",
        ],
    ),
    "app_transport_diff_ms": CountermeasureHint(
        metric_name="app_transport_diff_ms",
        problem="High RTT difference between application and transport layers — "
                "cross-layer timing discrepancy enables CalcuLatency-style detection "
                "(application vs transport RTT mismatch).",
        recommended_changes=[
            "implement RTT-aware pacing to align layer latencies",
            "add latency budget tracking in scheduler",
            "reduce transport-layer buffering to minimize artificial delay",
            "evaluate multiplex / scheduler interaction for latency impact",
            "target app-transport diff < 15ms for low-risk profile",
        ],
        affected_layers=[
            "scheduler",
            "transport adapter",
        ],
        tradeoffs=[
            "performance cost of latency alignment",
            "may require transport-level buffering changes",
            "RTT-aware pacing may reduce throughput",
        ],
        avoid=[
            "constant excessive delay as a blanket fix",
            "ignoring application-layer latency in scheduling",
        ],
    ),
    "app_network_diff_ms": CountermeasureHint(
        metric_name="app_network_diff_ms",
        problem="High RTT difference between application and network layers — "
                "cross-layer timing discrepancy across three layers enables "
                "passive cross-layer RTT fingerprinting.",
        recommended_changes=[
            "align application latency profile with network baseline",
            "implement latency budget tracking across all layers",
            "evaluate synthetic delay to match expected network profile",
            "monitor three-layer consistency in scheduler",
        ],
        affected_layers=[
            "scheduler",
            "transport adapter",
        ],
        tradeoffs=[
            "multi-layer latency alignment is complex",
            "may require network-layer awareness in application scheduler",
        ],
        avoid=[
            "large fixed synthetic delays",
            "ignoring network-layer constraints",
        ],
    ),
    "timing_stability_score": CountermeasureHint(
        metric_name="timing_stability_score",
        problem="High timing stability — consistent inter-packet or RTT timing "
                "creates a detectable timing signature (cross-layer RTT analysis, "
                "CalcuLatency timing fingerprint).",
        recommended_changes=[
            "add jitter to inter-packet timing",
            "randomize scheduler intervals within safe bounds",
            "avoid deterministic intervals in all layers",
            "use non-uniform pacing with configurable jitter range",
        ],
        affected_layers=[
            "scheduler",
        ],
        tradeoffs=[
            "jitter increases latency variance",
            "may affect throughput for interactive traffic",
        ],
        avoid=[
            "deterministic timer intervals",
            "constant delay that creates new patterns",
        ],
    ),
    "rtt_risk_score": CountermeasureHint(
        metric_name="rtt_risk_score",
        problem="Overall cross-layer RTT risk score exceeds threshold — "
                "multiple RTT metrics indicate a detectable timing fingerprint "
                "(CalcuLatency + cross-layer RTT combined).",
        recommended_changes=[
            "apply multi-layer timing countermeasures: pacing + jitter + scheduler",
            "prioritize reducing app-transport diff first",
            "re-evaluate after each countermeasure to avoid over-correction",
            "run before/after RTT comparison to validate improvement",
        ],
        affected_layers=[
            "scheduler",
            "transport adapter",
            "evaluation",
        ],
        tradeoffs=[
            "cumulative latency and throughput cost",
            "increased implementation complexity",
        ],
        avoid=[
            "applying all countermeasures at once without measurement",
            "over-engineering timing that creates new patterns",
        ],
    ),
    "fingerprint_risk_score": CountermeasureHint(
        metric_name="fingerprint_risk_score",
        problem="Overall fingerprint risk score exceeds threshold — the "
                "traffic exhibits multiple detectable patterns simultaneously.",
        recommended_changes=[
            "apply multi-layer countermeasures: padding + pacing + jitter",
            "prioritize the highest-contributing metric first",
            "re-evaluate after each countermeasure to avoid over-correction",
            "run before/after fingerprint comparison to validate improvement",
        ],
        affected_layers=[
            "traffic shaper",
            "scheduler",
            "frame codec",
        ],
        tradeoffs=[
            "cumulative bandwidth and latency cost",
            "increased implementation complexity",
        ],
        avoid=[
            "applying all countermeasures at once without measurement",
            "over-engineering that creates new patterns",
        ],
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def suggest_countermeasures(report: DetectionReport) -> list[CountermeasureHint]:
    """Generate countermeasure hints for all failed metrics in a report.

    Only returns hints for metrics that have actually failed (severity == "fail"
    or severity == "warning"). Maps each failed metric to its countermeasure
    direction.

    Args:
        report: An evaluated DetectionReport (after gate evaluation).

    Returns:
        List of CountermeasureHint objects, one per failed metric.
    """
    hints: list[CountermeasureHint] = []
    seen: set[str] = set()

    for metric in report.metrics:
        if metric.passed:
            continue

        hint = _HINT_MAP.get(metric.name)
        if hint is not None and hint.metric_name not in seen:
            hints.append(hint)
            seen.add(hint.metric_name)

    # If overall risk_score is a problem but no specific metric failed,
    # still add the risk_score hint
    if not hints and not report.passed and report.risk_score is not None:
        hint = _HINT_MAP.get("fingerprint_risk_score")
        if hint and hint.metric_name not in seen:
            hints.append(hint)

    return hints


def get_hint_for_metric(metric_name: str) -> CountermeasureHint | None:
    """Look up a countermeasure hint by metric name."""
    return _HINT_MAP.get(metric_name)
