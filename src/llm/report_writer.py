"""Report Writer for LLM Agent tasks.

Generates Markdown validation reports from task plans and results.
"""


def write_report(
    task_id: str,
    request: str,
    plan,
    compile_result,
    targeted_result,
    full_result,
    git_result,
    planner_type: str = "rule_based",
    patch_text: str | None = None,
    patch_file_paths: list[str] | None = None,
    git_apply_check_result=None,
    apply_result=None,
    post_apply_validation: dict | None = None,
    commit_message: str | None = None,
    commit_changed_files: list[str] | None = None,
    replacement_smoke_result=None,
    llm_validation_results: list | None = None,
    artifact_coverage_warnings: list[str] | None = None,
    protocol_retry_count: int = 0,
    protocol_retry_used: bool = False,
    semantic_retry_count: int = 0,
    semantic_retry_used: bool = False,
    patch_generation_error: str | None = None,
    intent_result=None,
    tunnel_smoke_result=None,
) -> str:
    """Generate a Markdown validation report.

    Args:
        task_id: Unique task identifier.
        request: Original user request string.
        plan: TaskPlan or LLMTaskPlan instance.
        compile_result: ValidationResult for compileall.
        targeted_result: ValidationResult for targeted tests, or None.
        full_result: ValidationResult for the full test suite.
        git_result: ValidationResult for git status.
        planner_type: "rule_based" or "llm_based".
        patch_text: Generated unified diff, or None if no patch.
        patch_file_paths: Files referenced in the patch, or None.
        git_apply_check_result: ValidationResult from git apply --check, or None.
        apply_result: ValidationResult from git apply, or None if not applied.
        post_apply_validation: Dict of label -> ValidationResult for post-apply checks.
        commit_message: Suggested commit message, or None.
        commit_changed_files: List of changed file paths from diff summary, or None.
        llm_validation_results: Optional list of ValidationResult from LLM-suggested
            validation commands. Failures here are non-blocking but appear in the report.
        artifact_coverage_warnings: Optional list of warning strings about missing
            expected artifacts (e.g., tests, docs, config files).

    Returns:
        Markdown report string.
    """
    risk_level = getattr(plan, "risk_level", None)
    intent_contract = getattr(plan, "intent_contract", None)

    lines = []
    lines.append("# LLM Task Report")
    lines.append("")
    lines.append("## Task Info")
    lines.append("")
    lines.append(f"- **Task ID**: `{task_id}`")
    lines.append(f"- **User Request**: {request}")
    lines.append(f"- **Planner**: {planner_type}")
    lines.append(f"- **Task Type**: `{plan.task_type}`")
    lines.append(f"- **Target Transport**: `{plan.target_transport or 'N/A'}`")
    if risk_level:
        lines.append(f"- **Risk Level**: `{risk_level}`")
    impl_level = getattr(plan, "implementation_level", None)
    if impl_level:
        lines.append(f"- **Implementation Level**: `{impl_level}`")
    lines.append("")

    # ---- IntentContract summary ----
    if intent_contract is not None:
        lines.append("## Intent Contract")
        lines.append("")
        lines.append(f"- **Implementation Level**: `{intent_contract.implementation_level}`")
        lines.append(f"- **Runtime Required**: {intent_contract.runtime_required}")
        lines.append(f"- **Allow Stub**: {intent_contract.allow_stub}")
        lines.append(f"- **Requires Default Change**: {intent_contract.requires_default_change}")
        lines.append(f"- **Requires Tests**: {intent_contract.requires_tests}")
        lines.append(f"- **Requires Docs**: {intent_contract.requires_docs}")
        lines.append(f"- **Requires Config Update**: {intent_contract.requires_config_update}")
        lines.append(f"- **Requires CLI Update**: {intent_contract.requires_cli_update}")
        lines.append(f"- **Requires Trace/Evaluation**: {intent_contract.requires_trace_or_evaluation}")
        lines.append(f"- **Requires No Behavior Change**: {intent_contract.requires_no_behavior_change}")
        lines.append(f"- **End-to-End Required**: {intent_contract.end_to_end_required}")
        lines.append(f"- **Must Pass Without Warnings**: {intent_contract.must_pass_without_warnings}")
        if intent_contract.runtime_wiring_required:
            lines.append(f"- **Runtime Wiring Required**: Yes")
        if intent_contract.expected_integration_points:
            lines.append("- **Expected Integration Points**:")
            for pt in intent_contract.expected_integration_points:
                lines.append(f"  - `{pt}`")
        if intent_contract.forbidden_degradations:
            lines.append("- **Forbidden Degradations**:")
            for d in intent_contract.forbidden_degradations:
                lines.append(f"  - {d}")
        lines.append("")

        # Acceptance criteria table
        if intent_contract.acceptance_criteria:
            lines.append("### Acceptance Criteria")
            lines.append("")
            lines.append("| # | Criterion | Category | Required | Validation Method |")
            lines.append("|---|---|---|---|---|")
            for i, ac in enumerate(intent_contract.acceptance_criteria, 1):
                req = "Yes" if ac.required else "No"
                lines.append(
                    f"| {i} | {ac.name} | {ac.category} | {req} | "
                    f"{ac.validation_method[:60]} |"
                )
            lines.append("")

    lines.append("## Planned Changes")
    lines.append("")
    if plan.affected_areas:
        for area in plan.affected_areas:
            lines.append(f"- `{area}`")
    else:
        lines.append("- _(none)_")
    lines.append("")

    lines.append("## Validation Results")
    lines.append("")

    _append_result_section(lines, "Compile Check", compile_result)
    _append_result_section(lines, "Targeted Tests", targeted_result)
    _append_result_section(lines, "Full Test Suite", full_result)
    _append_result_section(lines, "Git Status", git_result)

    # LLM-suggested validation commands (non-blocking)
    if llm_validation_results:
        lines.append("### LLM-Suggested Validation Commands")
        lines.append("")
        lines.append("> These commands were suggested by the LLM planner. "
                     "Failures are **non-blocking** and do not prevent patch generation.")
        lines.append("")
        for r in llm_validation_results:
            status = "PASS" if r.success else "FAIL"
            lines.append(f"- **[{status}]** `{r.command}` (rc={r.returncode})")
            if r.stderr:
                stderr_lines = r.stderr.strip().splitlines()
                snippet = "\n".join(stderr_lines[-5:])
                lines.append(f"  ```")
                for line in snippet.splitlines():
                    lines.append(f"  {line}")
                lines.append(f"  ```")
            if r.stdout:
                stdout_lines = r.stdout.strip().splitlines()
                snippet = "\n".join(stdout_lines[-5:])
                lines.append(f"  ```")
                for line in snippet.splitlines():
                    lines.append(f"  {line}")
                lines.append(f"  ```")
        lines.append("")

    # Patch section
    if patch_text is not None:
        lines.append("## Patch Generation")
        lines.append("")
        lines.append(f"- **Status**: patch was generated but not applied")
        lines.append(f"- **Patch size**: {len(patch_text)} bytes")
        if protocol_retry_used:
            lines.append(f"- **Protocol retry**: {protocol_retry_count} attempt(s) — "
                         f"first attempt malformed, retry succeeded")
        else:
            lines.append(f"- **Protocol retry**: not needed (first attempt passed)")
        if semantic_retry_used:
            lines.append(f"- **Semantic retry**: {semantic_retry_count} attempt(s) — "
                         f"semantic retry succeeded after validation error")
        else:
            lines.append(f"- **Semantic retry**: not needed (first valid response passed)")
        if patch_file_paths:
            lines.append("- **Files in patch**:")
            for fp in patch_file_paths:
                lines.append(f"  - `{fp}`")
        else:
            lines.append("- **Files in patch**: _(none detected)_")
        lines.append("")

        _append_result_section(lines, "Git Apply Check", git_apply_check_result)

    elif patch_generation_error is not None:
        lines.append("## Patch Generation")
        lines.append("")
        lines.append(f"- **Status**: FAILED")
        lines.append(f"- **Error**: {patch_generation_error}")
        if protocol_retry_used:
            lines.append(f"- **Protocol retry**: {protocol_retry_count} attempt(s) — "
                         f"malformed patch response after retry")
        if semantic_retry_used:
            lines.append(f"- **Semantic retry**: {semantic_retry_count} attempt(s) — "
                         f"semantic correction failed after retry")
        lines.append("")

    # Patch application section (only when patch was actually applied)
    patch_was_applied = apply_result is not None
    if patch_was_applied:
        lines.append("## Patch Application")
        lines.append("")
        lines.append(f"- **Patch applied**: Yes")
        _append_result_section(lines, "Git Apply", apply_result)
        if post_apply_validation:
            lines.append("### Post-Apply Validation")
            lines.append("")
            for label, result in post_apply_validation.items():
                if result is not None:
                    _append_result_section(lines, label, result)
                else:
                    lines.append(f"**{label}**: _(not run)_")
                    lines.append("")
        lines.append("")

    # Replacement Smoke Validation section
    rs_result = replacement_smoke_result
    if rs_result is not None:
        lines.append("## Replacement Smoke Validation")
        lines.append("")
        lines.append(f"- **Transports**: {', '.join(rs_result.transports)}")
        lines.append(f"- **Cores**: {', '.join(rs_result.cores)}")
        lines.append(f"- **Return Code**: {rs_result.returncode}")
        s = rs_result.summary
        if s:
            lines.append(f"- **Summary**: {s.get('passed', 0)} passed, "
                         f"{s.get('failed', 0)} failed, "
                         f"{s.get('skipped', 0)} skipped")
        if rs_result.error:
            lines.append(f"- **Error**: {rs_result.error}")
        lines.append("")
        if rs_result.results:
            lines.append("### Per-Transport Results")
            lines.append("")
            lines.append("| Transport | Core | Status | Duration | Error |")
            lines.append("|---|---|---|---|---|")
            for r in rs_result.results:
                dur = f"{r.get('duration_sec', 0):.2f}s"
                err = r.get("error") or ""
                lines.append(
                    f"| {r['transport']} | {r['core']} | "
                    f"{r['status'].upper()} | {dur} | {err} |"
                )
            lines.append("")
        if rs_result.success:
            lines.append("All replacement smokes passed.")
        else:
            lines.append("Some replacement smokes failed. Do not commit until fixed.")
        lines.append("")

    # Artifact coverage warnings (expected files missing from patch)
    if artifact_coverage_warnings:
        lines.append("## Artifact Coverage Warnings")
        lines.append("")
        lines.append("> The user request indicated certain file types should be generated, "
                     "but they were not found in the patch. Review manually.")
        lines.append("")
        for w in artifact_coverage_warnings:
            lines.append(f"- {w}")
        lines.append("")

    # ---- User Intent Validation (V2) ----
    if intent_result is not None:
        lines.append("## User Intent Validation")
        lines.append("")
        lines.append(f"- **Patch Integrity**: `{intent_result.patch_integrity_status}`")
        lines.append(f"- **Functional Validation**: `{intent_result.functional_validation_status}`")
        lines.append(f"- **User Intent**: `{intent_result.user_intent_status}`")
        lines.append(f"- **Final Task Status**: `{intent_result.final_task_status}`")
        if intent_result.was_downgraded:
            lines.append(f"- **Downgrade Detected**: Yes")
            lines.append(f"  - Detail: {intent_result.downgrade_detail}")
            lines.append(f"  - Downgrade Allowed: {intent_result.downgrade_allowed}")
        lines.append("")

        if intent_result.unmet_acceptance_criteria:
            lines.append("### Unmet Acceptance Criteria")
            lines.append("")
            for uc in intent_result.unmet_acceptance_criteria:
                lines.append(f"- {uc}")
            lines.append("")

        if intent_result.evidence:
            lines.append("### Evidence")
            lines.append("")
            lines.append("| Criterion | Satisfied | Evidence |")
            lines.append("|---|---|---|")
            for e in intent_result.evidence:
                status = "PASS" if e.satisfied else "FAIL"
                lines.append(f"| {e.criterion_name} | {status} | {e.evidence[:80]} |")
            lines.append("")

        # ---- Blueprint Validation (Phase LLM-M2) ----
        if intent_result is not None and intent_result.selected_blueprint:
            lines.append("## Patch Blueprint")
            lines.append("")
            lines.append(f"- **Selected Blueprint**: `{intent_result.selected_blueprint}`")
            lines.append(f"- **Blueprint Status**: `{intent_result.blueprint_status}`")
            if intent_result.required_file_changes_missing:
                lines.append("- **Missing Required File Changes**:")
                for f in intent_result.required_file_changes_missing:
                    lines.append(f"  - `{f}`")
            if intent_result.forbidden_blueprint_changes:
                lines.append("- **Forbidden Blueprint Changes Detected**:")
                for f in intent_result.forbidden_blueprint_changes:
                    lines.append(f"  - `{f}`")
            if intent_result.missing_template_evidence:
                lines.append("- **Missing Template Evidence**:")
                for f in intent_result.missing_template_evidence:
                    lines.append(f"  - {f}")
            if intent_result.missing_validation_evidence:
                lines.append("- **Missing Validation Evidence**:")
                for f in intent_result.missing_validation_evidence:
                    lines.append(f"  - {f}")
            lines.append("")

    # ---- End-to-End Tunnel Validation ----
    if tunnel_smoke_result is not None and (
        tunnel_smoke_result.mock_tun_smoke is not None
        or tunnel_smoke_result.phase9_smoke is not None
    ):
        lines.append("## End-to-End Tunnel Validation")
        lines.append("")

        # Mock-TUN smoke
        if tunnel_smoke_result.mock_tun_smoke is not None:
            mock = tunnel_smoke_result.mock_tun_smoke
            status_icon = "PASS" if mock.success else "FAIL"
            lines.append(f"- **Mock-TUN Smoke**: {status_icon}")
            lines.append(f"  - Transport: `{mock.transport}`")
            lines.append(f"  - Duration: {mock.duration_sec}s")
            lines.append(f"  - Log Dir: `{mock.log_dir}`")
            if mock.error:
                lines.append(f"  - Error: {mock.error}")
            if mock.server_errors:
                lines.append(f"  - Server Errors: {len(mock.server_errors)}")
            if mock.client_errors:
                lines.append(f"  - Client Errors: {len(mock.client_errors)}")
            lines.append("")

        # Phase 9 real-TUN/netns
        if tunnel_smoke_result.phase9_smoke is not None:
            p9 = tunnel_smoke_result.phase9_smoke
            status_icon = "PASS" if tunnel_smoke_result.phase9_passed else "FAIL"
            lines.append(f"- **Phase 9 Real Trace**: {status_icon}")
            if "output_dir" in p9:
                lines.append(f"  - Output Dir: `{p9['output_dir']}`")
            if "error" in p9 and p9["error"]:
                lines.append(f"  - Error: {p9['error']}")
            if "results_json" in p9:
                rj = p9["results_json"]
                lines.append(f"  - Total: {rj.get('total', '?')}")
                lines.append(f"  - Failed: {rj.get('failed', '?')}")
            lines.append("")
        elif tunnel_smoke_result.phase9_skipped:
            lines.append(f"- **Phase 9 Real Trace**: SKIPPED")
            lines.append(f"  - Reason: {tunnel_smoke_result.phase9_skip_reason[:200]}")
            lines.append(f"  - Real netns available: {tunnel_smoke_result.real_netns_available}")
            lines.append("")

    # Failure summary — separate pre-apply from post-apply
    pre_apply_checks = [
        ("Compile Check", compile_result),
        ("Targeted Tests", targeted_result),
        ("Full Test Suite", full_result),
        ("Git Status", git_result),
        ("Git Apply Check", git_apply_check_result if patch_text is not None else None),
    ]
    pre_failures = [(l, r) for l, r in pre_apply_checks if r is not None and not r.success]

    # Add LLM-suggested validation failures as non-blocking pre-apply failures
    if llm_validation_results:
        for r in llm_validation_results:
            if not r.success:
                pre_failures.append(
                    (f"[Non-blocking] LLM-Suggested: {r.command[:80]}", r)
                )

    post_apply_checks = []
    if patch_was_applied:
        post_apply_checks.append(("Git Apply", apply_result))
        if post_apply_validation:
            for label, result in post_apply_validation.items():
                if result is not None:
                    post_apply_checks.append((f"Post-Apply {label}", result))
        if rs_result is not None and not rs_result.success:
            post_apply_checks.append(("Replacement Smoke", type("_", (), {
                "success": False, "returncode": rs_result.returncode,
                "stderr": rs_result.error or "",
            })()))
    post_failures = [(l, r) for l, r in post_apply_checks if not r.success]

    # Compute flags used in both Failure Summary and Conclusion
    has_coverage_warnings = bool(artifact_coverage_warnings)
    core_pre_failures = [(l, r) for l, r in pre_failures
                         if not l.startswith("[Non-blocking]")]
    has_llm_failures = any(l.startswith("[Non-blocking]") for l, _ in pre_failures)

    lines.append("## Failure Summary")
    lines.append("")
    if pre_failures:
        lines.append("### Pre-Apply")
        lines.append("")
        for label, result in pre_failures:
            lines.append(f"- **{label}**: returncode={result.returncode}")
            if result.stderr:
                lines.append(f"  ```\n  {result.stderr[:500]}\n  ```")
    if post_failures:
        lines.append("### Post-Apply")
        lines.append("")
        for label, result in post_failures:
            lines.append(f"- **{label}**: returncode={result.returncode}")
            if result.stderr:
                lines.append(f"  ```\n  {result.stderr[:500]}\n  ```")
    if not pre_failures and not post_failures:
        if has_coverage_warnings:
            lines.append("No validation failures detected. "
                         "See Artifact Coverage Warnings above.")
        else:
            lines.append("No failures detected.")
    elif not core_pre_failures and not post_failures and has_llm_failures:
        lines.append(
            "Core checks passed. Non-blocking LLM-suggested validation "
            "failures listed above — these do not prevent patch application."
        )
    lines.append("")

    # Conclusion
    post_apply_all_pass = len(post_failures) == 0
    core_all_pass = len(core_pre_failures) == 0 and post_apply_all_pass
    all_pass = core_all_pass and not has_llm_failures and not has_coverage_warnings

    lines.append("## Conclusion")
    lines.append("")
    if all_pass:
        lines.append("All validation checks passed.")
    elif core_all_pass and has_llm_failures and not has_coverage_warnings:
        lines.append(
            "Core validation passed, patch apply check passed, "
            "but optional LLM-suggested validation commands had failures "
            "(non-blocking). See failure summary above."
        )
    elif core_all_pass and has_llm_failures and has_coverage_warnings:
        lines.append(
            "Core validation passed, but optional LLM-suggested validation "
            "commands had failures (non-blocking) and some expected artifacts "
            "were missing from the patch. See failure summary and coverage "
            "warnings above."
        )
    elif core_all_pass and has_coverage_warnings:
        lines.append(
            "Core validation passed, but some expected artifacts (tests, docs, "
            "or config files) were missing from the patch. See artifact "
            "coverage warnings above."
        )
    elif patch_was_applied and post_apply_all_pass:
        lines.append(
            f"All post-apply checks passed ({len(pre_failures)} pre-apply "
            f"check(s) failed — likely due to test data being stale after revert)."
        )
    else:
        lines.append("Some validation checks failed. See failure summary above.")
    lines.append("")

    # Commit advice section (only when commit advice was generated)
    commit_advice = commit_message is not None
    if commit_advice:
        lines.append("## Commit Advice")
        lines.append("")
        lines.append("- **Commit was suggested but NOT created.**")
        lines.append("- **Push was NOT performed.**")
        if commit_message:
            lines.append("")
            lines.append("### Suggested Commit Message")
            lines.append("")
            lines.append("```")
            lines.append(commit_message)
            lines.append("```")
        if commit_changed_files:
            lines.append("")
            lines.append("### Changed Files")
            lines.append("")
            for fp in commit_changed_files:
                lines.append(f"- `{fp}`")
        lines.append("")
        lines.append("> Review the changes manually. Commit when ready. **Do not push automatically.**")
        lines.append("")

    if patch_was_applied:
        if post_apply_all_pass:
            lines.append("> Patch was applied and all post-apply checks passed.")
            lines.append("> The working tree now contains the patched changes.")
            lines.append("> Commit the changes manually when ready. **Do not push automatically.**")
        else:
            lines.append("> **Do not commit until failures are fixed.**")
            lines.append("> Patch was applied but post-apply validation failed.")
            lines.append("> Review the failures, fix them, and re-run validation before committing.")
    elif patch_text is not None:
        lines.append("> Patch was generated and saved as `patch.diff` — NOT applied.")
        lines.append("> Review the diff manually before applying with `git apply`.")
    elif patch_generation_error is not None:
        lines.append("> Patch generation failed. Raw LLM outputs saved for debugging.")
        lines.append("> Review the raw attempts and re-run with a refined request.")
    else:
        lines.append("> MVP mode: plan + validation only. No code changes were applied.")

    return "\n".join(lines)


def _append_result_section(lines: list, label: str, result) -> None:
    """Append a validation result section to the report lines."""
    lines.append(f"### {label}")
    lines.append("")
    if result is None:
        lines.append("_(not run)_")
        lines.append("")
        return
    lines.append(f"- **Command**: `{result.command}`")
    lines.append(f"- **Return Code**: {result.returncode}")
    lines.append(f"- **Success**: {'Yes' if result.success else 'No'}")
    if result.stderr:
        lines.append(f"- **Stderr**:")
        lines.append(f"  ```")
        for line in result.stderr.strip().splitlines()[-10:]:
            lines.append(f"  {line}")
        lines.append(f"  ```")
    lines.append("")
