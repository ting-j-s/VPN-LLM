#!/usr/bin/env python3
"""LLM Task CLI entry point.

Usage:
    python3 scripts/llm_task.py --request "switch default transport to WebSocket"

MVP: plan + validation only. Does NOT generate or apply code changes.
"""

import argparse
import os
import sys

# Ensure the project root is on the Python path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.llm.task_planner import TaskPlanner, TASK_UNKNOWN
from src.llm.safety_guard import SafetyGuard, SafetyError
from src.llm.validation_runner import ValidationRunner


def build_report(plan, compile_result, test_result, full_result, git_result):
    """Build a structured text report."""
    lines = []
    lines.append("=" * 60)
    lines.append("LLM Task Report")
    lines.append("=" * 60)
    lines.append("")
    lines.append("--- Task Plan ---")
    lines.append(f"  Type:        {plan.task_type}")
    lines.append(f"  Transport:   {plan.target_transport or 'N/A'}")
    lines.append(f"  Areas:       {', '.join(plan.affected_areas) if plan.affected_areas else 'N/A'}")
    lines.append(f"  Description: {plan.description}")
    lines.append("")

    def _result_block(label, result):
        lines.append(f"--- {label} ---")
        lines.append(f"  Command:  {result.command}")
        lines.append(f"  Exit:     {result.returncode}")
        lines.append(f"  Passed:   {result.success}")
        if result.stderr:
            lines.append(f"  Stderr:   {result.stderr[:200]}")
        lines.append("")

    _result_block("Compile Check", compile_result)
    _result_block("Git Status", git_result)

    if test_result is not None:
        _result_block("Targeted Tests", test_result)

    _result_block("Full Test Suite", full_result)

    all_pass = compile_result.success and full_result.success
    if test_result is not None:
        all_pass = all_pass and test_result.success

    lines.append("--- Summary ---")
    lines.append(f"  All checks passed: {all_pass}")
    lines.append("  MVP mode: plan + validation only (no code changes applied)")
    lines.append("=" * 60)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="LLM Task Agent - MVP (plan + validate only)"
    )
    parser.add_argument(
        "--request", required=True,
        help="Natural language request, e.g. 'switch default transport to WebSocket'"
    )
    args = parser.parse_args()

    print(f"Request: {args.request}")
    print()

    # 1. Plan
    planner = TaskPlanner()
    plan = planner.plan(args.request)
    print(f"Task type: {plan.task_type}")
    print(f"Target transport: {plan.target_transport or 'N/A'}")
    print(f"Affected areas: {', '.join(plan.affected_areas) if plan.affected_areas else 'N/A'}")
    print()

    if plan.task_type == TASK_UNKNOWN:
        print("Warning: could not classify request. Proceeding with validation only.")
        print()

    # 2. Safety check on the request itself
    try:
        SafetyGuard.validate_command(args.request)
        SafetyGuard.validate_write_path("config/server.yaml")
        SafetyGuard.validate_write_path("config/client.yaml")
        SafetyGuard.validate_write_path("tests/test_websocket_transport.py")
    except SafetyError as e:
        print(f"SAFETY BLOCK: {e}")
        sys.exit(1)

    # 3. Validate safety on planned affected areas
    print("Validating safety of affected paths...")
    for area in plan.affected_areas:
        try:
            SafetyGuard.validate_write_path(area)
        except SafetyError as e:
            print(f"  BLOCKED: {e}")
    print("  Safety checks passed for affected areas.")
    print()

    # 4. Run validation
    runner = ValidationRunner()
    print("Running compileall...")
    compile_result = runner.run_compileall()

    test_result = None
    if plan.affected_areas and any("test" in a for a in plan.affected_areas):
        print("Running targeted tests...")
        test_result = runner.run_targeted_tests(["tests/"])
    elif plan.target_transport:
        test_file = f"tests/test_{plan.target_transport}_transport.py"
        import os as _os
        if _os.path.exists(os.path.join(_project_root, test_file)):
            print(f"Running targeted tests: {test_file}")
            test_result = runner.run_targeted_tests([test_file])

    print("Running full test suite...")
    full_result = runner.run_full_tests()

    print("Running git status...")
    git_result = runner.run_git_status()

    # 5. Report
    report = build_report(plan, compile_result, test_result, full_result, git_result)
    print()
    print(report)

    # Exit with the right code
    if not compile_result.success or not full_result.success:
        sys.exit(1)


if __name__ == "__main__":
    main()
