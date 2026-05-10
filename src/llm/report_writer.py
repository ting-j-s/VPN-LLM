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

    Returns:
        Markdown report string.
    """
    risk_level = getattr(plan, "risk_level", None)

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

    # Patch section (only when patch was generated)
    if patch_text is not None:
        lines.append("## Patch Generation")
        lines.append("")
        lines.append(f"- **Status**: patch was generated but not applied")
        lines.append(f"- **Patch size**: {len(patch_text)} bytes")
        if patch_file_paths:
            lines.append("- **Files in patch**:")
            for fp in patch_file_paths:
                lines.append(f"  - `{fp}`")
        else:
            lines.append("- **Files in patch**: _(none detected)_")
        lines.append("")

        _append_result_section(lines, "Git Apply Check", git_apply_check_result)

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

    # Failure summary
    all_checks = [
        ("Compile Check", compile_result),
        ("Targeted Tests", targeted_result),
        ("Full Test Suite", full_result),
        ("Git Status", git_result),
        ("Git Apply Check", git_apply_check_result if patch_text is not None else None),
    ]
    if patch_was_applied:
        all_checks.append(("Git Apply", apply_result))
        if post_apply_validation:
            for label, result in post_apply_validation.items():
                if result is not None:
                    all_checks.append((f"Post-Apply {label}", result))
    failures = [(label, result) for label, result in all_checks if result is not None and not result.success]

    lines.append("## Failure Summary")
    lines.append("")
    if failures:
        for label, result in failures:
            lines.append(f"- **{label}**: returncode={result.returncode}")
            if result.stderr:
                lines.append(f"  ```\n  {result.stderr[:500]}\n  ```")
    else:
        lines.append("No failures detected.")
    lines.append("")

    # Conclusion
    all_pass = compile_result.success and full_result.success
    if targeted_result is not None:
        all_pass = all_pass and targeted_result.success
    if git_apply_check_result is not None:
        all_pass = all_pass and git_apply_check_result.success
    post_apply_all_pass = True
    if patch_was_applied:
        all_pass = all_pass and apply_result.success
        if post_apply_validation:
            for result in post_apply_validation.values():
                if result is not None and not result.success:
                    all_pass = False
                    post_apply_all_pass = False

    lines.append("## Conclusion")
    lines.append("")
    if all_pass:
        lines.append("All validation checks passed.")
    else:
        lines.append("Some validation checks failed. See failure summary above.")
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
