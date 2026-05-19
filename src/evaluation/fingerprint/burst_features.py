"""Burst-based traffic features for VPN fingerprintability evaluation.

A **burst** is defined as a consecutive run of packets in the same direction
where each adjacent inter-arrival gap does not exceed *max_gap_ms*.
Direction changes and gaps > *max_gap_ms* both trigger a new burst.

Burst patterns (large downstream bursts, tight directional alternation)
can expose tunnel-internal handshake shapes even when individual packet
sizes are obfuscated.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Sequence

from .pcap_features import FlowPacket


@dataclass
class Burst:
    """A single burst — consecutive same-direction packets within max_gap_ms."""

    direction: str
    packet_count: int
    total_length: int
    start_timestamp: float
    end_timestamp: float
    duration_ms: float = 0.0

    def __post_init__(self) -> None:
        if self.duration_ms == 0.0 and self.start_timestamp <= self.end_timestamp:
            self.duration_ms = (self.end_timestamp - self.start_timestamp) * 1000.0


def extract_bursts(
    packets: Sequence[FlowPacket],
    max_gap_ms: float = 10.0,
) -> list[Burst]:
    """Partition a packet sequence into bursts.

    Rules:
    - Packets are sorted by timestamp first.
    - Same direction + gap <= max_gap_ms → same burst.
    - Direction change → new burst.
    - gap > max_gap_ms → new burst.
    """
    if not packets:
        return []

    sorted_packets = sorted(packets, key=lambda p: p.timestamp)
    max_gap_s = max_gap_ms / 1000.0

    bursts: list[Burst] = []
    current_dir = sorted_packets[0].direction
    current_start = sorted_packets[0].timestamp
    current_end = sorted_packets[0].timestamp
    current_count = 1
    current_total_len = sorted_packets[0].length

    for i in range(1, len(sorted_packets)):
        p = sorted_packets[i]
        gap = p.timestamp - sorted_packets[i - 1].timestamp
        same_dir = p.direction == current_dir
        within_gap = gap <= max_gap_s

        if same_dir and within_gap:
            current_count += 1
            current_total_len += p.length
            current_end = p.timestamp
        else:
            bursts.append(
                Burst(
                    direction=current_dir,
                    packet_count=current_count,
                    total_length=current_total_len,
                    start_timestamp=current_start,
                    end_timestamp=current_end,
                )
            )
            current_dir = p.direction
            current_start = p.timestamp
            current_end = p.timestamp
            current_count = 1
            current_total_len = p.length

    bursts.append(
        Burst(
            direction=current_dir,
            packet_count=current_count,
            total_length=current_total_len,
            start_timestamp=current_start,
            end_timestamp=current_end,
        )
    )

    return bursts


def summarize_bursts(bursts: Sequence[Burst]) -> dict[str, Any]:
    """Compute summary statistics over a list of bursts.

    Returns dict with:
        burst_count, burst_total_lengths, burst_directions,
        avg_burst_size, median_burst_size, p95_burst_size, max_burst_size,
        avg_burst_packet_count, max_burst_packet_count,
        c2s_burst_count, s2c_burst_count,
        direction_switch_count_between_bursts, dominant_burst_direction_ratio.
    """
    n = len(bursts)
    if n == 0:
        return {
            "burst_count": 0,
            "burst_total_lengths": [],
            "burst_directions": [],
            "avg_burst_size": 0.0,
            "median_burst_size": 0.0,
            "p95_burst_size": 0.0,
            "max_burst_size": 0,
            "avg_burst_packet_count": 0.0,
            "max_burst_packet_count": 0,
            "c2s_burst_count": 0,
            "s2c_burst_count": 0,
            "direction_switch_count_between_bursts": 0,
            "dominant_burst_direction_ratio": 0.0,
        }

    burst_sizes = [b.total_length for b in bursts]
    packet_counts = [b.packet_count for b in bursts]
    c2s_count = sum(1 for b in bursts if b.direction == "C2S")
    s2c_count = n - c2s_count

    sorted_sizes = sorted(burst_sizes)
    p95_idx = max(0, int(len(sorted_sizes) * 0.95 + 0.5) - 1)
    p95_idx = min(p95_idx, len(sorted_sizes) - 1)

    direction_switches = sum(
        1
        for i in range(1, len(bursts))
        if bursts[i].direction != bursts[i - 1].direction
    )

    dominant_ratio = max(c2s_count, s2c_count) / n if n > 0 else 0.0

    return {
        "burst_count": n,
        "burst_total_lengths": burst_sizes,
        "burst_directions": [b.direction for b in bursts],
        "avg_burst_size": round(statistics.mean(burst_sizes), 2),
        "median_burst_size": statistics.median(sorted_sizes),
        "p95_burst_size": sorted_sizes[p95_idx],
        "max_burst_size": max(burst_sizes),
        "avg_burst_packet_count": round(statistics.mean(packet_counts), 2),
        "max_burst_packet_count": max(packet_counts),
        "c2s_burst_count": c2s_count,
        "s2c_burst_count": s2c_count,
        "direction_switch_count_between_bursts": direction_switches,
        "dominant_burst_direction_ratio": round(dominant_ratio, 4),
    }


def extract_burst_features(
    packets: Sequence[FlowPacket],
    max_gap_ms: float = 10.0,
) -> dict[str, Any]:
    """Compute burst-based features from a packet sequence.

    Convenience wrapper: extract + summarize.
    """
    bursts = extract_bursts(packets, max_gap_ms=max_gap_ms)
    return summarize_bursts(bursts)
