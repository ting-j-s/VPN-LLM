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

    python3 -m src.evaluation.rtt.report \
      --mode websocket \
      --ws-port 8765 \
      --output-json /tmp/ws_rtt.report.json

Defaults:
- --local-only enforced; non-localhost hosts rejected
- --mock mode runs without network (MockRTTRunner)
- --mode websocket runs WebSocket echo (application) + TCP connect (transport)
  and produces a full cross-layer RTT report
- Exit codes: 0 = report generated, 2 = input error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .rtt_measurements import (
    CrossLayerRTTReport,
    compute_rtt_diff,
    score_cross_layer_rtt,
    timing_stability_score,
)
from .rtt_runner import (
    LocalTCPRTTRunner,
    MockRTTRunner,
    OptionalPingRunner,
    _is_local_host,
)
from .websocket_rtt import (
    WebSocketRTTConfig,
    WebSocketRTTResult,
    measure_websocket_rtt,
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
# WebSocket multi-runner mode
# ---------------------------------------------------------------------------


def _run_websocket_mode(args: argparse.Namespace) -> CrossLayerRTTReport:
    """Run a full cross-layer RTT evaluation with WebSocket + TCP + optional ping.

    Application RTT: WebSocket echo (measure_websocket_rtt).
    Transport RTT:  LocalTCPRTTRunner (unless --ws-no-tcp).
    Network RTT:    Not included by default; use --mode ping separately.
    """
    target = args.host
    notes: list[str] = []
    measurements = []
    app_rtt: float | None = None
    tcp_rtt: float | None = None
    net_rtt: float | None = None
    stability: float | None = None
    raw: dict = {"runner": "websocket", "host": target}

    # --- Application-layer RTT via WebSocket echo ---
    ws_config = WebSocketRTTConfig(
        host=target,
        port=args.ws_port,
        path=args.ws_path,
        sample_count=args.ws_sample_count,
        local_only=args.local_only,
    )
    try:
        ws_config.validate()
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    print(
        f"Measuring WebSocket RTT to ws://{target}:{args.ws_port}{args.ws_path} "
        f"({args.ws_sample_count} samples)",
        file=sys.stderr,
    )
    ws_result = measure_websocket_rtt(ws_config)
    ws_measurement = ws_result.to_rtt_measurement()
    measurements.append(ws_measurement)
    raw["websocket"] = {
        "connected": ws_result.connected,
        "samples_ms": ws_result.samples_ms,
        "sample_count": ws_result.sample_count,
        "error": ws_result.error,
    }

    if ws_result.error:
        notes.append(f"WebSocket RTT error: {ws_result.error}")
    if ws_result.sample_count > 0:
        app_rtt = ws_result.avg_ms
        notes.append(
            f"application_rtt_ms={app_rtt:.3f} "
            f"(WebSocket echo, {ws_result.sample_count} samples)"
        )

    # --- Transport-layer RTT via TCP connect ---
    if not args.ws_no_tcp:
        try:
            tcp_runner = LocalTCPRTTRunner(host=target, port=args.ws_port, samples=5)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(2)

        print(
            f"Measuring TCP RTT to {target}:{args.ws_port} (5 samples)",
            file=sys.stderr,
        )
        tcp_report = tcp_runner.run(target=target)
        raw["tcp"] = tcp_report.raw
        notes.extend(tcp_report.notes)
        for m in tcp_report.measurements:
            measurements.append(m)
            if m.layer == "transport" and m.avg_ms is not None:
                tcp_rtt = m.avg_ms

    # --- Compute cross-layer diffs and risk ---
    diffs = compute_rtt_diff(app_rtt, tcp_rtt, net_rtt)

    if ws_result.sample_count >= 2:
        stability = timing_stability_score(ws_result.samples_ms)

    risk_score, risk_level = score_cross_layer_rtt(
        diffs["app_transport_diff_ms"], stability
    )

    if risk_level == "insufficient_data":
        notes.append(
            "insufficient data for cross-layer RTT scoring "
            "(application RTT or transport RTT unavailable)"
        )

    raw["diffs"] = diffs
    raw["stability"] = stability

    return CrossLayerRTTReport(
        target=target,
        trace_type="real",
        application_rtt_ms=app_rtt,
        transport_rtt_ms=tcp_rtt,
        network_rtt_ms=net_rtt,
        app_transport_diff_ms=diffs["app_transport_diff_ms"],
        app_network_diff_ms=diffs["app_network_diff_ms"],
        timing_stability_score=stability,
        risk_score=risk_score,
        risk_level=risk_level,
        measurements=measurements,
        notes=notes,
        raw=raw,
    )


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
        choices=["mock", "tcp", "ping", "websocket"],
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
    # WebSocket RTT options
    parser.add_argument(
        "--ws-port", type=int, default=8765,
        help="WebSocket server port for --mode websocket (default: 8765)",
    )
    parser.add_argument(
        "--ws-path", default="/rtt",
        help="WebSocket endpoint path (default: /rtt)",
    )
    parser.add_argument(
        "--ws-sample-count", type=int, default=10,
        help="Echo samples for --mode websocket (default: 10)",
    )
    parser.add_argument(
        "--ws-no-tcp", action="store_true",
        help="Skip TCP transport RTT in --mode websocket",
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
        report = runner.run()
    elif args.mode == "tcp":
        try:
            runner = LocalTCPRTTRunner(host=args.host, port=args.port)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(2)
        print(f"Measuring TCP RTT to {args.host}:{args.port}", file=sys.stderr)
        report = runner.run()
    elif args.mode == "ping":
        try:
            runner = OptionalPingRunner(host=args.host)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(2)
        print(f"Measuring ICMP RTT to {args.host}", file=sys.stderr)
        report = runner.run()
    elif args.mode == "websocket":
        report = _run_websocket_mode(args)
    else:
        print(f"ERROR: unknown mode {args.mode!r}", file=sys.stderr)
        sys.exit(2)

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
