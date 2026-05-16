#!/usr/bin/env python3
"""LLM Task CLI entry point.

Usage:
    # Rule-based planner (default, no network)
    python3 scripts/llm_task.py --request "switch default transport to WebSocket"

    # LLM-based planner (needs LLM_API_KEY env var and config/llm_agent.yaml)
    python3 scripts/llm_task.py --request "..." --use-llm-planner

    # Custom record directory
    python3 scripts/llm_task.py --request "..." --record-dir .llm_tasks

The agentized flow:
  1. TaskPlan (rule-based or LLM-based)
  2. RepoIndexer.build() — local static analysis, no LLM
  3. FileRetriever.retrieve() — multi-strategy recall from the index
  4. ImpactExpander.expand() — impact analysis → FileSelection
  5. ContextBuilder.build() — read selected files for LLM context
  6. PatchGenerator.generate() — LLM writes patches constrained by FileSelection

LLM is NOT responsible for guessing which files to edit — that is handled
by the local index → retrieve → expand pipeline.
"""

import argparse
import json
import os
import sys

# Ensure the project root is on the Python path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.llm.task_planner import TaskPlanner, TASK_UNKNOWN
from src.llm.safety_guard import SafetyGuard, SafetyError
from src.llm.validation_runner import ValidationRunner, DirtyWorktreeError
from src.llm.task_record import TaskRecordManager
from src.llm.report_writer import write_report


# ---------------------------------------------------------------------------
# Risk control
# ---------------------------------------------------------------------------

_EXTREME_RISK_KEYWORDS = [
    ".git/", ".env", "*.key", "*.pem",
    "sudo", "rm -rf", "curl | sh", "curl|sh", "wget | sh",
    "auto commit", "auto push", "automatic commit", "automatic push",
    "git push", "force push",
]


def _is_extreme_risk(request: str) -> bool:
    """Check if a request involves extreme-risk operations."""
    request_lower = request.lower()
    for kw in _EXTREME_RISK_KEYWORDS:
        if kw in request_lower:
            return True
    return False


def _write_clarification_questions(task_dir: str, request: str) -> None:
    """Write clarification_questions.md for extreme-risk tasks."""
    lines = [
        "# Clarification Questions",
        "",
        "The following request was classified as **extreme risk** and was NOT executed.",
        "",
        f"**Request**: {request}",
        "",
        "## Risk Factors",
        "",
        "The request matched one or more extreme-risk patterns:",
        "",
    ]
    request_lower = request.lower()
    for kw in _EXTREME_RISK_KEYWORDS:
        if kw in request_lower:
            lines.append(f"- `{kw}`")
    lines.extend([
        "",
        "## Required Clarifications",
        "",
        "1. What specific files or directories need to be modified?",
        "2. Is there a safer alternative that achieves the same goal?",
        "3. Has this change been reviewed by a human?",
        "4. What is the rollback plan if something goes wrong?",
        "",
        "> This task was blocked by the LLM Agent safety system. "
        "Please clarify and re-submit with appropriate safeguards.",
    ])
    filepath = os.path.join(task_dir, "clarification_questions.md")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


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


def _validate_llm_commands(commands: list[str]) -> list[str]:
    """Filter out LLM-suggested commands that reference non-existent files.

    LLM planners may hallucinate test paths (e.g., tests/test_e2e.py).
    This keeps only commands whose referenced files exist on disk.
    """
    import re

    kept = []
    for cmd in commands:
        # Extract file-path-like arguments from the command
        parts = cmd.split()
        ok = True
        for part in parts:
            if not part.endswith(".py"):
                continue
            candidate = os.path.join(_project_root, part)
            if not os.path.exists(candidate):
                ok = False
                break
        if ok:
            kept.append(cmd)
        else:
            print(f"  Skipping LLM-suggested command (missing file): {cmd[:80]}")
    return kept


def _check_artifact_coverage(request: str, task_type: str,
                            patch_file_paths: list[str] | None,
                            plan_affected_areas: list[str] | None = None) -> list[str]:
    """Check whether the patch covers expected artifacts from the request.

    Returns a list of warning strings. Empty list means full coverage or
    no expected artifacts detected.
    """
    import re

    warnings = []
    if not patch_file_paths:
        return warnings

    patch_paths_lower = [p.lower() for p in patch_file_paths]
    request_lower = request.lower()

    # Only apply coverage checks for transport_addition, core_change,
    # config_change, and mixed_feature task types
    if task_type not in ("transport_addition", "core_change",
                         "config_change", "mixed_feature", "unknown"):
        return warnings

    # Detect transport name from request
    transport_name = None
    m = re.search(r'(?:add|new|create|implement)\s+(?:a\s+)?(?:new\s+)?(\w+)\s+transport', request_lower)
    if m:
        transport_name = m.group(1)

    # Check for tests
    if any(kw in request_lower for kw in ("test", "tests")):
        has_test = any("test_" in p for p in patch_paths_lower)
        if not has_test:
            if transport_name:
                warnings.append(
                    f"Request mentions tests but no test file was generated "
                    f"(expected: tests/test_{transport_name}_transport.py)"
                )
            else:
                warnings.append(
                    "Request mentions tests but no test file was generated"
                )

    # Check for docs
    if any(kw in request_lower for kw in ("doc", "docs", "documentation")):
        has_doc = any(p.endswith(".md") for p in patch_paths_lower)
        if not has_doc:
            if transport_name:
                warnings.append(
                    f"Request mentions docs but no .md file was generated "
                    f"(expected: docs/{transport_name}_transport.md)"
                )
            else:
                warnings.append(
                    "Request mentions docs but no .md file was generated"
                )

    # Check for config support
    if any(kw in request_lower for kw in ("config", "configuration")):
        has_config = any(
            "config" in p and (p.endswith(".yaml") or p.endswith(".yml") or p.endswith(".json"))
            for p in patch_paths_lower
        )
        has_config_py = any(
            "config.py" in p or "config." in p
            for p in patch_paths_lower
        )
        if not has_config and not has_config_py:
            if transport_name:
                warnings.append(
                    f"Request mentions config support but no config file or "
                    f"config.py modification was generated "
                    f"(expected: config/{transport_name}_transport.yaml "
                    f"or modification to src/common/config.py)"
                )
            else:
                warnings.append(
                    "Request mentions config support but no config file or "
                    "config.py modification was generated"
                )

    return warnings


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
    parser.add_argument(
        "--apply-patch", action="store_true",
        help="Apply the generated patch after git apply --check passes. Requires --generate-patch. Still no auto-commit or auto-push."
    )
    parser.add_argument(
        "--allow-dirty-worktree", action="store_true",
        help="Allow patch application even if the working tree has uncommitted changes."
    )
    parser.add_argument(
        "--suggest-commit", action="store_true",
        help="Generate commit message suggestion after successful patch apply. Requires --apply-patch. No auto-commit or auto-push."
    )
    parser.add_argument(
        "--run-replacement-smoke", action="store_true",
        help="Run replacement smoke matrix after successful patch apply. Requires --apply-patch."
    )
    parser.add_argument(
        "--replacement-smoke-timeout", type=int, default=5,
        help="Timeout for replacement smoke checks (default: 5)."
    )
    parser.add_argument(
        "--include-tls-smoke", action="store_true",
        help="Always include TLS in replacement smoke matrix."
    )
    parser.add_argument(
        "--include-ssh-smoke", action="store_true",
        help="Include SSH in replacement smoke matrix (expected skip without sshd)."
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print LLM API request/response details (prompt, response content) to stderr."
    )
    args = parser.parse_args()

    if args.verbose:
        os.environ["VPN_LLM_VERBOSE"] = "1"

    if args.generate_patch and not args.use_llm_planner:
        print("Error: --generate-patch requires --use-llm-planner")
        sys.exit(1)

    if args.apply_patch and not args.generate_patch:
        print("Error: --apply-patch requires --generate-patch")
        sys.exit(1)

    if args.suggest_commit and not args.apply_patch:
        print("Error: --suggest-commit requires --apply-patch")
        sys.exit(1)

    if args.run_replacement_smoke and not args.apply_patch:
        print("Error: --run-replacement-smoke requires --apply-patch")
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

    # 2. Basic safety check on path patterns (not the request command itself —
    #    that is checked after the extreme risk gate)
    try:
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
    task_dir = record_mgr.get_task_dir(task_id)
    print(f"Task record: {task_dir}")
    print()

    # ---- Extreme risk control ----
    if _is_extreme_risk(args.request):
        print("=== EXTREME RISK DETECTED ===")
        print("This request matches extreme-risk patterns (e.g., .git/, .env, sudo, rm -rf, auto-push).")
        print("Execution stopped. Generating clarification questions.")
        _write_clarification_questions(task_dir, args.request)
        # Generate a minimal report
        report = write_report(
            task_id=task_id, request=args.request, plan=plan,
            compile_result=None, targeted_result=None,
            full_result=None, git_result=None,
            planner_type=planner_type,
        )
        record_mgr.save_report(task_id, report)
        print(f"Clarification questions written to: {task_dir}/clarification_questions.md")
        print(">>> Task was NOT executed. Please clarify and re-submit. <<<")
        sys.exit(1)

    # ---- Safety command check (after extreme risk gate) ----
    try:
        SafetyGuard.validate_command(args.request)
    except SafetyError as e:
        print(f"SAFETY BLOCK: {e}")
        sys.exit(1)

    # ---- File selection pipeline (no LLM involved) ----
    file_selection = None
    repo_context = ""
    context_summary = None

    if args.generate_patch:
        from src.llm.repo_indexer import RepoIndexer
        from src.llm.file_retriever import FileRetriever
        from src.llm.impact_expander import ImpactExpander
        from src.llm.context_builder import ContextBuilder

        print("=== File Selection Pipeline ===")
        print()

        # 4a. Build repo index (scan cwd so tests can use temp repos)
        print("Building repo index...")
        indexer = RepoIndexer(os.getcwd())
        repo_index = indexer.build()
        print(f"  Files indexed: {repo_index.file_count} "
              f"({repo_index.python_count} python, {repo_index.test_count} tests, "
              f"{repo_index.doc_count} docs)")

        # Save index summary
        record_mgr._write_file(task_id, "repo_index_summary.json",
                               json.dumps(repo_index.summary(), indent=2))

        # 4b. Multi-strategy file retrieval
        print("Retrieving candidate files...")
        retriever = FileRetriever(repo_index)
        candidates = retriever.retrieve(args.request, plan)
        print(f"  Candidates retrieved: {len(candidates)}")
        for c in candidates[:10]:
            print(f"    [{c.action}] {c.path} (score={c.score:.2f}, sources={c.sources})")
        if len(candidates) > 10:
            print(f"    ... and {len(candidates) - 10} more")

        # Save retrieval results
        record_mgr._write_file(task_id, "file_retrieval.json",
                               json.dumps([c.to_dict() for c in candidates], indent=2))

        # 4c. Impact expansion
        print("Expanding impact...")
        expander = ImpactExpander(repo_index)
        file_selection = expander.expand(args.request, plan, candidates)
        print(f"  Must edit: {len(file_selection.must_edit_files)} files")
        print(f"  Must review: {len(file_selection.must_review_files)} files")
        print(f"  Test files: {len(file_selection.test_files)}")
        print(f"  Doc files: {len(file_selection.doc_files)}")
        print(f"  Allowed create paths: {file_selection.allowed_create_paths}")
        if file_selection.rejected_hints:
            print(f"  Rejected hints (not on disk): {file_selection.rejected_hints}")

        # Save impact analysis and selection
        record_mgr._write_file(task_id, "impact_analysis.json",
                               json.dumps({
                                   "affected_area_keys": list(set(
                                       c.sources[0] if c.sources else "unknown"
                                       for c in candidates
                                   )),
                                   "candidate_count": len(candidates),
                                   "must_edit_count": len(file_selection.must_edit_files),
                                   "must_review_count": len(file_selection.must_review_files),
                               }, indent=2))
        record_mgr._write_file(task_id, "file_selection.json",
                               json.dumps(file_selection.to_dict(), indent=2))

        # Check: no editable files found → stop
        if not file_selection.has_any_edits and not file_selection.allowed_create_paths:
            print()
            print(">>> No editable files were selected. Cannot determine modification scope. <<<")
            print(">>> The request may be too vague or the affected area is not in the index. <<<")
            # Write report and exit
            report = write_report(
                task_id=task_id, request=args.request, plan=plan,
                compile_result=None, targeted_result=None,
                full_result=None, git_result=None,
                planner_type=planner_type,
            )
            record_mgr.save_report(task_id, report)
            record_mgr.update_status(task_id, "failed", all_passed=False)
            sys.exit(1)

        # 4d. Build context for LLM (use cwd so tests work with temp repos)
        print("Building repository context...")
        builder = ContextBuilder(os.getcwd())
        repo_context, context_summary = builder.build(file_selection)
        print(f"  Context size: {context_summary.total_bytes} bytes "
              f"({len(context_summary.files_included)} files)")

        # Save context summary
        record_mgr._write_file(task_id, "context_summary.json",
                               json.dumps(context_summary.to_dict(), indent=2))
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
    llm_validation_results = []
    if llm_commands and planner_type == "llm_based":
        llm_commands = _validate_llm_commands(llm_commands)
        print("Running LLM-suggested validation commands...")
        for cmd in llm_commands:
            # SafetyGuard re-check (belt and suspenders)
            try:
                SafetyGuard.validate_command(cmd)
            except SafetyError:
                print(f"  Skipping unsafe LLM command: {cmd[:80]}")
                continue
            r = runner.run_command(cmd)
            llm_validation_results.append(r)
            status = "PASS" if r.success else f"FAIL (rc={r.returncode})"
            print(f"  [{status}] {cmd[:80]}")

    print("Running full test suite...")
    full_result = runner.run_full_tests()

    print("Running git status...")
    git_result = runner.run_git_status()

    # 6. Patch generation (optional, dry-run only)
    patch_text = None
    patch_file_paths = None
    artifact_coverage_warnings = None
    git_apply_check_result = None
    protocol_retry_count = 0
    protocol_retry_used = False
    semantic_retry_count = 0
    semantic_retry_used = False
    patch_generation_error = None

    if args.generate_patch:
        from src.llm.patch_generator import LLMPatchGeneratorError

        print()
        print("=== Patch Generation (dry-run) ===")

        patch_gen = load_patch_generator(args.llm_config)

        # Use context from ContextBuilder if available, otherwise fall back to legacy path
        if repo_context:
            print("Using file-selection-based context.")
        else:
            # Legacy fallback: build minimal context from plan.candidate_files
            print("Warning: file selection pipeline not run — using legacy context.")
            repo_context_lines = [
                f"Task type: {plan.task_type}",
                f"Target transport: {plan.target_transport or 'N/A'}",
                "Affected areas:",
            ]
            for area in plan.affected_areas:
                repo_context_lines.append(f"  - {area}")
            repo_context_lines.append("")
            repo_context_lines.append("File contents (for exact FIND matching):")
            max_file_bytes = 8192
            for fpath in getattr(plan, "candidate_files", []) or []:
                if not os.path.isfile(fpath):
                    continue
                ext = os.path.splitext(fpath)[1].lower()
                if ext in (".key", ".pem", ".crt"):
                    continue
                try:
                    size = os.path.getsize(fpath)
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read(max_file_bytes)
                    repo_context_lines.append(f"")
                    repo_context_lines.append(f"--- {fpath} ({size} bytes) ---")
                    repo_context_lines.append(content)
                    if size > max_file_bytes:
                        repo_context_lines.append("... (truncated)")
                except Exception:
                    repo_context_lines.append(f"")
                    repo_context_lines.append(f"--- {fpath} (unable to read) ---")
            repo_context = "\n".join(repo_context_lines)

        # Prepare file constraints from file_selection
        allowed_edit_files = None
        allowed_create_paths = None
        allowed_create_patterns = None
        if file_selection is not None:
            allowed_edit_files = list(file_selection.must_edit_files)
            allowed_create_paths = list(file_selection.allowed_create_paths) or None
            allowed_create_patterns = list(file_selection.allowed_create_patterns) or None

        task_dir = record_mgr.get_task_dir(task_id) if record_mgr else None

        try:
            patch_text = patch_gen.generate(
                args.request, plan, repo_context,
                allowed_edit_files=allowed_edit_files,
                allowed_create_paths=allowed_create_paths,
                allowed_create_patterns=allowed_create_patterns,
                task_dir=task_dir,
            )
            print("Patch generated successfully")
            protocol_retry_count = patch_gen.protocol_retry_count
            protocol_retry_used = patch_gen.protocol_retry_used
            semantic_retry_count = patch_gen.semantic_retry_count
            semantic_retry_used = patch_gen.semantic_retry_used
            if protocol_retry_used:
                print(f"  (protocol retry: {protocol_retry_count} attempt(s), first attempt malformed)")
            if semantic_retry_used:
                print(f"  (semantic retry: {semantic_retry_count} attempt(s), validation error corrected)")
        except LLMPatchGeneratorError as e:
            print(f"Patch generation failed: {e}")
            patch_generation_error = str(e)
            protocol_retry_count = getattr(patch_gen, 'protocol_retry_count', 0)
            protocol_retry_used = getattr(patch_gen, 'protocol_retry_used', False)
            semantic_retry_count = getattr(patch_gen, 'semantic_retry_count', 0)
            semantic_retry_used = getattr(patch_gen, 'semantic_retry_used', False)
            if task_dir:
                for fn in ["llm_patch_raw_attempt1.txt", "llm_patch_raw_attempt2.txt",
                           "llm_patch_raw.txt"]:
                    raw_path = os.path.join(task_dir, fn)
                    if os.path.isfile(raw_path):
                        print(f"Raw LLM output saved to: {raw_path}")
            # Continue to write report with failure info; exit later
            patch_text = None

        if patch_text is not None:
            # Save patch.diff
            record_mgr.save_patch(task_id, patch_text)
            patch_path = os.path.join(record_mgr.get_task_dir(task_id), "patch.diff")
            print(f"Patch saved to: {patch_path}")

            # Parse file paths from patch for reporting
            from src.llm.patch_generator import LLMPatchGenerator as PG
            patch_file_paths = PG._parse_file_paths(patch_text)
            print(f"Files in patch: {', '.join(patch_file_paths) if patch_file_paths else '(none)'}")

            # Artifact coverage check
            artifact_coverage_warnings = _check_artifact_coverage(
                args.request, plan.task_type, patch_file_paths,
                plan_affected_areas=getattr(plan, "affected_areas", None),
            )
            if artifact_coverage_warnings:
                print("Artifact coverage warnings:")
                for w in artifact_coverage_warnings:
                    print(f"  Warning: {w}")

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
        else:
            print()
            print(">>> Patch generation failed — no patch.diff was produced. <<<")
            print()

    # 6.5 Patch application (optional, only after explicit --apply-patch)
    apply_result = None
    post_apply_validation = None
    replacement_smoke_result = None

    if args.apply_patch:
        from src.llm.patch_generator import LLMPatchGeneratorError

        print()
        print("=== Patch Application ===")

        # Pre-condition: git apply --check must have passed
        if git_apply_check_result is None or not git_apply_check_result.success:
            print("Error: git apply --check did not pass. Cannot apply patch.")
            print(f"  Check result: {git_apply_check_result}")
            sys.exit(1)

        # Pre-condition: clean worktree (unless --allow-dirty-worktree)
        if not args.allow_dirty_worktree:
            try:
                runner.ensure_clean_worktree()
                print("Working tree is clean.")
            except DirtyWorktreeError as e:
                print(f"Error: {e}")
                print("Use --allow-dirty-worktree to bypass this check.")
                sys.exit(1)
        else:
            print("Warning: --allow-dirty-worktree specified, skipping worktree check.")

        patch_path = os.path.join(record_mgr.get_task_dir(task_id), "patch.diff")

        # Apply the patch
        print(f"Applying patch: {patch_path}")
        apply_result = runner.run_git_apply(patch_path)

        if not apply_result.success:
            print(f"  git apply FAILED (returncode={apply_result.returncode})")
            if apply_result.stderr:
                print(f"  {apply_result.stderr.strip()[:500]}")
            print()
            print(">>> Patch application failed. No files were modified. <<<")
        else:
            print("  git apply: PASS (patch applied successfully)")
            print()
            print(">>> PATCH WAS APPLIED. Running post-apply validation... <<<")
            print()

            # Post-apply validation
            post_apply_validation = {}

            print("Running compileall (post-apply)...")
            post_apply_validation["Compile Check"] = runner.run_compileall()
            status = "PASS" if post_apply_validation["Compile Check"].success else "FAIL"
            print(f"  [{status}] compileall")

            # Run LLM-suggested validation commands first, then fall back to targeted tests
            llm_cmds = getattr(plan, "validation_commands", [])
            if llm_cmds and planner_type == "llm_based":
                llm_cmds = _validate_llm_commands(llm_cmds)
                print("Running LLM-suggested validation commands (post-apply)...")
                for cmd in llm_cmds:
                    try:
                        SafetyGuard.validate_command(cmd)
                    except SafetyError:
                        print(f"  Skipping unsafe LLM command: {cmd[:80]}")
                        continue
                    r = runner.run_command(cmd)
                    post_apply_validation[cmd[:80]] = r
                    status = "PASS" if r.success else f"FAIL (rc={r.returncode})"
                    print(f"  [{status}] {cmd[:80]}")

            if plan.target_transport:
                test_file = f"tests/test_{plan.target_transport}_transport.py"
                if os.path.exists(os.path.join(_project_root, test_file)):
                    print(f"Running targeted tests (post-apply): {test_file}")
                    post_apply_validation["Targeted Tests"] = runner.run_targeted_tests([test_file])
                    status = "PASS" if post_apply_validation["Targeted Tests"].success else "FAIL"
                    print(f"  [{status}] targeted tests")

            print("Running full test suite (post-apply)...")
            post_apply_validation["Full Test Suite"] = runner.run_full_tests()
            status = "PASS" if post_apply_validation["Full Test Suite"].success else "FAIL"
            print(f"  [{status}] full test suite")

            print("Running git status (post-apply)...")
            post_apply_validation["Git Status"] = runner.run_git_status()
            print(f"  Changes after apply:")
            if post_apply_validation["Git Status"].stdout:
                for line in post_apply_validation["Git Status"].stdout.strip().splitlines()[:20]:
                    print(f"    {line}")
            else:
                print("    (none)")

            post_all_pass = all(r.success for r in post_apply_validation.values() if r is not None)
            print()
            if post_all_pass:
                print(">>> All post-apply checks passed. Review the changes and commit manually. <<<")
                print(">>> NEVER push automatically. <<<")
            else:
                print(">>> Do not commit until failures are fixed. <<<")
            print()

            # 6.5.5 Replacement smoke validation (optional, after apply + post-apply)
            if args.run_replacement_smoke:
                from src.llm.replacement_validator import ReplacementValidator

                print("=== Replacement Smoke Validation ===")
                print()

                validator = ReplacementValidator()
                apply_ok = apply_result is not None and apply_result.success
                post_ok = all(
                    r.success for r in (post_apply_validation or {}).values()
                    if r is not None
                )

                if not apply_ok:
                    print("Replacement smoke skipped: patch application did not succeed.")
                elif not post_ok:
                    print("Replacement smoke skipped: post-apply validation did not fully pass.")
                else:
                    replacement_smoke_result = validator.validate(
                        plan,
                        timeout=args.replacement_smoke_timeout,
                        include_tls=args.include_tls_smoke,
                        include_ssh=args.include_ssh_smoke,
                    )

                    print(f"Transports: {', '.join(replacement_smoke_result.transports)}")
                    print(f"Cores: {', '.join(replacement_smoke_result.cores)}")
                    print(f"Return code: {replacement_smoke_result.returncode}")

                    s = replacement_smoke_result.summary
                    print(f"Summary: {s.get('passed', 0)} passed, "
                          f"{s.get('failed', 0)} failed, "
                          f"{s.get('skipped', 0)} skipped")

                    for r in replacement_smoke_result.results:
                        status = r["status"].upper()
                        name = f"{r['transport']}/{r['core']}"
                        err = f" — {r['error']}" if r.get("error") else ""
                        print(f"  [{status}] {name}{err}")

                    if replacement_smoke_result.success:
                        print()
                        print(">>> All replacement smokes passed. <<<")
                    else:
                        print()
                        print(">>> Do not commit until replacement smoke failures are fixed. <<<")

                    # Save to task record
                    record_mgr.save_replacement_validation(
                        task_id, replacement_smoke_result,
                    )

                    print()

    # 6.6 Commit advice generation (optional, only after successful apply + post-apply validation)
    commit_message = None
    commit_changed_files = None

    if args.suggest_commit:
        from src.llm.commit_advisor import CommitAdvisor

        print()
        print("=== Commit Advice ===")

        apply_succeeded = apply_result is not None and apply_result.success
        post_all_pass = apply_succeeded and all(
            r.success for r in (post_apply_validation or {}).values() if r is not None
        )

        if not apply_succeeded:
            print("Commit advice skipped: patch application did not succeed.")
        elif not post_all_pass:
            print("Commit advice skipped: post-apply validation did not fully pass.")
        else:
            advisor = CommitAdvisor()
            diff_summary = advisor.collect_diff_summary()
            commit_changed_files = diff_summary["files"]

            commit_message = advisor.suggest_commit_message(
                plan, commit_changed_files, validation_passed=True,
            )

            print(f"Suggested commit message:")
            print(f"  {commit_message}")
            if commit_changed_files:
                print(f"Changed files:")
                for f in commit_changed_files:
                    print(f"  {f}")

            task_dir = record_mgr.get_task_dir(task_id)
            advisor.write_commit_advice(task_dir, {
                "message": commit_message,
                "changed_files": commit_changed_files,
                "diff_stat": diff_summary["diff_stat"],
                "validation_passed": True,
            })

            record_mgr.save_commit_advice(task_id, {
                "message": commit_message,
                "changed_files": commit_changed_files,
                "diff_stat": diff_summary["diff_stat"],
                "validation_passed": True,
            })

            print(f"Commit advice saved to: {task_dir}/suggested_commit_message.txt")
            print(f"Commit summary saved to: {task_dir}/commit_summary.md")
            print()
            print(">>> Commit was suggested but NOT created. <<<")
            print(">>> Push was NOT performed. <<<")
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
        apply_result=apply_result,
        post_apply_validation=post_apply_validation,
        commit_message=commit_message,
        commit_changed_files=commit_changed_files,
        replacement_smoke_result=replacement_smoke_result,
        llm_validation_results=llm_validation_results if llm_validation_results else None,
        artifact_coverage_warnings=artifact_coverage_warnings,
        protocol_retry_count=protocol_retry_count,
        protocol_retry_used=protocol_retry_used,
        semantic_retry_count=semantic_retry_count,
        semantic_retry_used=semantic_retry_used,
        patch_generation_error=patch_generation_error,
    )

    # 8. Save all artifacts
    record_mgr.save_validation_result(task_id, {
        "compile": compile_result,
        "targeted_tests": test_result,
        "full_tests": full_result,
        "git_status": git_result,
        "git_apply_check": git_apply_check_result,
    })

    if apply_result is not None:
        record_mgr.save_apply_result(task_id, apply_result, post_apply_validation or {})

    all_pass = compile_result.success and full_result.success
    if test_result is not None:
        all_pass = all_pass and test_result.success
    if git_apply_check_result is not None:
        all_pass = all_pass and git_apply_check_result.success
    if patch_generation_error is not None:
        all_pass = False
    if apply_result is not None:
        all_pass = all_pass and apply_result.success
        if post_apply_validation:
            for r in post_apply_validation.values():
                if r is not None and not r.success:
                    all_pass = False

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
