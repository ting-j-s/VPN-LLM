#!/usr/bin/env python3
"""Synthetic before/after RTT comparison for Phase 7B timing countermeasures.

Generates three mock RTT profiles:
  1. baseline_proxy_like — no timing countermeasure applied
  2. jitter_metadata_only — jitter with delay_ms metadata, no sleep
  3. paced_synthetic — pacing applied to reduce app-transport diff

Outputs summary JSON and CSV to traces_after/rtt/.
Marked SYNTHETIC — not from real network measurement.
"""

import csv
import json
import random
import sys
from pathlib import Path

from src.evaluation.rtt.rtt_runner import MockRTTRunner
from src.evaluation.rtt.rtt_measurements import (
    CrossLayerRTTReport,
    score_cross_layer_rtt,
    timing_stability_score,
)


def _generate_mock_report(
    label: str,
    app_rtt_ms: float,
    transport_rtt_ms: float,
    timing_stability: float,
) -> dict:
    """Generate a synthetic RTT report dict for a countermeasure profile."""
    diff = round(abs(app_rtt_ms - transport_rtt_ms), 3)
    risk_score_val, risk_level = score_cross_layer_rtt(diff, timing_stability)

    return {
        "label": label,
        "app_rtt_ms": app_rtt_ms,
        "transport_rtt_ms": transport_rtt_ms,
        "app_transport_diff_ms": diff,
        "timing_stability_score": timing_stability,
        "risk_score": risk_score_val,
        "risk_level": risk_level,
        "trace_type": "synthetic",
        "detector_name": "cross_layer_rtt",
    }


def main() -> None:
    output_dir = Path("traces_after/rtt")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Use fixed seed for reproducibility
    rng = random.Random(42)

    # 1. Baseline proxy_like (no countermeasure)
    baseline = _generate_mock_report(
        "baseline_proxy_like",
        app_rtt_ms=42.5,
        transport_rtt_ms=8.2,
        timing_stability=0.8575,
    )

    # 2. Jitter metadata_only (app RTT slightly increased by pacing)
    # Jitter randomizes chunk timing within a range but doesn't sleep.
    # App RTT still elevated but slightly more variable.
    jitter_base = 42.5 + rng.uniform(2, 8)  # 44.5-50.5
    jitter_transport = 8.2 + rng.uniform(1, 3)  # 9.2-11.2
    jitter_stability = timing_stability_score([rng.uniform(0.8, 1.2) for _ in range(50)])
    jitter = _generate_mock_report(
        "jitter_metadata_only",
        app_rtt_ms=round(jitter_base, 2),
        transport_rtt_ms=round(jitter_transport, 2),
        timing_stability=jitter_stability or 0.7,
    )

    # 3. Paced synthetic (reduced app-transport diff via RTT-aware pacing)
    # Pacing adds per-chunk delay to align transport with app RTT profile.
    pacing_app = 8.2 + rng.uniform(2, 5)  # ~10.2-13.2ms (close to transport)
    pacing_transport = 8.2 + rng.uniform(0.5, 1.5)  # ~8.7-9.7ms
    pacing_samples = [rng.uniform(0.3, 0.8) for _ in range(50)]  # more variable
    pacing_stability = timing_stability_score(pacing_samples)
    paced = _generate_mock_report(
        "paced_synthetic",
        app_rtt_ms=round(pacing_app, 2),
        transport_rtt_ms=round(pacing_transport, 2),
        timing_stability=pacing_stability or 0.4,
    )

    # Build comparison
    rows = [baseline, jitter, paced]

    summary = {
        "description": "Synthetic Phase 7B RTT countermeasure comparison",
        "note": "All data is synthetic — not from real network measurement.",
        "detector_name": "cross_layer_rtt",
        "scenarios": rows,
    }

    # Write summary JSON
    json_path = output_dir / "summary.json"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"JSON summary written to {json_path}")

    # Write summary CSV
    csv_path = output_dir / "summary.csv"
    fieldnames = [
        "label", "app_rtt_ms", "transport_rtt_ms", "app_transport_diff_ms",
        "timing_stability_score", "risk_score", "risk_level",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in fieldnames})

    print(f"CSV summary written to {csv_path}")
    print()

    # Print table
    header = f"{'Profile':<25} {'App':>8} {'Transport':>10} {'Diff':>8} {'Stability':>10} {'Risk':>8} {'Level':>8}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['label']:<25} "
            f"{row['app_rtt_ms']:>7.1f}ms "
            f"{row['transport_rtt_ms']:>9.1f}ms "
            f"{row['app_transport_diff_ms']:>7.1f}ms "
            f"{row['timing_stability_score']:>9.4f} "
            f"{row['risk_score']:>7.4f} "
            f"{row['risk_level']:>8}"
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
