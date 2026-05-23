#!/usr/bin/env python3
"""Generate final project summary for Phase 11 convergence.

Reads git metadata, documentation, and existing evaluation outputs to produce:
  - outputs/final_project_summary.json
  - outputs/final_project_summary.md

If any input is missing, marks it as missing rather than crashing.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _git_head() -> dict:
    """Read git HEAD metadata."""
    try:
        rev = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd=REPO_ROOT
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        rev = "unknown"

    try:
        short = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, cwd=REPO_ROOT
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        short = "unknown"

    try:
        msg = subprocess.check_output(
            ["git", "log", "-1", "--format=%s"], text=True, cwd=REPO_ROOT
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        msg = "unknown"

    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True, cwd=REPO_ROOT
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        branch = "unknown"

    try:
        count = subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"], text=True, cwd=REPO_ROOT
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        count = "unknown"

    return {
        "commit": rev,
        "commit_short": short,
        "commit_message": msg,
        "branch": branch,
        "total_commits": int(count) if count.isdigit() else count,
    }


def _collect_coverage_matrix() -> dict:
    path = REPO_ROOT / "docs" / "detection_coverage_matrix.md"
    exists = path.exists()
    return {
        "status": "ok" if exists else "missing",
        "path": str(path),
        "size_bytes": path.stat().st_size if exists else None,
    }


def _collect_final_report() -> dict:
    path = REPO_ROOT / "docs" / "final_integrated_report.md"
    exists = path.exists()
    return {
        "status": "ok" if exists else "missing",
        "path": str(path),
        "size_bytes": path.stat().st_size if exists else None,
    }


def _collect_phase9_results() -> dict:
    """Collect Phase 9 real trace matrix results."""
    candidates = [
        REPO_ROOT / "outputs" / "phase9_real_matrix_e" / "results.json",
        REPO_ROOT / "outputs" / "phase9_real_matrix" / "results.json",
    ]
    for path in candidates:
        data = _read_json(path)
        if data is not None:
            entries = data.get("results", data.get("entries", []))
            ok_count = sum(
                1 for e in entries
                if e.get("status") == "ok" or e.get("data_quality") == "ok"
            )
            total = len(entries)
            transports = sorted(set(
                e.get("transport", "") for e in entries if e.get("transport")
            ))
            return {
                "status": "ok",
                "source": str(path.relative_to(REPO_ROOT)),
                "total_entries": total,
                "ok_entries": ok_count,
                "transports": transports,
            }
    return {"status": "missing", "source": None, "total_entries": 0}


def _collect_phase10_results() -> dict:
    """Collect HTTP/2 Phase 10 before/after results."""
    phases = {}
    phase_dirs = {
        "10C": "phase10c_http2_real",
        "10D": "phase10d_http2_aware",
        "10E-A": "phase10e_a_multistream",
        "10E-B": "phase10e_b_settings",
    }
    for label, dirname in phase_dirs.items():
        # Try repeated format first, then single-run format
        path = REPO_ROOT / "outputs" / dirname / "summaries" / "repeated_before_after_comparison.json"
        data = _read_json(path)
        is_repeated = data is not None
        if data is None:
            path = REPO_ROOT / "outputs" / dirname / "summaries" / "before_after_comparison.json"
            data = _read_json(path)
        if data is not None:
            entries = data.get("entries", [])
            summary = {}
            for e in entries:
                scenario = e.get("scenario", "unknown")
                if is_repeated:
                    before = e.get("before_risk_score_mean")
                    after = e.get("after_risk_score_mean")
                    verdict = e.get("aggregate_verdict")
                else:
                    before = e.get("fingerprint_risk_score_before")
                    after = e.get("fingerprint_risk_score_after")
                    verdict = e.get("verdict")
                summary[scenario] = {
                    "before_risk": before,
                    "after_risk": after,
                    "verdict": verdict,
                }
            phases[label] = {"status": "ok", "scenarios": summary}
        else:
            phases[label] = {"status": "missing", "scenarios": {}}
    return phases


def _collect_integrated_summary() -> dict:
    path = REPO_ROOT / "outputs" / "integrated_evaluation_summary.json"
    data = _read_json(path)
    if data is None:
        return {"status": "missing"}
    gates = data.get("gates", {})
    available = sum(1 for g in gates.values() if g.get("available"))
    return {
        "status": "ok",
        "available_gates": available,
        "total_gates": len(gates),
        "generated_at": data.get("generated_at"),
    }


def generate_summary(output_dir: str = "outputs") -> dict:
    """Generate the final project summary."""
    now = datetime.now(timezone.utc).isoformat()

    git = _git_head()
    coverage = _collect_coverage_matrix()
    final_report = _collect_final_report()
    phase9 = _collect_phase9_results()
    phase10 = _collect_phase10_results()
    integrated = _collect_integrated_summary()

    return {
        "generated_at": now,
        "generated_by": "scripts/generate_final_project_summary.py",
        "phase": "Phase 11 — Final Integrated Report and Project Convergence",
        "git": git,
        "docs": {
            "detection_coverage_matrix": coverage,
            "final_integrated_report": final_report,
        },
        "evaluation": {
            "integrated_summary": integrated,
            "phase9_real_trace_matrix": phase9,
            "phase10_http2": phase10,
        },
        "test_baseline": "1633 passed, 10 skipped",
        "future_work": [
            "HPACK/header behavior (deferred from Phase 10E-C)",
            "Passive RTT estimation",
            "Full repeated matrix (all transport x scenario x n>=3)",
            "Real WebSocket RTT before/after",
            "Richer LLM auto-fix loop",
            "CI integration",
            "Optional push/sync",
        ],
        "safety_boundary": [
            "Local controlled experiments only",
            "No third-party scanning",
            "No browser emulation claim",
            "No real undetectability claim",
            "pcap not committed",
            "No HPACK/header behavior implemented",
        ],
    }


def render_markdown(summary: dict) -> str:
    """Render the final project summary as markdown."""
    git = summary["git"]
    phase9 = summary["evaluation"]["phase9_real_trace_matrix"]
    phase10 = summary["evaluation"]["phase10_http2"]
    integrated = summary["evaluation"]["integrated_summary"]

    lines = [
        "# VPN-LLM Final Project Summary",
        "",
        f"Generated: {summary['generated_at']}",
        f"Phase: **{summary['phase']}**",
        "",
        "## Git Status",
        "",
        f"- Branch: `{git['branch']}`",
        f"- HEAD: `{git['commit_short']}` ({git['commit']})",
        f"- Message: {git['commit_message']}",
        f"- Total commits: {git['total_commits']}",
        "",
        "## Documentation",
        "",
        f"- Detection coverage matrix: **{summary['docs']['detection_coverage_matrix']['status']}**",
        f"- Final integrated report: **{summary['docs']['final_integrated_report']['status']}**",
        "",
        "## Evaluation Results",
        "",
        "### Integrated Summary",
    ]
    if integrated["status"] == "ok":
        lines.append(f"- Available gates: {integrated['available_gates']}/{integrated['total_gates']}")
        lines.append(f"- Last generated: {integrated['generated_at']}")
    else:
        lines.append("- Status: missing")

    lines += [
        "",
        "### Phase 9 Real Trace Matrix",
    ]
    if phase9["status"] == "ok":
        lines.append(f"- Source: `{phase9['source']}`")
        lines.append(f"- Entries: {phase9['total_entries']} total, {phase9['ok_entries']} ok")
        lines.append(f"- Transports: {', '.join(phase9['transports'])}")
    else:
        lines.append("- Status: missing")

    lines += [
        "",
        "### HTTP/2 Phase 10 Results",
    ]
    for label in ["10C", "10D", "10E-A", "10E-B"]:
        phase = phase10.get(label, {"status": "missing"})
        if phase["status"] == "ok":
            scenarios = phase["scenarios"]
            lines.append(f"**Phase {label}**")
            for scenario, data in scenarios.items():
                before = data.get("before_risk", "N/A")
                after = data.get("after_risk", "N/A")
                verdict = data.get("verdict", "N/A")
                if isinstance(before, (int, float)):
                    before = f"{before:.3f}"
                if isinstance(after, (int, float)):
                    after = f"{after:.3f}"
                lines.append(f"  - {scenario}: {before} → {after} ({verdict})")
        else:
            lines.append(f"**Phase {label}**: missing")

    lines += [
        "",
        "## LLM Workflow",
        "",
        "The LLM is NOT in the runtime data path. It operates on structured detection",
        "reports: DetectionReport → Gate → CountermeasurePolicy → PromptBuilder → LLM",
        "Patch Loop → SafetyGuard → ValidationRunner → pytest → Real Trace Matrix.",
        "",
        "### Core LLM Detection Modules",
        "",
        "| Module | Purpose |",
        "|---|---|",
        "| `DetectionReport` | Unified data model for fingerprint/probe/RTT reports |",
        "| `DetectionGate` | Multi-metric threshold evaluation |",
        "| `CountermeasurePolicy` | Metric → countermeasure hint mapping |",
        "| `PromptBuilder` | Structured adversarial patch prompt generation |",
        "| `PatchLoop` | End-to-end CLI pipeline |",
        "",
        "### Evaluation Gates",
        "",
        "| Gate | Purpose |",
        "|---|---|",
        "| Fingerprint Gate | Packet size, n-gram, burst, timing features |",
        "| Active Probe Gate | Malformed-input resistance (9 scenarios, silent-drop) |",
        "| Cross-Layer RTT Gate | App/transport/network RTT comparison |",
        "| Real Trace Matrix | TUN/netns before/after repeated capture |",
        "",
        "## Future Work",
        "",
    ]
    for item in summary["future_work"]:
        lines.append(f"- {item}")

    lines += [
        "",
        "## Safety Boundary",
        "",
    ]
    for item in summary["safety_boundary"]:
        lines.append(f"- {item}")

    lines += [
        "",
        "---",
        "",
        f"Test baseline: {summary['test_baseline']}",
        "",
        "See [docs/final_integrated_report.md](docs/final_integrated_report.md) for full details.",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate final project summary for Phase 11 convergence"
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/final_summary",
        help="Output directory (default: outputs/final_summary)",
    )
    args = parser.parse_args()

    summary = generate_summary()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "final_project_summary.json"
    json_path.write_text(json.dumps(summary, indent=2, default=str))

    md_path = out_dir / "final_project_summary.md"
    md_path.write_text(render_markdown(summary))

    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")

    any_data = (
        summary["evaluation"]["integrated_summary"]["status"] == "ok"
        or summary["evaluation"]["phase9_real_trace_matrix"]["status"] == "ok"
        or any(
            p["status"] == "ok"
            for p in summary["evaluation"]["phase10_http2"].values()
        )
    )
    if not any_data:
        print("Warning: no evaluation data available", file=sys.stderr)


if __name__ == "__main__":
    main()
