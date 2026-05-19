"""Active probe resistance report generator.

CLI entry point for running probe scenarios and producing a DetectionReport
suitable for the Phase 4 detection gate / LLM patch loop.

Usage:
    python3 -m src.evaluation.probe.report \
      --host 127.0.0.1 --port 9000 \
      --output-json traces/probe/server_probe.report.json \
      --local-only

    python3 -m src.evaluation.probe.report \
      --mock \
      --output-json /tmp/probe.report.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

from .probe_scenarios import PROBE_SCENARIOS, ProbeScenario, get_scenario_by_name
from .probe_runner import (
    MockProbeRunner,
    LocalSocketProbeRunner,
    ProbeResult,
    _is_local_host,
)


# ---------------------------------------------------------------------------
# Behavior summary & risk calculation
# ---------------------------------------------------------------------------


def compute_behavior_summary(results: list[ProbeResult]) -> dict:
    """Compute summary statistics from probe results."""
    if not results:
        return {
            "total_scenarios": 0,
            "connected_count": 0,
            "close_time_min_ms": 0,
            "close_time_max_ms": 0,
            "close_time_range_ms": 0,
            "close_time_mean_ms": 0,
            "timeout_count": 0,
            "immediate_close_count": 0,
            "response_count": 0,
            "distinct_error_types": 0,
        }

    close_times = [r.elapsed_ms for r in results if r.close_observed]
    timeout_count = sum(1 for r in results if r.timeout_observed)
    response_count = sum(1 for r in results if r.bytes_received > 0)
    immediate_close_count = sum(
        1 for r in results if r.close_observed and r.elapsed_ms < 10.0
    )
    error_types = set(r.error_type for r in results if r.error_type)

    return {
        "total_scenarios": len(results),
        "connected_count": sum(1 for r in results if r.connected),
        "close_time_min_ms": round(min(close_times), 2) if close_times else 0,
        "close_time_max_ms": round(max(close_times), 2) if close_times else 0,
        "close_time_range_ms": round(max(close_times) - min(close_times), 2) if close_times else 0,
        "close_time_mean_ms": round(statistics.mean(close_times), 2) if close_times else 0,
        "timeout_count": timeout_count,
        "immediate_close_count": immediate_close_count,
        "response_count": response_count,
        "distinct_error_types": len(error_types),
    }


def compute_probe_variance_metrics(results: list[ProbeResult]) -> dict:
    """Compute probe-specific metrics for DetectionGate evaluation.

    Returns dict with:
    - probe_response_variance: 0-1 score. High = more fingerprintable variance.
    - malformed_close_time_variance: normalized close time spread.
    """
    if not results:
        return {
            "probe_response_variance": 0.0,
            "malformed_close_time_variance": 0.0,
        }

    summary = compute_behavior_summary(results)

    # probe_response_variance: composite of distinct_error_types ratio,
    # timeout/response mixture, and close_time spread.
    n = summary["total_scenarios"]
    distinct_ratio = summary["distinct_error_types"] / max(n, 1)

    has_timeouts = 1 if summary["timeout_count"] > 0 else 0
    has_responses = 1 if summary["response_count"] > 0 else 0
    mixture_score = (has_timeouts + has_responses) / 2.0  # 0, 0.5, or 1.0

    # Normalize close time range: > 1000ms = definitely large variance
    close_time_range_ms = summary["close_time_range_ms"]
    close_time_spread = min(close_time_range_ms / 1000.0, 1.0)

    probe_response_variance = round(
        0.4 * distinct_ratio + 0.3 * mixture_score + 0.3 * close_time_spread, 4
    )

    # malformed_close_time_variance: coefficient of variation-like measure
    mean_close = summary["close_time_mean_ms"]
    if mean_close > 0 and summary["close_time_range_ms"] > 0:
        malformed_close_time_variance = round(
            min(summary["close_time_range_ms"] / mean_close, 1.0), 4
        )
    else:
        malformed_close_time_variance = 0.0

    return {
        "probe_response_variance": probe_response_variance,
        "malformed_close_time_variance": malformed_close_time_variance,
    }


def compute_risk_level(probe_response_variance: float, malformed_close_time_variance: float) -> tuple[float, str]:
    """Compute risk score and level from probe metrics.

    Risk rules:
    - High distinct_error_types / timeout-response mixture → high risk
    - Very uniform behavior across all malformed inputs → low risk
    """
    # Weighted composite
    risk_score = round(
        0.5 * probe_response_variance + 0.5 * malformed_close_time_variance, 4
    )

    if risk_score < 0.3:
        level = "low"
    elif risk_score < 0.6:
        level = "medium"
    else:
        level = "high"

    return risk_score, level


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_probe_report(
    results: list[ProbeResult],
    host: str = "127.0.0.1",
    port: int = 9000,
    local_only: bool = True,
    notes: list[str] | None = None,
) -> dict:
    """Generate a probe report dict compatible with DetectionReport loading.

    The output dict includes all fields needed by load_detection_report()
    and from_fingerprint_report(), plus probe-specific behavior_summary.
    """
    if notes is None:
        notes = []

    if local_only and not _is_local_host(host):
        raise ValueError(
            f"--local-only is set, rejecting non-local host: {host!r}. "
            f"No third-party scanning permitted."
        )

    summary = compute_behavior_summary(results)
    variance = compute_probe_variance_metrics(results)
    risk_score, risk_level = compute_risk_level(
        variance["probe_response_variance"],
        variance["malformed_close_time_variance"],
    )

    # Build metrics list in DetectionMetric-compatible format
    metrics = [
        {
            "name": "probe_response_variance",
            "value": variance["probe_response_variance"],
            "threshold": None,
            "passed": True,
            "severity": "info",
            "explanation": f"probe_response_variance={variance['probe_response_variance']}",
        },
        {
            "name": "malformed_close_time_variance",
            "value": variance["malformed_close_time_variance"],
            "threshold": None,
            "passed": True,
            "severity": "info",
            "explanation": f"malformed_close_time_variance={variance['malformed_close_time_variance']}",
        },
        {
            "name": "packet_count",
            "value": summary["total_scenarios"],
            "threshold": None,
            "passed": True,
            "severity": "info",
            "explanation": f"probe scenario count={summary['total_scenarios']}",
        },
    ]

    notes_lines = list(notes)
    notes_lines.append(
        f"local_only={local_only} host={host} port={port}"
    )
    if summary["distinct_error_types"] > 2:
        notes_lines.append(
            f"high distinct_error_types={summary['distinct_error_types']} — "
            f"server has distinguishable error paths"
        )
    if summary["timeout_count"] > 0 and summary["response_count"] > 0:
        notes_lines.append(
            "mixture of timeout and response behaviors — potential fingerprint vector"
        )
    if summary["close_time_range_ms"] > 500:
        notes_lines.append(
            f"large close_time_range={summary['close_time_range_ms']:.0f}ms — "
            f"variable close timing"
        )

    return {
        "detector_name": "active_probe_resistance",
        "source_path": None,
        "trace_type": "real" if not any("mock" in r.notes for r in results) else "synthetic",
        "transport": "tcp",
        "scenario": "probe",
        "passed": True,  # pre-gate; gate will set false if thresholds exceeded
        "risk_score": risk_score,
        "risk_level": risk_level,
        "fingerprint_risk_score": risk_score,
        "metrics": metrics,
        "notes": notes_lines,
        "raw": {
            "target_host": host,
            "target_port": port,
            "local_only": local_only,
            "scenario_count": len(results),
            "results": [r.to_dict() for r in results],
            "behavior_summary": summary,
            "probe_response_variance": variance["probe_response_variance"],
            "malformed_close_time_variance": variance["malformed_close_time_variance"],
            "risk_score": risk_score,
            "risk_level": risk_level,
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Active probe resistance report generator (local only)",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Target host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=9000,
        help="Target port (default: 9000)",
    )
    parser.add_argument(
        "--output-json", default=None,
        help="Output JSON report path",
    )
    parser.add_argument(
        "--local-only", action="store_true", default=True,
        help="Restrict to localhost (default: True)",
    )
    parser.add_argument(
        "--scenario", action="append", default=None,
        help="Run specific scenario(s). Repeatable. Default: all.",
    )
    parser.add_argument(
        "--mock", action="store_true", default=False,
        help="Use MockProbeRunner instead of real sockets",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for mock runner (default: 42)",
    )
    args = parser.parse_args()

    # Security: enforce --local-only
    if args.local_only and not _is_local_host(args.host):
        print(
            f"ERROR: --local-only is set, rejecting non-local target: {args.host!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Select scenarios
    if args.scenario:
        scenarios: list[ProbeScenario] = []
        for name in args.scenario:
            s = get_scenario_by_name(name)
            if s is None:
                print(f"ERROR: unknown scenario: {name!r}", file=sys.stderr)
                print(f"Available: {[s.name for s in PROBE_SCENARIOS]}", file=sys.stderr)
                sys.exit(1)
            scenarios.append(s)
    else:
        scenarios = PROBE_SCENARIOS

    # Create runner and execute
    if args.mock:
        runner = MockProbeRunner(seed=args.seed)
        print(f"Using MockProbeRunner (seed={args.seed})", file=sys.stderr)
    else:
        runner = LocalSocketProbeRunner(host=args.host, port=args.port)
        print(f"Probing {args.host}:{args.port} (local only)", file=sys.stderr)

    results = runner.run_all(scenarios)

    # Generate report
    report = generate_probe_report(
        results,
        host=args.host,
        port=args.port,
        local_only=args.local_only,
    )

    # Output
    json_text = json.dumps(report, indent=2, default=str)
    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json_text, encoding="utf-8")
        print(f"Report written to {out_path}", file=sys.stderr)
    else:
        print(json_text)

    # Summary to stderr
    summary = report["raw"]["behavior_summary"]
    print(
        f"\nScenarios: {summary['total_scenarios']} | "
        f"Connected: {summary['connected_count']} | "
        f"Close range: {summary['close_time_range_ms']:.0f}ms | "
        f"Timeouts: {summary['timeout_count']} | "
        f"Responses: {summary['response_count']} | "
        f"Error types: {summary['distinct_error_types']}",
        file=sys.stderr,
    )
    print(
        f"Risk: {report['risk_level']} (score={report['risk_score']})",
        file=sys.stderr,
    )

    sys.exit(0)  # exit 0 = report generated; not a pass/fail verdict


if __name__ == "__main__":
    main()
