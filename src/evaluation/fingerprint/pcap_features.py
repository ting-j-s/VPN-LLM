"""Local offline traffic feature extraction for VPN-LLM fingerprintability evaluation.

Reads CSV trace files and computes explainable flow-level features
without using machine learning.  The risk score is derived from
heuristic rules inspired by the 1OpenVPN.pdf analysis:

  - packet length distribution (repeating / small packets)
  - inter-arrival time regularity
  - direction-switch pattern stability
  - burst behaviour
  - 3-gram signed-length-sequence patterns (2Fingerprinting paper)
  - burst-direction aggregation (2Fingerprinting paper)
"""

from __future__ import annotations

import csv
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

# burst/ngram imports are deferred into extract_features() to avoid
# circular imports: burst_features / ngram_features import FlowPacket
# from this module.

_VALID_DIRECTIONS = frozenset({"C2S", "S2C"})


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FlowPacket:
    """A single packet record parsed from a CSV trace.

    Attributes:
        timestamp: seconds (float or float-parseable).
        src: source address string.
        dst: destination address string.
        src_port: source port.
        dst_port: destination port.
        proto: protocol label (tcp / udp / ws / tls / ssh / …).
        length: observable packet length in bytes.
        direction: ``C2S`` (client→server) or ``S2C`` (server→client).
    """

    timestamp: float
    src: str
    dst: str
    src_port: int
    dst_port: int
    proto: str
    length: int
    direction: str

    def __post_init__(self) -> None:
        if self.direction not in _VALID_DIRECTIONS:
            raise ValueError(
                f"direction must be one of {sorted(_VALID_DIRECTIONS)}, "
                f"got {self.direction!r}"
            )


@dataclass
class FlowFeatures:
    """Explainable flow-level features extracted from a sequence of packets.

    All inter-arrival metrics are in milliseconds.  ``first_n_lengths``
    and ``first_n_directions`` capture only the first *N* packets of the
    flow (default *N* = 30) so that early-handshake stability can be
    inspected.
    """

    packet_count: int
    first_n_lengths: list[int] = field(default_factory=list)
    first_n_directions: list[str] = field(default_factory=list)
    small_packet_ratio: float = 0.0
    repeated_length_ratio: float = 0.0
    unique_length_count: int = 0
    avg_inter_arrival_ms: float = 0.0
    median_inter_arrival_ms: float = 0.0
    stdev_inter_arrival_ms: float = 0.0
    max_burst_packets_10ms: int = 0
    direction_switch_count: int = 0
    c2s_packet_count: int = 0
    s2c_packet_count: int = 0
    fingerprint_risk_score: float = 0.0
    risk_level: str = "insufficient_data"
    notes: list[str] = field(default_factory=list)
    # ---- 3-gram features ---------------------------------------------------
    signed_sizes: list[int] = field(default_factory=list)
    bucket_sequence: list[str] = field(default_factory=list)
    ngram_entropy: float = 0.0
    unique_ngram_count: int = 0
    dominant_ngram_ratio: float = 0.0
    top_ngrams: list[dict[str, Any]] = field(default_factory=list)
    # ---- burst features ----------------------------------------------------
    burst_count: int = 0
    burst_total_lengths: list[int] = field(default_factory=list)
    burst_directions: list[str] = field(default_factory=list)
    avg_burst_size: float = 0.0
    median_burst_size: float = 0.0
    p95_burst_size: float = 0.0
    max_burst_size: int = 0
    dominant_burst_direction_ratio: float = 0.0


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------


def load_trace_csv(path: str | Path) -> list[FlowPacket]:
    """Load a CSV trace file into a list of :class:`FlowPacket`.

    Expected CSV columns (header row required)::

        timestamp,src,dst,src_port,dst_port,proto,length,direction

    The ``timestamp`` column is cast to :func:`float`.  ``src_port`` and
    ``dst_port`` are cast to :class:`int`.  ``direction`` is validated
    against ``C2S`` / ``S2C``.
    """
    path = Path(path)
    packets: list[FlowPacket] = []

    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"CSV file {path} has no header row")

        for row in reader:
            direction = row.get("direction", "").strip()
            if direction not in _VALID_DIRECTIONS:
                raise ValueError(
                    f"Row {reader.line_num}: direction must be C2S or S2C, "
                    f"got {direction!r}"
                )
            packets.append(
                FlowPacket(
                    timestamp=float(row["timestamp"]),
                    src=row.get("src", "").strip(),
                    dst=row.get("dst", "").strip(),
                    src_port=int(row["src_port"]),
                    dst_port=int(row["dst_port"]),
                    proto=row.get("proto", "").strip(),
                    length=int(row["length"]),
                    direction=direction,
                )
            )

    return packets


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def extract_features(
    packets: Sequence[FlowPacket],
    first_n: int = 30,
    small_packet_threshold: int = 128,
    ngram_n: int = 3,
    burst_gap_ms: float = 10.0,
) -> FlowFeatures:
    """Extract explainable flow features from a packet sequence.

    Packets are sorted by ``timestamp`` before extraction so that an
    unsorted trace is handled deterministically.

    Returns:
        :class:`FlowFeatures` with computed fields.
    """
    if not packets:
        return FlowFeatures(
            packet_count=0,
            notes=["empty trace — no packets to analyse"],
        )

    sorted_packets: list[FlowPacket] = sorted(packets, key=lambda p: p.timestamp)
    n = len(sorted_packets)

    # ---- first-N snapshot ------------------------------------------------
    head = sorted_packets[:first_n]
    first_n_lengths = [p.length for p in head]
    first_n_directions = [p.direction for p in head]

    # ---- length-based features -------------------------------------------
    all_lengths = [p.length for p in sorted_packets]
    small_count = sum(1 for l in all_lengths if l < small_packet_threshold)
    small_packet_ratio = small_count / n

    length_counts: dict[int, int] = {}
    for l in all_lengths:
        length_counts[l] = length_counts.get(l, 0) + 1
    repeated_length_count = sum(c for c in length_counts.values() if c > 1)
    repeated_length_ratio = repeated_length_count / n if n else 0.0
    unique_length_count = len(length_counts)

    # ---- inter-arrival (ms) ----------------------------------------------
    inter_arrivals_ms: list[float] = []
    for i in range(1, n):
        delta_s = sorted_packets[i].timestamp - sorted_packets[i - 1].timestamp
        inter_arrivals_ms.append(max(delta_s, 0.0) * 1000.0)

    if inter_arrivals_ms:
        avg_inter_arrival_ms = statistics.mean(inter_arrivals_ms)
        median_inter_arrival_ms = statistics.median(inter_arrivals_ms)
        stdev_inter_arrival_ms = (
            statistics.stdev(inter_arrivals_ms) if len(inter_arrivals_ms) >= 2 else 0.0
        )
    else:
        avg_inter_arrival_ms = 0.0
        median_inter_arrival_ms = 0.0
        stdev_inter_arrival_ms = 0.0

    # ---- burst: max packets in any 10 ms window --------------------------
    max_burst = _compute_max_burst_10ms(sorted_packets)

    # ---- direction -------------------------------------------------------
    c2s_count = sum(1 for p in sorted_packets if p.direction == "C2S")
    s2c_count = n - c2s_count

    direction_switches = sum(
        1
        for i in range(1, len(first_n_directions))
        if first_n_directions[i] != first_n_directions[i - 1]
    )
    direction_switch_count = direction_switches

    # ---- 3-gram features ---------------------------------------------------
    from .ngram_features import extract_ngram_features  # deferred — circular import

    ngram_feats = extract_ngram_features(sorted_packets, n=ngram_n)
    dominant_ngram_ratio = ngram_feats["dominant_ngram_ratio"]
    ngram_entropy_val = ngram_feats["ngram_entropy"]
    unique_ngram_count = ngram_feats["unique_ngram_count"]

    # ---- burst aggregation features ----------------------------------------
    from .burst_features import extract_bursts, summarize_bursts  # deferred — circular import

    burst_list = extract_bursts(sorted_packets, max_gap_ms=burst_gap_ms)
    burst_summary = summarize_bursts(burst_list)

    # ---- risk score (explainable rules, no ML) ---------------------------
    risk_score, risk_level, notes = _compute_risk(
        n=n,
        small_packet_ratio=small_packet_ratio,
        repeated_length_ratio=repeated_length_ratio,
        avg_inter_arrival_ms=avg_inter_arrival_ms,
        stdev_inter_arrival_ms=stdev_inter_arrival_ms,
        max_burst=max_burst,
        first_n_directions=first_n_directions,
        direction_switches=direction_switches,
        inter_arrivals_ms=inter_arrivals_ms,
        dominant_ngram_ratio=dominant_ngram_ratio,
        ngram_entropy=ngram_entropy_val,
        unique_ngram_count=unique_ngram_count,
        burst_count=burst_summary["burst_count"],
        dominant_burst_direction_ratio=burst_summary["dominant_burst_direction_ratio"],
        max_burst_size=burst_summary["max_burst_size"],
        avg_burst_size=burst_summary["avg_burst_size"],
    )

    return FlowFeatures(
        packet_count=n,
        first_n_lengths=first_n_lengths,
        first_n_directions=first_n_directions,
        small_packet_ratio=round(small_packet_ratio, 4),
        repeated_length_ratio=round(repeated_length_ratio, 4),
        unique_length_count=unique_length_count,
        avg_inter_arrival_ms=round(avg_inter_arrival_ms, 3),
        median_inter_arrival_ms=round(median_inter_arrival_ms, 3),
        stdev_inter_arrival_ms=round(stdev_inter_arrival_ms, 3),
        max_burst_packets_10ms=max_burst,
        direction_switch_count=direction_switch_count,
        c2s_packet_count=c2s_count,
        s2c_packet_count=s2c_count,
        fingerprint_risk_score=round(risk_score, 4),
        risk_level=risk_level,
        notes=notes,
        signed_sizes=ngram_feats["signed_sizes"],
        bucket_sequence=ngram_feats["bucket_sequence"],
        ngram_entropy=ngram_entropy_val,
        unique_ngram_count=unique_ngram_count,
        dominant_ngram_ratio=round(dominant_ngram_ratio, 4),
        top_ngrams=ngram_feats["top_ngrams"],
        burst_count=burst_summary["burst_count"],
        burst_total_lengths=burst_summary["burst_total_lengths"],
        burst_directions=burst_summary["burst_directions"],
        avg_burst_size=burst_summary["avg_burst_size"],
        median_burst_size=burst_summary["median_burst_size"],
        p95_burst_size=burst_summary["p95_burst_size"],
        max_burst_size=burst_summary["max_burst_size"],
        dominant_burst_direction_ratio=burst_summary["dominant_burst_direction_ratio"],
    )


# ---------------------------------------------------------------------------
# High-level summary
# ---------------------------------------------------------------------------


def summarize_trace(
    path: str | Path,
    first_n: int = 30,
    small_packet_threshold: int = 128,
    ngram_n: int = 3,
    burst_gap_ms: float = 10.0,
) -> dict[str, Any]:
    """Load a CSV trace and return a JSON-serialisable summary dict.

    Convenience wrapper around :func:`load_trace_csv` +
    :func:`extract_features`.
    """
    packets = load_trace_csv(path)
    features = extract_features(
        packets,
        first_n=first_n,
        small_packet_threshold=small_packet_threshold,
        ngram_n=ngram_n,
        burst_gap_ms=burst_gap_ms,
    )
    result: dict[str, Any] = {
        "trace_path": str(Path(path).resolve()),
        "packet_count": features.packet_count,
        "first_n_lengths": features.first_n_lengths,
        "first_n_directions": features.first_n_directions,
        "small_packet_ratio": features.small_packet_ratio,
        "repeated_length_ratio": features.repeated_length_ratio,
        "unique_length_count": features.unique_length_count,
        "avg_inter_arrival_ms": features.avg_inter_arrival_ms,
        "median_inter_arrival_ms": features.median_inter_arrival_ms,
        "stdev_inter_arrival_ms": features.stdev_inter_arrival_ms,
        "max_burst_packets_10ms": features.max_burst_packets_10ms,
        "direction_switch_count": features.direction_switch_count,
        "c2s_packet_count": features.c2s_packet_count,
        "s2c_packet_count": features.s2c_packet_count,
        "fingerprint_risk_score": features.fingerprint_risk_score,
        "risk_level": features.risk_level,
        "notes": features.notes,
        # 3-gram
        "signed_sizes": features.signed_sizes,
        "bucket_sequence": features.bucket_sequence,
        "ngram_entropy": features.ngram_entropy,
        "unique_ngram_count": features.unique_ngram_count,
        "dominant_ngram_ratio": features.dominant_ngram_ratio,
        "top_ngrams": features.top_ngrams,
        # burst
        "burst_count": features.burst_count,
        "burst_total_lengths": features.burst_total_lengths,
        "burst_directions": features.burst_directions,
        "avg_burst_size": features.avg_burst_size,
        "median_burst_size": features.median_burst_size,
        "p95_burst_size": features.p95_burst_size,
        "max_burst_size": features.max_burst_size,
        "dominant_burst_direction_ratio": features.dominant_burst_direction_ratio,
    }
    return result


# ===================================================================
# Internal helpers
# ===================================================================


def _compute_max_burst_10ms(packets: list[FlowPacket]) -> int:
    """Return the maximum number of packets falling in any 10 ms window."""
    if len(packets) < 2:
        return len(packets)

    timestamps = [p.timestamp for p in packets]
    n = len(timestamps)
    max_burst = 1
    j = 0

    for i in range(n):
        window_end = timestamps[i] + 0.010  # 10 ms
        while j < n and timestamps[j] <= window_end:
            j += 1
        count_in_window = j - i
        if count_in_window > max_burst:
            max_burst = count_in_window

    return max_burst


def _compute_risk(
    *,
    n: int,
    small_packet_ratio: float,
    repeated_length_ratio: float,
    avg_inter_arrival_ms: float,
    stdev_inter_arrival_ms: float,
    max_burst: int,
    first_n_directions: list[str],
    direction_switches: int,
    inter_arrivals_ms: list[float],
    dominant_ngram_ratio: float = 0.0,
    ngram_entropy: float = 0.0,
    unique_ngram_count: int = 0,
    burst_count: int = 0,
    dominant_burst_direction_ratio: float = 0.0,
    max_burst_size: int = 0,
    avg_burst_size: float = 0.0,
) -> tuple[float, str, list[str]]:
    """Compute an explainable fingerprint risk score.

    Returns ``(score, level, notes)``.
    """
    notes: list[str] = []

    # Rule 1: insufficient data
    if n < 5:
        return 0.0, "insufficient_data", ["packet_count < 5 — not enough data"]

    # ---- factor 1: repeated length ratio (weight 0.20) -------------------
    # High repeated_length_ratio means many packets share the same length.
    f_repeat = min(repeated_length_ratio / 0.7, 1.0)

    # ---- factor 2: small packet ratio (weight 0.16) ----------------------
    f_small = min(small_packet_ratio / 0.6, 1.0)

    # ---- factor 3: inter-arrival regularity (weight 0.16) ----------------
    # Low coefficient of variation → very regular timing.
    if avg_inter_arrival_ms > 0 and len(inter_arrivals_ms) >= 2:
        cv = stdev_inter_arrival_ms / avg_inter_arrival_ms
    else:
        cv = 0.0
    # cv < 0.3 is quite regular; cv >= 1.0 is noisy
    f_timing = max(1.0 - min(cv / 1.0, 1.0), 0.0)

    # ---- factor 4: direction-switch fixedness (weight 0.16) --------------
    # switch_fraction close to 0 (all one direction) or 1 (alternating
    # every packet) are both fingerprintable; ~0.5 looks random.
    head_count = len(first_n_directions)
    if head_count >= 2:
        max_possible = head_count - 1
        switch_frac = direction_switches / max_possible
        # Distance from 0.5 — farther = more fingerprintable
        f_dir = abs(switch_frac - 0.5) * 2.0
    else:
        f_dir = 0.5

    # ---- factor 5: packet-level burst (weight 0.12) -----------------------
    # max_burst > 5 in a 10 ms window is quite bursty.
    f_burst = min(max_burst / 8.0, 1.0)

    # ---- factor 6: ngram pattern (weight 0.10) ----------------------------
    # High dominant_ngram_ratio → repetitive (size,dir) patterns.
    # Low ngram_entropy → low variability in the sequence.
    # Note: unique_ngram_count == 1 means every 3-gram is identical.
    f_ngram_dom = min(dominant_ngram_ratio / 0.4, 1.0) if ngram_entropy > 0 else 0.0
    f_ngram_ent = max(1.0 - min(ngram_entropy / 3.0, 1.0), 0.0) if ngram_entropy > 0 else 0.0
    f_ngram = 0.5 * f_ngram_dom + 0.5 * f_ngram_ent

    # ---- factor 7: burst-direction pattern (weight 0.10) ------------------
    # High dominant_burst_direction_ratio → traffic heavily biased to one side.
    # Large max_burst_size relative to avg → bursty bulk transfer pattern.
    f_burst_dir = min(dominant_burst_direction_ratio / 0.8, 1.0)
    if avg_burst_size > 0 and max_burst_size > 0:
        burst_ratio = max_burst_size / max(avg_burst_size, 1.0)
        f_burst_size = min(burst_ratio / 5.0, 1.0)
    else:
        f_burst_size = 0.0
    f_burst_pattern = 0.5 * f_burst_dir + 0.5 * f_burst_size

    # ---- weighted score --------------------------------------------------
    score = (
        0.20 * f_repeat
        + 0.16 * f_small
        + 0.16 * f_timing
        + 0.16 * f_dir
        + 0.12 * f_burst
        + 0.10 * f_ngram
        + 0.10 * f_burst_pattern
    )

    # Build human-readable notes
    if f_repeat > 0.5:
        notes.append(f"high repeated-length ratio ({repeated_length_ratio:.3f})")
    if f_small > 0.5:
        notes.append(f"high small-packet ratio ({small_packet_ratio:.3f})")
    if f_timing > 0.5:
        notes.append(
            f"regular inter-arrival timing (cv={cv:.3f}, avg={avg_inter_arrival_ms:.1f}ms)"
        )
    if f_dir > 0.5:
        notes.append(
            f"fixed direction-switch pattern (switches={direction_switches}/{max_possible if head_count >= 2 else 0})"
        )
    if f_burst > 0.5:
        notes.append(f"high packet-level burst — {max_burst} packets in 10 ms window")
    if f_ngram > 0.5:
        notes.append(
            f"stable 3-gram pattern (dominant_ratio={dominant_ngram_ratio:.3f}, entropy={ngram_entropy:.3f})"
        )
    if f_burst_pattern > 0.5:
        notes.append(
            f"distinctive burst profile (dir_ratio={dominant_burst_direction_ratio:.3f}, max_burst={max_burst_size}B)"
        )

    # Determine level
    if score < 0.35:
        level = "low"
    elif score < 0.70:
        level = "medium"
    else:
        level = "high"

    if not notes:
        notes.append("no strong fingerprintability indicators detected")

    return score, level, notes
