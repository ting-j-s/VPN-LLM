"""Cross-Layer RTT Evaluation Gate.

Local controlled evaluation of application / transport / network-layer RTT
differences for cross-layer timing fingerprint detection.

Inspired by CalcuLatency (application vs transport RTT) and cross-layer RTT
passive detection papers.

This module:
- Collects RTT samples per layer (mock, TCP, optional ICMP)
- Computes cross-layer RTT differences
- Scores timing stability and risk
- Produces a report compatible with DetectionGate / CountermeasurePolicy / LLM patch loop

SECURITY: All targets are restricted to localhost. No third-party scanning.
"""

from .rtt_measurements import (
    RTTMeasurement,
    CrossLayerRTTReport,
    summarize_samples,
    compute_rtt_diff,
    score_cross_layer_rtt,
    timing_stability_score,
)

from .rtt_runner import (
    MockRTTRunner,
    LocalTCPRTTRunner,
    OptionalPingRunner,
    create_rtt_runner,
)

__all__ = [
    "RTTMeasurement",
    "CrossLayerRTTReport",
    "summarize_samples",
    "compute_rtt_diff",
    "score_cross_layer_rtt",
    "timing_stability_score",
    "MockRTTRunner",
    "LocalTCPRTTRunner",
    "OptionalPingRunner",
    "create_rtt_runner",
]
