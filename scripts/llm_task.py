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
from pathlib import Path

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


def _run_stage_repair(
    stage_name: str,
    stage_desc: str,
    stage_patch: str,
    stage_files: list[str],
    allowed_edit_files: list[str],
    forbidden_files: list[str],
    transport_name: str,
    patch_gen,
    task_dir: str,
    args,
    plan,
    repo_context,
    max_repair_attempts: int,
    staged_results: list,
    stage_patches: list,
) -> tuple[bool, str | None, int]:
    """Run stage-level incremental repair.

    Applies the stage patch, runs validation, and if failures are found,
    calls the LLM to repair only the current stage's files.

    Returns (repair_ok, error_message, repair_attempt_count).
    """
    from src.llm.task_modules import (collect_stage_failure_evidence,
                                       build_stage_repair_prompt)

    # Apply the stage patch
    import subprocess
    patch_path = os.path.join(task_dir, f"stage_{stage_name}_repair.patch")
    with open(patch_path, "w", encoding="utf-8") as f:
        f.write(stage_patch)

    result = subprocess.run(
        ["git", "apply", patch_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"    [{stage_name}] repair: patch apply FAILED: {result.stderr[:200]}")
        return True, None, 0  # Patch doesn't apply — this is a generation failure, not repair

    # Run targeted validation
    pytest_target = ""
    test_files = [f for f in stage_files if f.startswith("tests/")]
    if test_files:
        pytest_target = " ".join(test_files)

    repair_plan = collect_stage_failure_evidence(
        stage_name=stage_name,
        patch_files=[f for f in stage_files if f.endswith(".py")],
        pytest_target=pytest_target,
    )

    if not repair_plan.validation_errors:
        print(f"    [{stage_name}] repair: validation PASSED, no repair needed")
        return True, None, 0

    print(f"    [{stage_name}] repair: {len(repair_plan.validation_errors)} validation failure(s)")

    # Set up repair boundaries
    repair_plan.allowed_repair_files = list(allowed_edit_files)
    repair_plan.forbidden_repair_files = list(forbidden_files) if forbidden_files else []
    repair_plan.max_repair_attempts = max_repair_attempts

    for repair_attempt in range(max_repair_attempts):
        repair_plan.repair_attempt_count = repair_attempt
        repair_plan.repair_status = "in_progress"

        # Re-read current file content (may have been modified by previous repair)
        for f in repair_plan.allowed_repair_files:
            full_path = os.path.join(os.getcwd(), f)
            if os.path.isfile(full_path):
                try:
                    with open(full_path, "r", encoding="utf-8") as fh:
                        repair_plan.current_file_content[f] = fh.read()[:8000]
                except Exception:
                    pass

        repair_prompt = build_stage_repair_prompt(
            repair_plan,
            stage_description=stage_desc,
            transport_name=transport_name,
        )

        # Save repair prompt
        prompt_path = os.path.join(task_dir, f"repair_prompt_{stage_name}_{repair_attempt}.txt")
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(repair_prompt)

        print(f"    [{stage_name}] repair attempt {repair_attempt + 1}/{max_repair_attempts}")

        try:
            # Build repair stage_info
            repair_stage_info = {
                "stage_name": f"{stage_name}_repair",
                "description": f"Repair attempt {repair_attempt + 1} for {stage_name}",
                "prompt_section": (
                    f"REPAIR STAGE: {stage_name}\n\n"
                    f"This is a REPAIR for stage '{stage_name}'.\n"
                    "All files EXCEPT the allowed repair files are correct.\n"
                    "Only fix the specific validation errors listed below.\n"
                ),
                "constraint_text": repair_prompt,
                "allowed_edit_files": repair_plan.allowed_repair_files or None,
                "allowed_create_paths": None,
                "required_edit_files": None,
                "required_create_files": None,
                "forbidden_files": repair_plan.forbidden_repair_files or None,
                "max_output_files": len(repair_plan.allowed_repair_files),
            }

            # Call LLM for repair
            from src.llm.patch_generator import LLMPatchGenerator
            repair_request = (
                f"Repair stage '{stage_name}' for {transport_name} transport. "
                f"Fix the validation errors below."
            )
            repair_patch = patch_gen.generate(
                repair_request, plan, repo_context,
                allowed_edit_files=(repair_plan.allowed_repair_files or None),
                allowed_create_paths=None,
                allowed_create_patterns=None,
                must_create_files=None,
                task_dir=task_dir,
                stage_info=repair_stage_info,
            )

            # Verify repair patch only touches allowed files
            repair_files = LLMPatchGenerator._parse_file_paths(repair_patch)
            forbidden_hit = [f for f in repair_files if f not in repair_plan.allowed_repair_files]
            if forbidden_hit:
                print(f"    [{stage_name}] repair: FORBIDDEN file(s) touched: {forbidden_hit}")
                continue

            # Apply repair patch
            repair_patch_path = os.path.join(task_dir, f"repair_{stage_name}_{repair_attempt}.patch")
            with open(repair_patch_path, "w", encoding="utf-8") as f:
                f.write(repair_patch)

            result = subprocess.run(
                ["git", "apply", repair_patch_path],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"    [{stage_name}] repair patch apply FAILED: {result.stderr[:200]}")
                repair_plan.last_error = result.stderr[:500]
                continue

            stage_patches.append(repair_patch)
            print(f"    [{stage_name}] repair applied: {', '.join(repair_files)}")

            # Re-validate
            repair_plan2 = collect_stage_failure_evidence(
                stage_name=stage_name,
                patch_files=[f for f in repair_plan.allowed_repair_files if f.endswith(".py")],
                pytest_target=pytest_target,
            )

            if not repair_plan2.validation_errors:
                print(f"    [{stage_name}] repair SUCCESS: validation passes after repair")
                repair_plan.repair_status = "passed"
                staged_results.append({
                    "stage_name": stage_name,
                    "status": "repaired",
                    "patch_files": stage_files + repair_files,
                    "error": None,
                    "retry_used": False,
                    "repair_used": True,
                    "repair_attempts": repair_attempt + 1,
                    "attempts": 1,
                })
                return True, None, repair_attempt + 1

            print(f"    [{stage_name}] repair: still {len(repair_plan2.validation_errors)} failure(s)")
            repair_plan.validation_errors = repair_plan2.validation_errors
            repair_plan.syntax_errors = repair_plan2.syntax_errors
            repair_plan.pytest_failures = repair_plan2.pytest_failures

        except Exception as e:
            print(f"    [{stage_name}] repair attempt FAILED: {e}")
            repair_plan.last_error = str(e)[:500]

    # All repair attempts exhausted
    repair_plan.repair_status = "exhausted"
    print(f"    [{stage_name}] repair EXHAUSTED after {max_repair_attempts} attempt(s)")
    return True, None, max_repair_attempts  # Stage still has a patch but validation may still fail


def _build_stage_retry_prompt(
    stage_name: str,
    stage_desc: str,
    error: str,
    target_files_context: str,
    allowed_files: list[str],
    forbidden_files: list[str],
) -> str:
    """Build a stage-specific retry prompt with diagnostics.

    Includes the error that occurred, target file content, and
    recommended strategies to avoid the same error.
    """
    lines = [
        "=== STAGE RETRY PROMPT ===",
        f"This is a RETRY for stage '{stage_name}': {stage_desc}",
        "",
        "PREVIOUS ATTEMPT ERROR:",
        f"  {error[:800]}",
        "",
    ]

    # Diagnose specific error types
    error_lower = error.lower()
    if "find string not found" in error_lower or "find string matches" in error_lower:
        lines.append("FIND/REPLACE DIAGNOSTIC:")
        lines.append("  The FIND anchor did not match the target file content.")
        lines.append("  Possible causes:")
        lines.append("    1. The target file content changed between attempts.")
        lines.append("    2. Whitespace or indentation mismatch in FIND block.")
        lines.append("    3. The LLM guessed the FIND content without seeing the file.")
        lines.append("  RECOMMENDED FIX:")
        lines.append("    - Use the EXACT file content provided below for FIND blocks.")
        lines.append("    - Prefer appending new content at end of file rather than")
        lines.append("      replacing interior sections.")
        lines.append("    - For small files, use whole-file replace with full content.")
    elif "delimiter leakage" in error_lower:
        lines.append("DELIMITER LEAKAGE DIAGNOSTIC:")
        lines.append("  The LLM output contained raw <<< or >>> delimiters.")
        lines.append("  REMOVE all bare <<< >>> <<<FIND <<<REPLACE <<<CONTENT lines")
        lines.append("  from your generated file content.")
    elif "file already exists" in error_lower or "action:create" in error_lower:
        lines.append("FILE EXISTS DIAGNOSTIC:")
        lines.append("  You used ACTION: create on a file that already exists.")
        lines.append("  Use ACTION: replace instead with a FIND block.")
        lines.append("  Target file content is provided below — use it for exact matching.")

    lines.append("")
    lines.append("ALLOWED FILES (this stage):")
    for f in allowed_files:
        lines.append(f"  - {f}")

    lines.append("")
    lines.append("FORBIDDEN FILES (DO NOT touch):")
    for f in forbidden_files:
        lines.append(f"  - {f}")

    lines.append("")
    lines.append("STAGE BOUNDARY RULES:")
    lines.append("  - ONLY generate files listed under ALLOWED FILES.")
    lines.append("  - Do NOT modify files from previous stages (they are already correct).")
    lines.append("  - Do NOT modify files from future stages.")
    lines.append("  - This retry is ONLY for this stage. Previous stages succeeded.")

    if target_files_context:
        lines.append("")
        lines.append(target_files_context)

    return "\n".join(lines)


def _build_stage_constraint_text(stage_name: str, target_transport: str,
                                 stage_context=None) -> str:
    """Build stage-specific constraint text for the LLM prompt.

    Includes StageContext target file content when available for
    tests_docs_config and integration_wiring stages.
    """
    from src.llm.task_modules import build_stage_context_prompt_section

    t = target_transport
    base_text = ""
    if stage_name == "runtime_core":
        base_text = (
            f"ONLY modify src/transport/{t}_transport.py in this stage.\n"
            "Do NOT touch factory.py, config.py, tests/, docs/, or config/examples/.\n"
            f"Do NOT create src/transport/{t}_full_transport.py or any bypass file.\n"
            "Implement runtime methods: connect(), send(), recv(), close(), is_connected().\n"
            "Keep output small and complete. Focus on the transport protocol only.\n"
            "Do NOT generate any other files. This stage is for the transport file ONLY."
        )
    elif stage_name == "integration_wiring":
        base_text = (
            "ONLY modify factory.py, config.py, and create config example.\n"
            f"Do NOT rewrite the transport implementation in src/transport/{t}_transport.py.\n"
            f"Wire the EXISTING runtime transport class into create_transport(type=\"{t}\").\n"
            "Add config fields for this transport's options."
        )
    elif stage_name == "tests_docs_config":
        base_text = (
            "ONLY modify tests/ and docs/ for this transport.\n"
            f"Do NOT modify src/transport/{t}_transport.py, factory.py, or config.py.\n"
            "Add roundtrip or client/server tests that exercise actual data transmission.\n"
            "Update docs to reflect runtime status, usage, and limitations.\n\n"
            "IMPORTANT: Prefer ACTION: append for tests/docs files.\n"
            "Only use ACTION: replace if you have the EXACT content provided below.\n"
            "If the test file already exists, add a new test class at the END of the file\n"
            "using ACTION: replace with a FIND block that matches the last lines of the file.\n"
            "For the docs file, append new sections to the end.\n"
            "If you cannot find a reliable FIND anchor, use whole-file replace\n"
            "with the full file content provided below (test/doc files are small)."
        )

    if stage_context is not None:
        context_section = build_stage_context_prompt_section(stage_context)
        if context_section:
            base_text = base_text + "\n\n" + context_section

    return base_text


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
        "--patch-context-file", default=None,
        help="Path to additional context file (e.g. patch_loop output) injected "
             "only into the Patch Generation stage. Does not affect planning, "
             "module resolution, or file selection."
    )
    parser.add_argument(
        "--generate-patch", action="store_true",
        help="Generate unified diff via LLM (dry-run: saved to patch.diff, NOT applied)."
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
    parser.add_argument(
        "--staged-generation", action="store_true",
        help="Enable staged (multi-phase) patch generation for transport_runtime tasks."
    )
    parser.add_argument(
        "--max-stage-retries", type=int, default=1,
        help="Max retries per stage when staged generation is enabled (default: 1)."
    )
    parser.add_argument(
        "--disable-stage-retry", action="store_true",
        help="Disable per-stage retry (equivalent to --max-stage-retries 0)."
    )
    parser.add_argument(
        "--stage-repair", action="store_true",
        help="Enable stage-level incremental repair: apply, validate, and repair each stage."
    )
    parser.add_argument(
        "--max-repair-attempts", type=int, default=2,
        help="Max repair attempts per stage when --stage-repair is enabled (default: 2)."
    )
    args = parser.parse_args()

    if args.verbose:
        os.environ["VPN_LLM_VERBOSE"] = "1"

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

    # Read patch-context-file if provided (injected ONLY at Patch Generation stage)
    patch_context = ""
    if args.patch_context_file:
        try:
            patch_context = Path(args.patch_context_file).read_text(encoding="utf-8")
            print(f"Patch context loaded: {args.patch_context_file} ({len(patch_context)} chars)")
        except Exception as e:
            print(f"Warning: Failed to read patch-context-file: {e}")

    effective_request = args.request
    if patch_context.strip():
        effective_request = (
            f"{args.request}\n\n"
            f"--- Additional Detection Context ---\n"
            f"{patch_context}"
        )

    # 1. Plan — rule-based or LLM-based (uses original args.request, NOT effective_request)
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
    impl_level = getattr(plan, "implementation_level", "skeleton")
    runtime_req = getattr(plan, "runtime_required", False)
    default_sw = getattr(plan, "requires_default_switch", False)
    print(f"Implementation level: {impl_level} (runtime_required={runtime_req}, "
          f"requires_default_switch={default_sw})")
    if planner_type == "llm_based":
        print(f"Risk level: {plan.risk_level}")
        print(f"Summary: {plan.summary}")
    print()

    # ---- Phase LLM-M1: Task Module Resolution ----
    intent_contract = getattr(plan, "intent_contract", None)
    if intent_contract is None:
        from src.llm.intent_contract import infer_intent_contract
        intent_contract = infer_intent_contract(
            args.request,
            task_type=plan.task_type,
            target_transport=plan.target_transport,
        )
        plan.intent_contract = intent_contract

    from src.llm.task_modules import resolve_task_module
    module_resolution = resolve_task_module(intent_contract)
    intent_contract.selected_module = module_resolution.selected_module
    intent_contract.module_resolution = module_resolution.to_dict()
    print(f"Task Module: {module_resolution.selected_module} (confidence={module_resolution.confidence:.2f})")
    print(f"  Reason: {module_resolution.reason}")
    if module_resolution.required_files:
        print(f"  Required files: {len(module_resolution.required_files)}")
    if module_resolution.forbidden_files:
        print(f"  Forbidden files: {len(module_resolution.forbidden_files)}")
    if module_resolution.staged_generation_required:
        print(f"  Staged: yes ({len(module_resolution.stages)} stages)")
    if module_resolution.warnings:
        for w in module_resolution.warnings:
            print(f"  WARNING: {w}")

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
    fs_validation = None

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
        file_selection = expander.expand(args.request, plan, candidates,
                                         module_resolution=module_resolution)
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

        # ---- File Selection Consistency Validation ----
        from src.llm.file_selection_validator import validate_file_selection_consistency

        print("Validating file selection consistency...")
        fs_validation = validate_file_selection_consistency(
            file_selection=file_selection,
            repo_index=repo_index,
            intent_contract=intent_contract,
            module_resolution=module_resolution,
            request=args.request,
        )
        status_icon = "PASS" if fs_validation.success else "FAIL"
        print(f"  File selection consistency: {status_icon}")
        if fs_validation.errors:
            for e in fs_validation.errors:
                print(f"  ERROR: {e}")
        if fs_validation.warnings:
            for w in fs_validation.warnings:
                print(f"  WARNING: {w}")

        # Save file selection validation result
        record_mgr._write_file(
            task_id, "file_selection_validation.json",
            json.dumps(fs_validation.to_dict(), indent=2),
        )

        # Block on hard failures
        if not fs_validation.success:
            print()
            print(">>> File selection consistency check FAILED. <<<")
            print(">>> This is a file selection conflict, not an LLM output failure. <<<")
            retry_lines = ["# File Selection Retry Prompt", ""]
            retry_lines.append(
                "The file selection phase produced conflicts that must be resolved "
                "before patch generation:"
            )
            retry_lines.append("")
            for e in fs_validation.errors:
                retry_lines.append(f"- ERROR: {e}")
            for w in fs_validation.warnings:
                retry_lines.append(f"- WARNING: {w}")
            retry_lines.append("")
            retry_lines.append("## Suggested Actions")
            retry_lines.append("- Review the request for ambiguity about which files to modify.")
            retry_lines.append("- Check whether required module files exist and are named correctly.")
            retry_lines.append("- If this is a docs_only/config_only task, remove source file edits.")
            retry_prompt = "\n".join(retry_lines)
            record_mgr._write_file(task_id, "retry_prompt.txt", retry_prompt)
            print(f"Retry prompt saved to: {task_dir}/retry_prompt.txt")
            report = write_report(
                task_id=task_id, request=args.request, plan=plan,
                compile_result=None, targeted_result=None,
                full_result=None, git_result=None,
                planner_type=planner_type,
                file_selection_validation=fs_validation,
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

        # ---- ContextBuilder truncation check for must_edit files ----
        truncated_must_edit = [
            f for f in context_summary.files_truncated
            if f in file_selection.must_edit_files
        ]
        if truncated_must_edit:
            print(f"  WARNING: {len(truncated_must_edit)} must-edit file(s) truncated:")
            for f in truncated_must_edit:
                print(f"    - {f}")
            if fs_validation is not None:
                fs_validation.evidence["truncated_must_edit_files"] = truncated_must_edit
                fs_validation.warnings.append(
                    f"Must-edit file(s) truncated in repository context: "
                    f"{', '.join(truncated_must_edit)}"
                )

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
    intent_result = None
    tunnel_smoke_result = None

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
        must_create_files = None
        if file_selection is not None:
            allowed_edit_files = list(file_selection.must_edit_files)
            allowed_create_paths = list(file_selection.allowed_create_paths) or None
            allowed_create_patterns = list(file_selection.allowed_create_patterns) or None
            must_create_files = list(file_selection.must_create_files) or None

        task_dir = record_mgr.get_task_dir(task_id) if record_mgr else None

        # ---- Staged generation (Phase LLM-M1.1) ----
        staged_results: list[dict] = []
        staged_generation = (
            args.staged_generation
            and module_resolution is not None
            and module_resolution.staged_generation_required
            and len(module_resolution.stages) > 0
        )

        if staged_generation:
            from src.llm.task_modules import build_stage_context

            print("  Staged generation enabled — splitting into sub-stages:")
            target = plan.target_transport or ""
            merged_patches: list[str] = []
            stage_patches: list[str] = []

            max_retries = 0 if args.disable_stage_retry else args.max_stage_retries

            for stage in module_resolution.stages:
                stage_name = stage.stage_name
                stage_desc = stage.description if hasattr(stage, "description") else ""
                print(f"    [{stage_name}] {stage_desc}")

                # Build stage-specific allowed/edit/create lists
                stage_allowed_edit = [
                    p.format(name=target) if "{name}" in p else p
                    for p in getattr(stage, "allowed_edit_patterns", [])
                ]
                stage_allowed_create = [
                    p.format(name=target) if "{name}" in p else p
                    for p in getattr(stage, "allowed_create_patterns", [])
                ]
                stage_required_edit = [
                    p.format(name=target) if "{name}" in p else p
                    for p in getattr(stage, "required_edit_patterns", [])
                ]
                stage_required_create = [
                    p.format(name=target) if "{name}" in p else p
                    for p in getattr(stage, "required_create_patterns", [])
                ]
                stage_forbidden = [
                    p.format(name=target) if "{name}" in p else p
                    for p in getattr(stage, "forbidden_patterns", [])
                ]

                # Build stage context with target file content
                stage_ctx = build_stage_context(stage, target)

                # Build stage_info dict for PatchGenerator
                stage_info = {
                    "stage_name": stage_name,
                    "description": stage_desc,
                    "prompt_section": (
                        f"STAGE: {stage_name} — {stage_desc}\n\n"
                        "THIS IS A STAGED GENERATION TASK.\n"
                        f"You are generating ONLY the files for stage '{stage_name}'.\n"
                        "Do NOT generate files from other stages.\n"
                    ),
                    "constraint_text": _build_stage_constraint_text(
                        stage_name, target, stage_context=stage_ctx),
                    "allowed_edit_files": stage_allowed_edit or None,
                    "allowed_create_paths": stage_allowed_create or None,
                    "required_edit_files": stage_required_edit or None,
                    "required_create_files": stage_required_create or None,
                    "forbidden_files": stage_forbidden or None,
                    "max_output_files": getattr(stage, "max_output_files", 4),
                    "stage_context": stage_ctx,
                }

                # For final_validation stage, skip LLM call
                if stage_name == "final_validation":
                    print(f"    [{stage_name}] skipped (validation-only stage)")
                    staged_results.append({
                        "stage_name": stage_name,
                        "status": "skipped",
                        "patch_files": [],
                        "error": None,
                        "retry_used": False,
                        "attempts": 0,
                    })
                    continue

                # Per-stage retry loop
                stage_attempt = 0
                stage_passed = False
                last_error = None
                stage_patch = ""
                stage_files: list[str] = []

                while stage_attempt <= max_retries:
                    stage_attempt += 1
                    try:
                        stage_patch = patch_gen.generate(
                            effective_request, plan, repo_context,
                            allowed_edit_files=(stage_allowed_edit or None),
                            allowed_create_paths=(stage_allowed_create or None),
                            allowed_create_patterns=None,
                            must_create_files=(stage_required_create or None),
                            task_dir=task_dir,
                            stage_info=stage_info,
                        )
                        # Parse files from stage patch
                        from src.llm.patch_generator import LLMPatchGenerator as PG2
                        stage_files = PG2._parse_file_paths(stage_patch)
                        merged_patches.append(stage_patch)
                        stage_patches.append(stage_patch)
                        print(f"    [{stage_name}] generated {len(stage_files)} file(s): "
                              f"{', '.join(stage_files)}"
                              + (f" (retry {stage_attempt - 1}/{max_retries})" if stage_attempt > 1 else ""))
                        stage_passed = True
                        break
                    except LLMPatchGeneratorError as e:
                        last_error = str(e)
                        if stage_attempt <= max_retries:
                            print(f"    [{stage_name}] attempt {stage_attempt} FAILED: {e}")
                            print(f"    [{stage_name}] retrying (attempts left: {max_retries - stage_attempt})")

                            # Build richer retry prompt with diagnostics
                            from src.llm.task_modules import build_stage_context_prompt_section
                            retry_context = build_stage_context_prompt_section(stage_ctx)
                            retry_diag = _build_stage_retry_prompt(
                                stage_name=stage_name,
                                stage_desc=stage_desc,
                                error=str(e),
                                target_files_context=retry_context,
                                allowed_files=stage_allowed_edit + stage_allowed_create,
                                forbidden_files=stage_forbidden,
                            )
                            # Inject retry diagnostics into stage_info
                            stage_info["constraint_text"] = (
                                stage_info["constraint_text"] + "\n\n" + retry_diag
                            )
                            stage_info["prompt_section"] = (
                                stage_info["prompt_section"]
                                + "\n\nRETRY — PREVIOUS ATTEMPT FAILED:\n"
                                + str(e)[:1000]
                                + "\n\n" + retry_diag
                            )

                # After generation retry loop: run repair if generation succeeded
                repair_used = False
                repair_attempts = 0
                if stage_passed and args.stage_repair and stage_files:
                    repair_ok, repair_err, repair_used_count = _run_stage_repair(
                        stage_name=stage_name,
                        stage_desc=stage_desc,
                        stage_patch=stage_patch,
                        stage_files=stage_files,
                        allowed_edit_files=stage_allowed_edit,
                        forbidden_files=stage_forbidden,
                        transport_name=target,
                        patch_gen=patch_gen,
                        task_dir=task_dir,
                        args=args,
                        plan=plan,
                        repo_context=repo_context,
                        max_repair_attempts=args.max_repair_attempts,
                        staged_results=staged_results,
                        stage_patches=stage_patches,
                    )
                    if repair_used_count > 0:
                        repair_used = True
                        repair_attempts = repair_used_count
                    if repair_ok:
                        print(f"    [{stage_name}] repair passed (stage kept)")
                    else:
                        print(f"    [{stage_name}] repair did not fix all errors (stage kept with warnings)")

                if not stage_passed:
                    print(f"    [{stage_name}] FAILED after {stage_attempt} attempt(s): {last_error}")
                    staged_results.append({
                        "stage_name": stage_name,
                        "status": "failed",
                        "patch_files": [],
                        "error": last_error,
                        "retry_used": stage_attempt > 1,
                        "attempts": stage_attempt,
                        "repair_used": repair_used,
                        "repair_attempts": repair_attempts,
                    })
                    break

                staged_results.append({
                    "stage_name": stage_name,
                    "status": "passed",
                    "patch_files": stage_files,
                    "error": None,
                    "retry_used": stage_attempt > 1,
                    "attempts": stage_attempt,
                    "repair_used": repair_used,
                    "repair_attempts": repair_attempts,
                })

            # Merge all stage patches
            if merged_patches:
                patch_text = "\n".join(merged_patches)
                print(f"  Staged generation complete: {len(staged_results)} stage(s), "
                      f"{len(merged_patches)} patch(es)")
            else:
                patch_text = None
                patch_generation_error = (
                    f"Staged generation failed at stage "
                    f"'{staged_results[-1]['stage_name'] if staged_results else 'unknown'}': "
                    f"{staged_results[-1].get('error') if staged_results else 'unknown'}"
                )
                print(f"  Staged generation failed: {patch_generation_error}")
        else:
            try:
                patch_text = patch_gen.generate(
                    effective_request, plan, repo_context,
                    allowed_edit_files=allowed_edit_files,
                    allowed_create_paths=allowed_create_paths,
                    allowed_create_patterns=allowed_create_patterns,
                    must_create_files=must_create_files,
                    task_dir=task_dir,
                    module_resolution=module_resolution,
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

            # ---- git apply --check (run before validator so it gets real result) ----
            print("Running git apply --check...")
            git_apply_check_result = runner.run_git_apply_check(patch_path)
            if git_apply_check_result.success:
                print("  git apply --check: PASS (patch would apply cleanly)")
            else:
                print(f"  git apply --check: FAIL (returncode={git_apply_check_result.returncode})")
                if git_apply_check_result.stderr:
                    print(f"  {git_apply_check_result.stderr.strip()[:500]}")

            # ---- Patch completeness validation ----
            from src.llm.patch_generator import check_patch_completeness
            completeness = check_patch_completeness(
                patch_text,
                must_create_files=must_create_files,
                must_edit_files=allowed_edit_files,
            )
            print(f"Completeness check: {'PASS' if completeness.passed else 'FAIL'}")
            if completeness.missing_files:
                print(f"  Missing files: {completeness.missing_files}")
            if completeness.truncated_files:
                print(f"  Truncated files: {completeness.truncated_files}")
            if completeness.syntax_errors:
                print(f"  Syntax errors: {completeness.syntax_errors}")
            if completeness.warnings:
                for w in completeness.warnings:
                    print(f"  Warning: {w}")

            # Save completeness result
            record_mgr._write_file(
                task_id, "completeness_check.json",
                json.dumps(completeness.to_dict(), indent=2),
            )

            # ---- Runtime transport contract validation ----
            from src.llm.patch_generator import check_runtime_transport_contract
            runtime_required = getattr(plan, "runtime_required", False)
            allow_skeleton = getattr(plan, "allow_skeleton", True)
            requires_default_switch = getattr(plan, "requires_default_switch", False)
            default_target = getattr(plan, "default_transport_target", None)

            runtime_check = check_runtime_transport_contract(
                patch_text,
                runtime_required=runtime_required,
                allow_skeleton=allow_skeleton,
                requires_default_switch=requires_default_switch,
                default_transport_target=default_target,
            )
            print(f"Runtime contract check: {'PASS' if runtime_check.passed else 'FAIL'}")
            if runtime_check.errors:
                for e in runtime_check.errors:
                    print(f"  ERROR: {e}")
            if runtime_check.warnings:
                for w in runtime_check.warnings:
                    print(f"  WARNING: {w}")

            # Save runtime check result
            record_mgr._write_file(
                task_id, "runtime_contract_check.json",
                json.dumps(runtime_check.to_dict(), indent=2),
            )

            # ---- Tunnel Smoke Validation ----
            from src.llm.tunnel_smoke_validator import (
                run_tunnel_smoke_validation,
            )
            print()
            print("=== Tunnel Smoke Validation ===")
            task_type = getattr(plan, "task_type", None)
            target_transport = getattr(plan, "target_transport", None)
            intent_contract = getattr(plan, "intent_contract", None)
            smoke_dir = os.path.join(task_dir, "tunnel_smoke")
            tunnel_smoke_result = run_tunnel_smoke_validation(
                task_type=task_type,
                target_transport=target_transport,
                intent_contract=intent_contract,
                output_dir=smoke_dir,
            )
            if tunnel_smoke_result.mock_tun_smoke is not None:
                ms = tunnel_smoke_result.mock_tun_smoke
                print(f"Mock-TUN smoke: {ms.status} (transport={ms.transport}, {ms.duration_sec}s)")
                if ms.error:
                    print(f"  Error: {ms.error}")
            if tunnel_smoke_result.phase9_passed:
                print("Phase 9 real trace: PASS")
            elif tunnel_smoke_result.phase9_skipped:
                print(f"Phase 9 real trace: SKIPPED ({tunnel_smoke_result.phase9_skip_reason[:100]})")
            elif tunnel_smoke_result.phase9_smoke is not None:
                print(f"Phase 9 real trace: FAIL ({tunnel_smoke_result.phase9_smoke.get('error', 'unknown')[:100]})")

            # Save tunnel smoke result
            record_mgr._write_file(
                task_id, "tunnel_smoke_result.json",
                json.dumps(tunnel_smoke_result.to_dict(), indent=2, default=str),
            )

            # ---- User Intent Validation (V2) ----
            from src.llm.user_intent_validator import UserIntentValidator

            intent_validator = UserIntentValidator()
            intent_result = intent_validator.validate(
                patch_text=patch_text,
                intent_contract=intent_contract,
                patch_file_paths=patch_file_paths,
                completeness_result=completeness,
                runtime_check_result=runtime_check,
                compile_ok=compile_result.success,
                tests_ok=full_result.success,
                git_apply_check_ok=(
                    git_apply_check_result.success
                    if git_apply_check_result is not None else None
                ),
                tunnel_smoke_result=tunnel_smoke_result,
                module_resolution=module_resolution,
            )
            print(f"User intent validation: {intent_result.user_intent_status}")
            print(f"  Patch integrity: {intent_result.patch_integrity_status}")
            print(f"  Functional: {intent_result.functional_validation_status}")
            print(f"  Final status: {intent_result.final_task_status}")
            if intent_result.was_downgraded:
                print(f"  DOWNGRADE DETECTED: {intent_result.downgrade_detail}")
            if intent_result.unmet_acceptance_criteria:
                for uc in intent_result.unmet_acceptance_criteria:
                    print(f"  UNMET: {uc}")

            # Save intent validation result
            record_mgr._write_file(
                task_id, "user_intent_validation.json",
                json.dumps(intent_result.to_dict(), indent=2),
            )

            # ---- Retry prompt generation on completeness, runtime, or intent failure ----
            completeness_or_runtime_failed = (
                not completeness.passed or not runtime_check.passed
                or intent_result.user_intent_status == "failed"
            )
            if completeness_or_runtime_failed:
                retry_lines = ["# Patch Generation Retry Prompt", ""]
                retry_lines.append(
                    "The previously generated patch was INCOMPLETE or does not "
                    "meet runtime requirements. Regenerate with the following fixes:"
                )
                retry_lines.append("")
                if completeness.missing_files:
                    retry_lines.append("## Missing Files (MUST be generated)")
                    for f in completeness.missing_files:
                        retry_lines.append(f"- `{f}`")
                    retry_lines.append("")
                if completeness.truncated_files:
                    retry_lines.append("## Truncated Files (MUST be completed)")
                    for f in completeness.truncated_files:
                        retry_lines.append(f"- `{f}`")
                    retry_lines.append("")
                if completeness.syntax_errors:
                    retry_lines.append("## Syntax Errors (MUST be fixed)")
                    for f in completeness.syntax_errors:
                        retry_lines.append(f"- `{f}`")
                    retry_lines.append("")
                if runtime_check.errors:
                    retry_lines.append("## Runtime Contract Violations (MUST be fixed)")
                    for e in runtime_check.errors:
                        retry_lines.append(f"- {e}")
                    retry_lines.append("")
                if intent_result.unmet_acceptance_criteria:
                    retry_lines.append("## Intent Contract Violations (MUST be fixed)")
                    for uc in intent_result.unmet_acceptance_criteria:
                        retry_lines.append(f"- {uc}")
                    retry_lines.append("")
                if intent_result.was_downgraded and not intent_result.downgrade_allowed:
                    retry_lines.append("## Downgrade Detected (MUST be fixed)")
                    retry_lines.append(f"- {intent_result.downgrade_detail}")
                    retry_lines.append("")
                if completeness.warnings:
                    retry_lines.append("## Additional Warnings")
                    for w in completeness.warnings:
                        retry_lines.append(f"- {w}")
                    retry_lines.append("")

                retry_lines.append("## Instructions")
                retry_lines.append("")
                task_type = plan.task_type if hasattr(plan, "task_type") else ""
                if task_type in ("transport_addition", "feature_addition"):
                    if runtime_required:
                        retry_lines.append(
                            "- Generate a RUNTIME transport — NOT a skeleton. "
                            "connect() must work. send() and recv() must transmit data. "
                            "Include client/server roundtrip tests."
                        )
                    else:
                        retry_lines.append(
                            "- Generate a SKELETON implementation only. "
                            "The class must be importable and constructable, but "
                            "methods like connect() should raise TransportError "
                            "with a clear 'skeleton not yet implemented' message."
                        )
                        retry_lines.append(
                            "- Full protocol implementation belongs in a follow-up task."
                        )
                retry_lines.append(
                    "- Output a complete FILE: block for EVERY required file."
                )
                retry_lines.append(
                    "- Do NOT include introductions, explanations, or markdown fences."
                )
                retry_lines.append(
                    "- Ensure every file ends with a complete line."
                )

                retry_prompt = "\n".join(retry_lines)
                record_mgr._write_file(task_id, "retry_prompt.txt", retry_prompt)
                print(f"Retry prompt saved to: {task_dir}/retry_prompt.txt")

            # Artifact coverage check
            artifact_coverage_warnings = _check_artifact_coverage(
                args.request, plan.task_type, patch_file_paths,
                plan_affected_areas=getattr(plan, "affected_areas", None),
            )
            if artifact_coverage_warnings:
                print("Artifact coverage warnings:")
                for w in artifact_coverage_warnings:
                    print(f"  Warning: {w}")

            # ---- Default switch gate ----
            if requires_default_switch and runtime_check.is_skeleton:
                print()
                print(">>> DEFAULT SWITCH BLOCKED: Cannot set skeleton transport as default. <<<")
                print(">>> The transport must be runtime-usable before switching defaults. <<<")

            print()
            if not completeness.passed or not runtime_check.passed:
                print(">>> PATCH IS INCOMPLETE. Review retry_prompt.txt and re-generate. <<<")
            else:
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
    post_apply_tunnel_smoke_result = None

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

            # ---- Post-apply tunnel smoke validation ----
            from src.llm.tunnel_smoke_validator import run_tunnel_smoke_validation
            print("Running tunnel smoke (post-apply)...")
            _task_type = getattr(plan, "task_type", None)
            _target_transport = getattr(plan, "target_transport", None)
            _intent_contract = getattr(plan, "intent_contract", None)
            post_smoke_dir = os.path.join(task_dir, "tunnel_smoke_post_apply")
            post_apply_tunnel_smoke_result = run_tunnel_smoke_validation(
                task_type=_task_type,
                target_transport=_target_transport,
                intent_contract=_intent_contract,
                output_dir=post_smoke_dir,
            )
            if post_apply_tunnel_smoke_result.mock_tun_smoke is not None:
                ms = post_apply_tunnel_smoke_result.mock_tun_smoke
                print(f"  Mock-TUN smoke: {ms.status} (transport={ms.transport}, {ms.duration_sec}s)")
                if ms.error:
                    print(f"    Error: {ms.error}")
            if post_apply_tunnel_smoke_result.phase9_passed:
                print("  Phase 9 real trace: PASS")
            elif post_apply_tunnel_smoke_result.phase9_skipped:
                print("  Phase 9 real trace: SKIPPED")
            elif post_apply_tunnel_smoke_result.phase9_smoke is not None:
                print(f"  Phase 9 real trace: FAIL")
            # Save post-apply tunnel smoke result
            record_mgr._write_file(
                task_id, "post_apply_tunnel_smoke_result.json",
                json.dumps(post_apply_tunnel_smoke_result.to_dict(), indent=2, default=str),
            )

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
        intent_result=intent_result if args.generate_patch else None,
        tunnel_smoke_result=tunnel_smoke_result,
        post_apply_tunnel_smoke_result=post_apply_tunnel_smoke_result,
        file_selection_validation=fs_validation,
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
    if tunnel_smoke_result is not None and tunnel_smoke_result.mock_tun_smoke is not None:
        if not tunnel_smoke_result.mock_tun_smoke.success:
            intent_contract = getattr(plan, "intent_contract", None)
            if intent_contract is not None and (intent_contract.runtime_required or intent_contract.end_to_end_required):
                all_pass = False
    if post_apply_tunnel_smoke_result is not None and post_apply_tunnel_smoke_result.mock_tun_smoke is not None:
        if not post_apply_tunnel_smoke_result.mock_tun_smoke.success:
            intent_contract = getattr(plan, "intent_contract", None)
            if intent_contract is not None and (intent_contract.runtime_required or intent_contract.end_to_end_required):
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
