"""PatchLoop — CLI that reads detection reports, runs gates, and generates
an adversarial patch prompt for the next LLM iteration.

Usage:
    python3 -m src.llm.detection.patch_loop \\
      --user-request "reduce fingerprint risk" \\
      --functional-test-summary "compileall passed; pytest passed" \\
      --report traces/tcp/idle.report.json \\
      --report traces/tls/idle.report.json \\
      --max-risk-score 0.70 \\
      --output-prompt /tmp/next_patch_prompt.txt \\
      --output-json /tmp/patch_loop_result.json

Exit codes:
    0 — all detection gates passed (no patch needed)
    1 — some gates failed, next patch prompt generated
    2 — input error
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .detector_report import DetectionReport
from .gate import DetectionThresholds, evaluate_report_file, evaluate_report_files
from .countermeasure_policy import CountermeasureHint, suggest_countermeasures
from .prompt_builder import build_adversarial_patch_prompt


@dataclass
class PatchLoopInput:
    """Input configuration for a patch loop iteration."""

    user_request: str
    repo_status: str = ""
    functional_test_summary: str = ""
    report_paths: list[str] = field(default_factory=list)
    thresholds: DetectionThresholds | None = None
    patch_protocol: str | None = None
    max_iterations: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_request": self.user_request,
            "repo_status": self.repo_status,
            "functional_test_summary": self.functional_test_summary,
            "report_paths": self.report_paths,
            "thresholds": self.thresholds.to_dict() if self.thresholds else None,
            "max_iterations": self.max_iterations,
        }


@dataclass
class PatchLoopResult:
    """Result of a patch loop iteration."""

    should_request_patch: bool
    prompt: str = ""
    failed_reports: list[DetectionReport] = field(default_factory=list)
    passed_reports: list[DetectionReport] = field(default_factory=list)
    hints: list[CountermeasureHint] = field(default_factory=list)
    input_config: PatchLoopInput | None = None
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_request_patch": self.should_request_patch,
            "prompt_length": len(self.prompt),
            "failed_reports": [r.to_dict() for r in self.failed_reports],
            "passed_reports": [r.to_dict() for r in self.passed_reports],
            "hints": [h.to_dict() for h in self.hints],
            "summary": self.summary,
        }


def _get_repo_status() -> str:
    """Collect current git status for the prompt."""
    lines = []
    try:
        r = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, timeout=10,
        )
        lines.append("git status --short:")
        lines.append(r.stdout.strip() or "(clean)")
    except Exception as e:
        lines.append(f"git status unavailable: {e}")

    try:
        r = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            capture_output=True, text=True, timeout=10,
        )
        lines.append("")
        lines.append("Recent commits:")
        lines.append(r.stdout.strip())
    except Exception:
        pass

    return "\n".join(lines)


def build_patch_loop_input(
    user_request: str,
    report_paths: list[str],
    repo_status: str | None = None,
    functional_test_summary: str = "",
    thresholds: DetectionThresholds | None = None,
    patch_protocol: str | None = None,
    max_iterations: int = 1,
) -> PatchLoopInput:
    """Build a PatchLoopInput from parameters."""
    if repo_status is None:
        repo_status = _get_repo_status()
    if thresholds is None:
        thresholds = DetectionThresholds()
    return PatchLoopInput(
        user_request=user_request,
        repo_status=repo_status,
        functional_test_summary=functional_test_summary,
        report_paths=list(report_paths),
        thresholds=thresholds,
        patch_protocol=patch_protocol,
        max_iterations=max_iterations,
    )


def prepare_next_patch_prompt(input_config: PatchLoopInput) -> PatchLoopResult:
    """Run detection gates on all reports and generate the next patch prompt.

    Args:
        input_config: PatchLoopInput with report paths, thresholds, etc.

    Returns:
        PatchLoopResult with should_request_patch, prompt, hints, and summary.
    """
    thresholds = input_config.thresholds or DetectionThresholds()

    # Evaluate all reports
    all_reports = evaluate_report_files(input_config.report_paths, thresholds)

    failed = [r for r in all_reports if not r.passed]
    passed = [r for r in all_reports if r.passed]

    # Collect all countermeasure hints from failed reports
    all_hints: list[CountermeasureHint] = []
    seen_hint_names: set[str] = set()
    for report in failed:
        hints = suggest_countermeasures(report)
        for h in hints:
            if h.metric_name not in seen_hint_names:
                all_hints.append(h)
                seen_hint_names.add(h.metric_name)

    should_request_patch = len(failed) > 0
    prompt = ""

    if should_request_patch:
        # Build a merged report from all failed reports
        # Deduplicate metrics by (name, transport) and keep notes unique
        primary = failed[0] if failed else all_reports[0]
        seen_metrics: set[tuple[str, str | None]] = set()
        seen_notes: set[str] = set()

        # Reset metrics and notes, re-add from all failed reports (deduplicated)
        merged_metrics: list = []
        merged_notes: list[str] = []
        transports = []

        for report in failed:
            if report.transport and report.transport not in transports:
                transports.append(report.transport)
            for m in report.metrics:
                key = (m.name, report.transport)
                if key not in seen_metrics:
                    seen_metrics.add(key)
                    merged_metrics.append(m)
            for note in report.notes:
                if note not in seen_notes:
                    seen_notes.add(note)
                    merged_notes.append(note)

        primary.metrics = merged_metrics
        primary.notes = merged_notes
        if transports:
            primary.transport = ", ".join(transports)
        primary.source_path = f"{len(failed)} failed report(s)"

        prompt = build_adversarial_patch_prompt(
            user_request=input_config.user_request,
            repo_status=input_config.repo_status,
            functional_test_summary=input_config.functional_test_summary,
            detection_report=primary,
            countermeasure_hints=all_hints,
            patch_protocol=input_config.patch_protocol,
        )

    failed_count = len(failed)
    passed_count = len(passed)
    total_count = len(all_reports)

    summary = {
        "total_reports": total_count,
        "passed": passed_count,
        "failed": failed_count,
        "should_request_patch": should_request_patch,
        "failed_metrics_summary": _summarize_failed_metrics(failed),
    }

    return PatchLoopResult(
        should_request_patch=should_request_patch,
        prompt=prompt,
        failed_reports=failed,
        passed_reports=passed,
        hints=all_hints,
        input_config=input_config,
        summary=summary,
    )


def _summarize_failed_metrics(failed_reports: list[DetectionReport]) -> list[dict[str, Any]]:
    """Build a compact summary of all failed metrics across reports."""
    summary: list[dict[str, Any]] = []
    for report in failed_reports:
        for m in report.metrics:
            if not m.passed:
                summary.append({
                    "report": report.source_path,
                    "transport": report.transport,
                    "scenario": report.scenario,
                    "metric": m.name,
                    "value": m.value,
                    "threshold": m.threshold,
                    "severity": m.severity,
                })
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="VPN-LLM Detection Patch Loop — evaluate detection gates and generate patch prompts.",
    )
    parser.add_argument(
        "--user-request", required=True,
        help="Natural language request describing the goal.",
    )
    parser.add_argument(
        "--repo-status-file", default=None,
        help="Optional file containing git status / repo state.",
    )
    parser.add_argument(
        "--functional-test-summary", default="",
        help="Summary of functional test results.",
    )
    parser.add_argument(
        "--report", action="append", dest="reports", default=[],
        help="Path to fingerprint report JSON (repeatable).",
    )
    parser.add_argument(
        "--max-risk-score", type=float, default=0.70,
        help="Maximum allowed risk score (default: 0.70).",
    )
    parser.add_argument(
        "--max-repeated-length-ratio", type=float, default=None,
        help="Maximum repeated length ratio.",
    )
    parser.add_argument(
        "--max-small-packet-ratio", type=float, default=None,
        help="Maximum small packet ratio.",
    )
    parser.add_argument(
        "--min-ngram-entropy", type=float, default=None,
        help="Minimum ngram entropy.",
    )
    parser.add_argument(
        "--max-dominant-ngram-ratio", type=float, default=None,
        help="Maximum dominant ngram ratio.",
    )
    parser.add_argument(
        "--max-dominant-burst-dir-ratio", type=float, default=None,
        help="Maximum dominant burst direction ratio.",
    )
    parser.add_argument(
        "--fail-on-insufficient-data", action="store_true",
        help="Fail (rather than warn) on insufficient_data.",
    )
    parser.add_argument(
        "--output-prompt", default=None,
        help="Write the generated patch prompt to this file.",
    )
    parser.add_argument(
        "--output-json", default=None,
        help="Write the PatchLoopResult JSON to this file.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    # Validate inputs
    if not args.reports:
        print("Error: at least one --report is required", file=sys.stderr)
        sys.exit(2)

    for rp in args.reports:
        if not os.path.isfile(rp):
            print(f"Error: report file not found: {rp}", file=sys.stderr)
            sys.exit(2)

    # Build thresholds from CLI args
    thresholds = DetectionThresholds(
        max_risk_score=args.max_risk_score,
        max_repeated_length_ratio=args.max_repeated_length_ratio,
        max_small_packet_ratio=args.max_small_packet_ratio,
        min_ngram_entropy=args.min_ngram_entropy,
        max_dominant_ngram_ratio=args.max_dominant_ngram_ratio,
        max_dominant_burst_direction_ratio=args.max_dominant_burst_dir_ratio,
        fail_on_insufficient_data=args.fail_on_insufficient_data,
    )

    # Read repo status
    repo_status = ""
    if args.repo_status_file:
        try:
            repo_status = Path(args.repo_status_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"Error reading repo status file: {e}", file=sys.stderr)
            sys.exit(2)
    else:
        repo_status = _get_repo_status()

    # Build input and run
    input_config = build_patch_loop_input(
        user_request=args.user_request,
        report_paths=args.reports,
        repo_status=repo_status,
        functional_test_summary=args.functional_test_summary,
        thresholds=thresholds,
    )

    result = prepare_next_patch_prompt(input_config)

    # Write outputs
    if args.output_prompt and result.prompt:
        Path(args.output_prompt).write_text(result.prompt, encoding="utf-8")
        print(f"Patch prompt written to: {args.output_prompt}")

    if args.output_json:
        data = result.to_dict()
        # Include input config in JSON output
        data["input_config"] = input_config.to_dict()
        Path(args.output_json).write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Result JSON written to: {args.output_json}")

    # Print summary
    print()
    s = result.summary
    print(f"Reports: {s['total_reports']} total, {s['passed']} passed, {s['failed']} failed")

    if result.should_request_patch:
        print(f"Detection gate: FAILED — {s['failed']} report(s) have failed metrics")
        print("Next patch prompt generated.")
        if result.failed_reports:
            for fr in result.failed_reports:
                failed_names = [m.name for m in fr.metrics if not m.passed]
                print(f"  {fr.source_path}: {', '.join(failed_names)}")
    else:
        print("Detection gate: ALL PASSED — no patch needed")

    # Exit codes
    if result.should_request_patch:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
