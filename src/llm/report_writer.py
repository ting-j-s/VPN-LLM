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

    # Failure summary
    failures = []
    for label, result in [
        ("Compile Check", compile_result),
        ("Targeted Tests", targeted_result),
        ("Full Test Suite", full_result),
        ("Git Status", git_result),
    ]:
        if result is not None and not result.success:
            failures.append((label, result))

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

    lines.append("## Conclusion")
    lines.append("")
    if all_pass:
        lines.append("All validation checks passed.")
    else:
        lines.append("Some validation checks failed. See failure summary above.")
    lines.append("")
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
