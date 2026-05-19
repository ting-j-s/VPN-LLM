#!/usr/bin/env python3
"""Synthetic before/after trace comparison for Phase 5C shaping validation.

Generates simulated traffic, applies shaping, and compares fingerprint metrics.
Marked SYNTHETIC — not from real network capture.
"""

import json
import math
import random
import statistics
from collections import Counter

# Re-use real shaping code
from src.shaping.config import ShapingConfig
from src.shaping.factory import create_traffic_shaper


def packet_sizes_series(lengths: list[int], shaper) -> list[int]:
    """Run a series of packet sizes through the shaper and collect chunk sizes."""
    sized: list[int] = []
    for n in lengths:
        frame = b"\x00" * n  # placeholder payload
        chunks = shaper.encode_frame(frame)
        if not chunks:
            continue  # buffered by aggregation
        for ch in chunks:
            sized.append(len(ch.data))
    # Flush remaining
    for ch in shaper.flush():
        sized.append(len(ch.data))
    return sized


def compute_basic_metrics(sizes: list[int]) -> dict:
    """Compute fingerprint-relevant metrics from chunk sizes."""
    n = len(sizes)
    if n == 0:
        return {"packet_count": 0}

    counter = Counter(sizes)
    most_common_count = counter.most_common(1)[0][1] if counter else 0
    repeated_length_ratio = most_common_count / n if n else 0
    small_count = sum(1 for s in sizes if s < 100)
    small_packet_ratio = small_count / n if n else 0
    unique_count = len(counter)

    # Simplified ngram entropy from direction-independent size sequence
    # Use 2-gram of size buckets (small=0, medium=1, large=2)
    buckets = [0 if s < 100 else (1 if s < 500 else 2) for s in sizes]
    bigrams: dict[tuple[int, int], int] = {}
    for i in range(len(buckets) - 1):
        pair = (buckets[i], buckets[i + 1])
        bigrams[pair] = bigrams.get(pair, 0) + 1
    total_bigrams = sum(bigrams.values())
    ngram_entropy = 0.0
    dominant_ngram_ratio = 0.0
    if total_bigrams > 0:
        dominant_ngram_ratio = max(bigrams.values()) / total_bigrams
        ngram_entropy = 0.0
        for count in bigrams.values():
            p = count / total_bigrams
            ngram_entropy -= p * math.log2(p)

    # Simplified risk score (same weights as detector)
    risk_score = round(
        0.25 * repeated_length_ratio
        + 0.25 * small_packet_ratio
        + 0.15 * dominant_ngram_ratio
        + 0.15 * (1 - min(ngram_entropy / 4.0, 1.0))
        + 0.10 * (1 - min(unique_count / max(n, 1), 1.0))
        + 0.10 * min(1.0, n / 50),  # more packets = more data for detector
        4,
    )

    risk_level = "low" if risk_score < 0.3 else ("medium" if risk_score < 0.6 else "high")

    return {
        "packet_count": n,
        "repeated_length_ratio": round(repeated_length_ratio, 4),
        "small_packet_ratio": round(small_packet_ratio, 4),
        "unique_length_count": unique_count,
        "ngram_entropy": round(ngram_entropy, 4),
        "dominant_ngram_ratio": round(dominant_ngram_ratio, 4),
        "fingerprint_risk_score": risk_score,
        "risk_level": risk_level,
    }


def main():
    rng = random.Random(42)

    # Simulate real traffic patterns: mix of small packets (ACK, control) and
    # medium packets (data segments). Based on typical VPN TUN traffic.
    # ~60 small packets (40-80 bytes), ~30 medium (200-800), ~10 large (1200-1400)
    small = [rng.randint(40, 80) for _ in range(60)]
    medium = [rng.randint(200, 800) for _ in range(30)]
    large = [rng.randint(1200, 1400) for _ in range(10)]
    all_sizes = small + medium + large
    rng.shuffle(all_sizes)

    # ---- Before (Noop) ----
    noop_shaper = create_traffic_shaper(ShapingConfig(), seed=42)
    noop_sizes = packet_sizes_series(all_sizes, noop_shaper)
    before = compute_basic_metrics(noop_sizes)
    before["synthetic"] = True
    before["shaping"] = "none (Noop)"
    before["sample_sizes"] = noop_sizes[:10] + ["..."]

    # ---- After (Padding) ----
    padding_config = ShapingConfig(
        enabled=True,
        padding_enabled=True,
        min_padding_bytes=8,
        max_padding_bytes=32,
    )
    padding_shaper = create_traffic_shaper(padding_config, seed=42)
    padded_sizes = packet_sizes_series(all_sizes, padding_shaper)
    after_padding = compute_basic_metrics(padded_sizes)
    after_padding["synthetic"] = True
    after_padding["shaping"] = "padding (8-32 bytes)"
    after_padding["sample_sizes"] = padded_sizes[:10] + ["..."]

    # ---- After (Padding + Aggregation) ----
    aggregation_config = ShapingConfig(
        enabled=True,
        padding_enabled=True,
        min_padding_bytes=8,
        max_padding_bytes=32,
        aggregation_enabled=True,
        aggregation_max_bytes=4096,
    )
    agg_shaper = create_traffic_shaper(aggregation_config, seed=42)
    agg_sizes = packet_sizes_series(all_sizes, agg_shaper)
    after_agg = compute_basic_metrics(agg_sizes)
    after_agg["synthetic"] = True
    after_agg["shaping"] = "padding + aggregation (4096 byte buffer)"
    after_agg["sample_sizes"] = agg_sizes[:10] + ["..."]

    results = {
        "generated_by": "scripts/synthetic_shaping_comparison.py",
        "synthetic": True,
        "note": "SYNTHETIC — not from real network capture. Packet sizes simulated.",
        "seed": 42,
        "input_packet_count": len(all_sizes),
        "before": before,
        "after_padding": after_padding,
        "after_padding_aggregation": after_agg,
        "deltas": {
            "padding_vs_noop": {
                "repeated_length_ratio": round(after_padding["repeated_length_ratio"] - before["repeated_length_ratio"], 4),
                "small_packet_ratio": round(after_padding["small_packet_ratio"] - before["small_packet_ratio"], 4),
                "risk_score": round(after_padding["fingerprint_risk_score"] - before["fingerprint_risk_score"], 4),
            },
            "aggregation_vs_noop": {
                "repeated_length_ratio": round(after_agg["repeated_length_ratio"] - before["repeated_length_ratio"], 4),
                "small_packet_ratio": round(after_agg["small_packet_ratio"] - before["small_packet_ratio"], 4),
                "risk_score": round(after_agg["fingerprint_risk_score"] - before["fingerprint_risk_score"], 4),
                "packet_count_reduction": f"{before['packet_count']} -> {after_agg['packet_count']}",
            },
        },
    }

    out_dir = "traces_after"
    import os
    os.makedirs(out_dir, exist_ok=True)

    out_path = f"{out_dir}/synthetic_comparison.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
