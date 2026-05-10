#!/usr/bin/env python3
"""LLM Task CLI entry point.

Usage:
    # Rule-based planner (default, no network)
    python3 scripts/llm_task.py --request "switch default transport to WebSocket"

    # LLM-based planner (needs LLM_API_KEY env var and config/llm_agent.yaml)
    python3 scripts/llm_task.py --request "..." --use-llm-planner

    # Custom record directory
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


def load_llm_planner(config_path: str):
    """Dynamically load LLMTaskPlanner (avoids import-time errors)."""
    from src.llm.llm_task_planner import LLMTaskPlanner, LLMTaskPlannerError

    try:
        return LLMTaskPlanner(config_path)
    except LLMTaskPlannerError as e:
        print(f"LLM planner init failed: {e}")
        sys.exit(1)


def load_patch_generator(config_path: str):
    """Dynamically load LLMPatchGenerator (avoids import-time errors)."""
    from src.llm.patch_generator import LLMPatchGenerator, LLMPatchGeneratorError

    try:
        return LLMPatchGenerator(config_path)
    except LLMPatchGeneratorError as e:
        print(f"Patch generator init failed: {e}")
        sys.exit(1)


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
    parser.add_argument(
        "--use-llm-planner", action="store_true",
        help="Use LLM-based planner instead of rule-based (needs config/llm_agent.yaml)"
    )
    parser.add_argument(
        "--llm-config", default="config/llm_agent.yaml",
        help="Path to LLM agent config (default: config/llm_agent.yaml)"
    )
    parser.add_argument(
        "--generate-patch", action="store_true",
        help="Generate unified diff via LLM (dry-run: saved to patch.diff, NOT applied). Requires --use-llm-planner."
    )
    args = parser.parse_args()

    if args.generate_patch and not args.use_llm_planner:
        print("Error: --generate-patch requires --use-llm-planner")
        sys.exit(1)

    print(f"Request: {args.request}")
    print()

    # 1. Plan — rule-based or LLM-based
    planner_type = "llm_based" if args.use_llm_planner else "rule_based"
    print(f"Planner: {planner_type}")

    if args.use_llm_planner:
        llm_planner = load_llm_planner(args.llm_config)
        plan = llm_planner.plan(args.request)
    else:
        rule_planner = TaskPlanner()
        plan = rule_planner.plan(args.request)

    print(f"Task type: {plan.task_type}")
    print(f"Target transport: {plan.target_transport or 'N/A'}")
    print(f"Affected areas: {', '.join(plan.affected_areas) if plan.affected_areas else 'N/A'}")
    if planner_type == "llm_based":
        print(f"Risk level: {plan.risk_level}")
        print(f"Summary: {plan.summary}")
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
    record_mgr.save_plan(task_id, plan, planner_type=planner_type)
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

    # Also run LLM-suggested validation commands if available (only safe ones)
    llm_commands = getattr(plan, "validation_commands", [])
    if llm_commands and planner_type == "llm_based":
        print("Running LLM-suggested validation commands...")
        for cmd in llm_commands:
            # SafetyGuard re-check (belt and suspenders)
            try:
                SafetyGuard.validate_command(cmd)
            except SafetyError:
                print(f"  Skipping unsafe LLM command: {cmd[:80]}")
                continue
            r = runner.run_command(cmd)
            status = "PASS" if r.success else f"FAIL (rc={r.returncode})"
            print(f"  [{status}] {cmd[:80]}")

    print("Running full test suite...")
    full_result = runner.run_full_tests()

    print("Running git status...")
    git_result = runner.run_git_status()

    # 6. Patch generation (optional, dry-run only)
    patch_text = None
    patch_file_paths = None
    git_apply_check_result = None

    if args.generate_patch:
        from src.llm.patch_generator import LLMPatchGeneratorError

        print()
        print("=== Patch Generation (dry-run) ===")

        patch_gen = load_patch_generator(args.llm_config)

        # Build lightweight repository context
        repo_context_lines = [
            f"Task type: {plan.task_type}",
            f"Target transport: {plan.target_transport or 'N/A'}",
            "Affected areas:",
        ]
        for area in plan.affected_areas:
            repo_context_lines.append(f"  - {area}")
        repo_context = "\n".join(repo_context_lines)

        try:
            patch_text = patch_gen.generate(args.request, plan, repo_context)
            print("Patch generated successfully")
        except LLMPatchGeneratorError as e:
            print(f"Patch generation failed: {e}")
            sys.exit(1)

        # Save patch.diff
        record_mgr.save_patch(task_id, patch_text)
        patch_path = os.path.join(record_mgr.get_task_dir(task_id), "patch.diff")
        print(f"Patch saved to: {patch_path}")

        # Parse file paths from patch for reporting
        from src.llm.patch_generator import LLMPatchGenerator as PG
        patch_file_paths = PG._parse_file_paths(patch_text)
        print(f"Files in patch: {', '.join(patch_file_paths) if patch_file_paths else '(none)'}")

        # git apply --check (dry-run only, does NOT apply)
        print("Running git apply --check...")
        git_apply_check_result = runner.run_git_apply_check(patch_path)
        if git_apply_check_result.success:
            print("  git apply --check: PASS (patch would apply cleanly)")
        else:
            print(f"  git apply --check: FAIL (returncode={git_apply_check_result.returncode})")
            if git_apply_check_result.stderr:
                print(f"  {git_apply_check_result.stderr.strip()[:500]}")

        print()
        print(">>> PATCH WAS NOT APPLIED. Review patch.diff manually before applying. <<<")
        print()

    # 7. Generate report
    report = write_report(
        task_id=task_id,
        request=args.request,
        plan=plan,
        compile_result=compile_result,
        targeted_result=test_result,
        full_result=full_result,
        git_result=git_result,
        planner_type=planner_type,
        patch_text=patch_text,
        patch_file_paths=patch_file_paths,
        git_apply_check_result=git_apply_check_result,
    )

    # 8. Save all artifacts
    record_mgr.save_validation_result(task_id, {
        "compile": compile_result,
        "targeted_tests": test_result,
        "full_tests": full_result,
        "git_status": git_result,
        "git_apply_check": git_apply_check_result,
    })

    all_pass = compile_result.success and full_result.success
    if test_result is not None:
        all_pass = all_pass and test_result.success
    if git_apply_check_result is not None:
        all_pass = all_pass and git_apply_check_result.success

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
