"""Cross-layer RTT report CLI generator.

Usage:
    python3 -m src.evaluation.rtt.report \
      --mock-profile direct \
      --output-json /tmp/rtt_direct.report.json

    python3 -m src.evaluation.rtt.report \
      --mock-profile proxy_like \
      --output-json /tmp/rtt_proxy.report.json

    python3 -m src.evaluation.rtt.report \
      --host 127.0.0.1 --port 9000 --mode tcp \
      --output-json traces/rtt/local.report.json

Defaults:
- --local-only enforced; non-localhost hosts rejected
- --mock mode runs without network (MockRTTRunner)
- Exit codes: 0 = report generated, 2 = input error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .rtt_measurements import CrossLayerRTTReport
from .rtt_runner import (
    LocalTCPRTTRunner,
    MockRTTRunner,
    OptionalPingRunner,
    _is_local_host,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rtt_report_to_json_dict(report: CrossLayerRTTReport) -> dict:
    """Convert CrossLayerRTTReport to a JSON-serializable dict.

    The output dict is compatible with DetectionReport loading via
    from_rtt_report() and load_detection_report().

    Required fields:
    - detector_name: "cross_layer_rtt"
    - trace_type: "real" | "synthetic" | "mock"
    - metrics: list of dicts with name/value/threshold/passed/severity/explanation
    - risk_score, risk_level
    - raw: dict with full measurements and metadata
    """
    metrics = []

    if report.application_rtt_ms is not None:
        metrics.append({
            "name": "application_rtt_ms",
            "value": report.application_rtt_ms,
            "threshold": None,
            "passed": True,
            "severity": "info",
            "explanation": f"application_rtt_ms={report.application_rtt_ms}",
        })

    if report.transport_rtt_ms is not None:
        metrics.append({
            "name": "transport_rtt_ms",
            "value": report.transport_rtt_ms,
            "threshold": None,
            "passed": True,
            "severity": "info",
            "explanation": f"transport_rtt_ms={report.transport_rtt_ms}",
        })

    if report.network_rtt_ms is not None:
        metrics.append({
            "name": "network_rtt_ms",
            "value": report.network_rtt_ms,
            "threshold": None,
            "passed": True,
            "severity": "info",
            "explanation": f"network_rtt_ms={report.network_rtt_ms}",
        })

    if report.app_transport_diff_ms is not None:
        metrics.append({
            "name": "app_transport_diff_ms",
            "value": report.app_transport_diff_ms,
            "threshold": None,
            "passed": True,
            "severity": "info",
            "explanation": f"app_transport_diff_ms={report.app_transport_diff_ms}",
        })

    if report.app_network_diff_ms is not None:
        metrics.append({
            "name": "app_network_diff_ms",
            "value": report.app_network_diff_ms,
            "threshold": None,
            "passed": True,
            "severity": "info",
            "explanation": f"app_network_diff_ms={report.app_network_diff_ms}",
        })

    if report.timing_stability_score is not None:
        metrics.append({
            "name": "timing_stability_score",
            "value": report.timing_stability_score,
            "threshold": None,
            "passed": True,
            "severity": "info",
            "explanation": f"timing_stability_score={report.timing_stability_score}",
        })

    # Always include risk_score and risk_level as metrics
    metrics.append({
        "name": "rtt_risk_score",
        "value": report.risk_score,
        "threshold": None,
        "passed": True,
        "severity": "info",
        "explanation": f"rtt_risk_score={report.risk_score} risk_level={report.risk_level}",
    })

    return {
        "detector_name": report.detector_name,
        "trace_type": report.trace_type,
        "transport": "tcp",
        "scenario": "rtt",
        "risk_score": report.risk_score,
        "risk_level": report.risk_level,
        "metrics": metrics,
        "notes": report.notes,
        "raw": {
            "target": report.target,
            "trace_type": report.trace_type,
            "application_rtt_ms": report.application_rtt_ms,
            "transport_rtt_ms": report.transport_rtt_ms,
            "network_rtt_ms": report.network_rtt_ms,
            "app_transport_diff_ms": report.app_transport_diff_ms,
            "app_network_diff_ms": report.app_network_diff_ms,
            "timing_stability_score": report.timing_stability_score,
            "risk_score": report.risk_score,
            "risk_level": report.risk_level,
            "measurements": [m.to_dict() for m in report.measurements],
            "runner_info": report.raw,
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cross-layer RTT evaluation report generator (local only)",
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
        "--mode", default="mock",
        choices=["mock", "tcp", "ping"],
        help="RTT measurement mode (default: mock)",
    )
    parser.add_argument(
        "--mock-profile", default="direct",
        choices=["direct", "proxy_like"],
        help="Mock profile for --mode mock (default: direct)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for mock runner (default: 42)",
    )
    parser.add_argument(
        "--output-json", default=None,
        help="Output JSON report path",
    )
    parser.add_argument(
        "--local-only", action="store_true", default=True,
        help="Restrict to localhost (default: True)",
    )
    args = parser.parse_args()

    # Security: enforce local-only
    if args.local_only and not _is_local_host(args.host):
        print(
            f"ERROR: --local-only rejects non-local target: {args.host!r}",
            file=sys.stderr,
        )
        sys.exit(2)

    # Create runner and execute
    if args.mode == "mock":
        runner: MockRTTRunner | LocalTCPRTTRunner | OptionalPingRunner = MockRTTRunner(
            profile=args.mock_profile, seed=args.seed
        )
        print(
            f"Using MockRTTRunner (profile={args.mock_profile}, seed={args.seed})",
            file=sys.stderr,
        )
    elif args.mode == "tcp":
        try:
            runner = LocalTCPRTTRunner(host=args.host, port=args.port)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(2)
        print(f"Measuring TCP RTT to {args.host}:{args.port}", file=sys.stderr)
    elif args.mode == "ping":
        try:
            runner = OptionalPingRunner(host=args.host)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(2)
        print(f"Measuring ICMP RTT to {args.host}", file=sys.stderr)
    else:
        print(f"ERROR: unknown mode {args.mode!r}", file=sys.stderr)
        sys.exit(2)

    report = runner.run()

    # Convert to JSON-compatible dict
    output_dict = _rtt_report_to_json_dict(report)

    json_text = json.dumps(output_dict, indent=2, default=str)
    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json_text, encoding="utf-8")
        print(f"Report written to {out_path}", file=sys.stderr)
    else:
        print(json_text)

    # Summary to stderr
    print(
        f"\nTarget: {report.target} | "
        f"App RTT: {report.application_rtt_ms}ms | "
        f"Transport RTT: {report.transport_rtt_ms}ms | "
        f"Diff: {report.app_transport_diff_ms}ms | "
        f"Timing stability: {report.timing_stability_score}",
        file=sys.stderr,
    )
    print(
        f"Risk: {report.risk_level} (score={report.risk_score})",
        file=sys.stderr,
    )

    sys.exit(0)


if __name__ == "__main__":
    main()
