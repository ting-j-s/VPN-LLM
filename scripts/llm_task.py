#!/usr/bin/env python3
"""LLM Task CLI entry point.

Usage:
    python3 scripts/llm_task.py --request "switch default transport to WebSocket"
    python3 scripts/llm_task.py --request "..." --record-dir .llm_tasks

MVP: plan + validation only. Does NOT generate or apply code changes,
does NOT commit, does NOT push.
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
from src.llm.task_record import TaskRecordManager
from src.llm.report_writer import write_report


def main():
    parser = argparse.ArgumentParser(
        description="LLM Task Agent - MVP (plan + validate only)"
    )
    parser.add_argument(
        "--request", required=True,
        help="Natural language request, e.g. 'switch default transport to WebSocket'"
    )
    parser.add_argument(
        "--record-dir", default=".llm_tasks",
        help="Base directory for task records (default: .llm_tasks)"
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

    # 4. Initialize task record
    record_mgr = TaskRecordManager(args.record_dir)
    task_id = record_mgr.create_task(args.request)
    record_mgr.save_plan(task_id, plan)
    print(f"Task record: {record_mgr.get_task_dir(task_id)}")
    print()

    # 5. Run validation
    runner = ValidationRunner()

    print("Running compileall...")
    compile_result = runner.run_compileall()

    test_result = None
    if plan.affected_areas and any("test" in a for a in plan.affected_areas):
        print("Running targeted tests...")
        test_result = runner.run_targeted_tests(["tests/"])
    elif plan.target_transport:
        test_file = f"tests/test_{plan.target_transport}_transport.py"
        if os.path.exists(os.path.join(_project_root, test_file)):
            print(f"Running targeted tests: {test_file}")
            test_result = runner.run_targeted_tests([test_file])

    print("Running full test suite...")
    full_result = runner.run_full_tests()

    print("Running git status...")
    git_result = runner.run_git_status()

    # 6. Generate report
    report = write_report(
        task_id=task_id,
        request=args.request,
        plan=plan,
        compile_result=compile_result,
        targeted_result=test_result,
        full_result=full_result,
        git_result=git_result,
    )

    # 7. Save all artifacts
    record_mgr.save_validation_result(task_id, {
        "compile": compile_result,
        "targeted_tests": test_result,
        "full_tests": full_result,
        "git_status": git_result,
    })

    all_pass = compile_result.success and full_result.success
    if test_result is not None:
        all_pass = all_pass and test_result.success

    record_mgr.update_status(task_id, "completed" if all_pass else "failed", all_passed=all_pass)
    record_mgr.save_report(task_id, report)

    print()
    print(report)
    print()
    print(f"Report saved to: {os.path.join(record_mgr.get_task_dir(task_id), 'report.md')}")

    if not all_pass:
        sys.exit(1)


if __name__ == "__main__":
    main()
