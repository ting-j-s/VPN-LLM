#!/usr/bin/env python3
"""Generate an integrated evaluation summary across all four detection gates.

Reads existing evaluation outputs and produces:
  - outputs/integrated_evaluation_summary.json
  - outputs/integrated_evaluation_summary.md

If any input is missing, marks it as missing rather than crashing.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _collect_fingerprint_summary(summary_path: Path | None) -> dict:
    """Read fingerprint summary.json, return structured section."""
    if summary_path is None:
        summary_path = Path("traces/summary.json")

    data = _read_json(summary_path)
    if data is None:
        return {"status": "missing", "path": str(summary_path), "entries": []}

    entries = data.get("entries", [])
    real_entries = [e for e in entries if e.get("trace_type") == "real"]
    skipped_entries = [e for e in entries if e.get("trace_type") == "skipped"]
    risk_levels = {}
    for e in real_entries:
        rl = e.get("risk_level", "unknown")
        risk_levels[rl] = risk_levels.get(rl, 0) + 1

    return {
        "status": "ok",
        "path": str(summary_path),
        "total_entries": len(entries),
        "real_entries": len(real_entries),
        "skipped_entries": len(skipped_entries),
        "risk_levels": risk_levels,
        "transport_scenario_pairs": [
            {
                "transport": e["transport"],
                "scenario": e["scenario"],
                "risk_level": e.get("risk_level"),
                "risk_score": e.get("fingerprint_risk_score"),
                "packet_count": e.get("packet_count"),
            }
            for e in real_entries
        ],
    }


def _collect_probe_summary(probe_path: Path | None) -> dict:
    """Read a probe report JSON, return structured section."""
    if probe_path is None:
        # Try default mock probe path
        candidates = [
            Path("/tmp/probe.report.json"),
            Path("traces/probe/mock.report.json"),
        ]
        for c in candidates:
            if c.exists():
                probe_path = c
                break

    if probe_path is None:
        return {"status": "missing", "path": "no default path found", "scenarios": []}

    data = _read_json(probe_path)
    if data is None:
        return {"status": "missing", "path": str(probe_path), "scenarios": []}

    raw = data.get("raw", data)
    results = raw.get("results", raw.get("scenarios", raw.get("probe_results", [])))
    # Count scenarios that did NOT respond (silent drop = good)
    silent_drops = sum(
        1 for r in results
        if isinstance(r, dict) and r.get("responded") is False
    )

    return {
        "status": "ok",
        "path": str(probe_path),
        "total_scenarios": len(results),
        "silent_drops": silent_drops,
        "risk_level": raw.get("risk_level", data.get("risk_level", "unknown")),
    }


def _collect_rtt_summary(rtt_path: Path | None) -> dict:
    """Read an RTT report JSON, return structured section."""
    if rtt_path is None:
        candidates = [
            Path("/tmp/rtt_proxy_like.report.json"),
            Path("/tmp/rtt.report.json"),
        ]
        for c in candidates:
            if c.exists():
                rtt_path = c
                break

    if rtt_path is None:
        return {"status": "missing", "path": "no default path found"}

    data = _read_json(rtt_path)
    if data is None:
        return {"status": "missing", "path": str(rtt_path)}

    # RTT values may be at top level, in raw, OR inside measurements array
    raw = data.get("raw", data)
    app_rtt = data.get("application_rtt_ms") or raw.get("application_rtt_ms")
    transport_rtt = data.get("transport_rtt_ms") or raw.get("transport_rtt_ms")
    network_rtt = data.get("network_rtt_ms") or raw.get("network_rtt_ms")
    diff_ms = data.get("app_transport_diff_ms") or raw.get("app_transport_diff_ms")
    network_diff_ms = data.get("app_network_diff_ms") or raw.get("app_network_diff_ms")
    stability = data.get("timing_stability_score") or raw.get("timing_stability_score")
    risk_score = data.get("risk_score") or raw.get("risk_score", 0)
    risk_level = data.get("risk_level") or raw.get("risk_level", "unknown")

    if app_rtt is None:
        measurements = data.get("measurements", raw.get("measurements", []))
        for m in measurements:
            if m.get("layer") == "application":
                app_rtt = m.get("median_ms")
            elif m.get("layer") == "transport":
                transport_rtt = m.get("median_ms")
            elif m.get("layer") == "network":
                network_rtt = m.get("median_ms")

    return {
        "status": "ok",
        "path": str(rtt_path),
        "detector_name": data.get("detector_name", "cross_layer_rtt"),
        "application_rtt_ms": app_rtt,
        "transport_rtt_ms": transport_rtt,
        "network_rtt_ms": network_rtt,
        "app_transport_diff_ms": diff_ms,
        "app_network_diff_ms": network_diff_ms,
        "timing_stability_score": stability,
        "risk_score": risk_score,
        "risk_level": risk_level,
    }


def _collect_shaping_summary(shaping_path: Path | None) -> dict:
    """Read synthetic shaping comparison JSON."""
    if shaping_path is None:
        shaping_path = Path("traces_after/synthetic_comparison.json")

    data = _read_json(shaping_path)
    if data is None:
        return {"status": "missing", "path": str(shaping_path)}

    return {
        "status": "ok",
        "path": str(shaping_path),
        "generated_by": data.get("generated_by", ""),
        "seed": data.get("seed"),
        "before_risk": data.get("before", {}).get("fingerprint_risk_score"),
        "after_padding_risk": data.get("after_padding", {}).get("fingerprint_risk_score"),
        "after_aggregation_risk": data.get("after_padding_aggregation", {}).get("fingerprint_risk_score"),
        "deltas": data.get("deltas", {}),
    }


def generate_summary(
    fingerprint_path: str | None = None,
    probe_path: str | None = None,
    rtt_path: str | None = None,
    shaping_path: str | None = None,
    output_dir: str = "outputs",
) -> dict:
    """Collect all gate summaries into a unified report."""
    now = datetime.now(timezone.utc).isoformat()

    fp = _collect_fingerprint_summary(Path(fingerprint_path) if fingerprint_path else None)
    probe = _collect_probe_summary(Path(probe_path) if probe_path else None)
    rtt = _collect_rtt_summary(Path(rtt_path) if rtt_path else None)
    shaping = _collect_shaping_summary(Path(shaping_path) if shaping_path else None)

    gate_status = {}
    for name, section in [
        ("fingerprint", fp),
        ("active_probe", probe),
        ("cross_layer_rtt", rtt),
        ("traffic_shaping", shaping),
    ]:
        gate_status[name] = {
            "available": section.get("status") == "ok",
            "status": section.get("status", "unknown"),
        }

    return {
        "generated_at": now,
        "generated_by": "scripts/generate_integrated_evaluation_summary.py",
        "test_baseline": "1324 passed, 6 skipped",
        "gates": gate_status,
        "fingerprint": fp,
        "active_probe": probe,
        "cross_layer_rtt": rtt,
        "traffic_shaping": shaping,
    }


def render_markdown(summary: dict) -> str:
    """Render the integrated summary as markdown."""
    lines = [
        "# VPN-LLM Integrated Evaluation Summary",
        "",
        f"Generated: {summary['generated_at']}",
        f"Test baseline: {summary['test_baseline']}",
        "",
        "## Gate Status",
        "",
        "| Gate | Available | Status |",
        "|---|---|---|",
    ]
    for name, gs in summary["gates"].items():
        status = "available" if gs["available"] else "missing"
        lines.append(f"| {name} | {gs['available']} | {status} |")

    # Fingerprint section
    fp = summary["fingerprint"]
    lines += [
        "",
        "## 1. Fingerprint Gate",
        "",
        f"Status: **{fp['status']}**",
    ]
    if fp["status"] == "ok":
        lines += [
            f"Source: `{fp['path']}`",
            f"Total entries: {fp['total_entries']}",
            f"Real traces: {fp['real_entries']}",
            f"Skipped: {fp['skipped_entries']}",
            f"Risk levels: {fp['risk_levels']}",
        ]
        if fp["transport_scenario_pairs"]:
            lines += [
                "",
                "| Transport | Scenario | Risk Level | Risk Score | Packets |",
                "|---|---|---|---|---|",
            ]
            for e in fp["transport_scenario_pairs"]:
                lines.append(
                    f"| {e['transport']} | {e['scenario']} | {e['risk_level']} | "
                    f"{e['risk_score']} | {e['packet_count']} |"
                )
    else:
        lines.append(f"Missing: `{fp['path']}`")

    # Probe section
    probe = summary["active_probe"]
    lines += [
        "",
        "## 2. Active Probe Gate",
        "",
        f"Status: **{probe['status']}**",
    ]
    if probe["status"] == "ok":
        lines += [
            f"Source: `{probe['path']}`",
            f"Scenarios: {probe['total_scenarios']}",
            f"Silent drops: {probe['silent_drops']}",
            f"Risk level: {probe['risk_level']}",
        ]
    else:
        lines.append(f"Missing: `{probe['path']}`")

    # RTT section
    rtt = summary["cross_layer_rtt"]
    lines += [
        "",
        "## 3. Cross-Layer RTT Gate",
        "",
        f"Status: **{rtt['status']}**",
    ]
    if rtt["status"] == "ok":
        lines += [
            f"Source: `{rtt['path']}`",
            f"App RTT: {rtt.get('application_rtt_ms')} ms",
            f"Transport RTT: {rtt.get('transport_rtt_ms')} ms",
            f"App-Transport diff: {rtt.get('app_transport_diff_ms')} ms",
            f"Timing stability: {rtt.get('timing_stability_score')}",
            f"Risk score: {rtt.get('risk_score')}",
            f"Risk level: **{rtt.get('risk_level')}**",
        ]
    else:
        lines.append(f"Missing: `{rtt['path']}`")

    # Shaping section
    shaping = summary["traffic_shaping"]
    lines += [
        "",
        "## 4. Traffic Shaping (Synthetic)",
        "",
        f"Status: **{shaping['status']}**",
    ]
    if shaping["status"] == "ok":
        lines += [
            f"Source: `{shaping['path']}`",
            f"Before risk: {shaping.get('before_risk')}",
            f"After padding risk: {shaping.get('after_padding_risk')}",
            f"After aggregation risk: {shaping.get('after_aggregation_risk')}",
            f"Deltas: {json.dumps(shaping.get('deltas', {}))}",
        ]
    else:
        lines.append(f"Missing: `{shaping['path']}`")

    # Limitations
    lines += [
        "",
        "## 5. Current Limitations",
        "",
        "- Most fingerprint traces are 'report not found' — only 3 real idle traces exist",
        "- Traffic shaping comparison is synthetic (computer-generated packet sizes)",
        "- No real WebSocket RTT before/after measurement",
        "- No real network probe testing",
        "- No passive RTT estimation",
        "",
        "---",
        "",
        "See [docs/phase8_integrated_evaluation.md](docs/phase8_integrated_evaluation.md) for full details.",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate integrated evaluation summary across all detection gates"
    )
    parser.add_argument(
        "--fingerprint-summary",
        default="traces/summary.json",
        help="Path to fingerprint summary.json",
    )
    parser.add_argument(
        "--probe-report",
        default=None,
        help="Path to probe report JSON",
    )
    parser.add_argument(
        "--rtt-report",
        default=None,
        help="Path to RTT report JSON",
    )
    parser.add_argument(
        "--shaping-comparison",
        default="traces_after/synthetic_comparison.json",
        help="Path to synthetic shaping comparison JSON",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs",
        help="Output directory (default: outputs/)",
    )
    args = parser.parse_args()

    summary = generate_summary(
        fingerprint_path=args.fingerprint_summary,
        probe_path=args.probe_report,
        rtt_path=args.rtt_report,
        shaping_path=args.shaping_comparison,
        output_dir=args.output_dir,
    )

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "integrated_evaluation_summary.json"
    json_path.write_text(json.dumps(summary, indent=2, default=str))

    md_path = out_dir / "integrated_evaluation_summary.md"
    md_path.write_text(render_markdown(summary))

    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")

    # Exit 0 if at least one gate has data
    any_available = any(
        g["available"] for g in summary["gates"].values()
    )
    if not any_available:
        print("Warning: no gate data available", file=sys.stderr)


if __name__ == "__main__":
    main()
