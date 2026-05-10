"""Commit Advisor — read-only git analysis and commit message suggestion.

Collects diff summaries and generates rule-based commit message suggestions.
NEVER performs git add, git commit, or git push. The human remains the
final authority on all version-control actions.
"""

import os
import subprocess

from src.llm.task_planner import (
    TASK_TRANSPORT_CHANGE,
    TASK_CONFIG_CHANGE,
    TASK_TEST_ADDITION,
    TASK_DOCS_UPDATE,
    TASK_UNKNOWN,
)

# Additional task types supported by LLMTaskPlanner
TASK_BUGFIX = "bugfix"
TASK_REFACTOR = "refactor"

_TASK_PREFIX_MAP = {
    TASK_TRANSPORT_CHANGE: "feat",
    TASK_CONFIG_CHANGE: "config",
    TASK_TEST_ADDITION: "test",
    TASK_DOCS_UPDATE: "docs",
    TASK_BUGFIX: "fix",
    TASK_REFACTOR: "refactor",
    TASK_UNKNOWN: "chore",
}


class CommitAdvisor:
    """Read-only analysis of git state and commit message suggestion.

    Usage:
        advisor = CommitAdvisor()
        summary = advisor.collect_diff_summary()
        msg = advisor.suggest_commit_message(plan, summary["files"], validation_passed=True)
        # msg = "feat(websocket): switch default transport to WebSocket"
        advisor.write_commit_advice(task_dir, {
            "message": msg,
            "changed_files": summary["files"],
            "diff_stat": summary["diff_stat"],
            "validation_passed": True,
        })
        # Writes: suggested_commit_message.txt, commit_summary.md
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def collect_diff_summary():
        """Collect a read-only summary of the current git diff.

        Runs:
          - git diff --stat      (summary of changed files and lines)
          - git diff --name-only  (list of changed file paths)

        Returns a dict with keys:
          - diff_stat: str       stdout of git diff --stat
          - files: list[str]     changed file paths
          - returncode: int      0 if both commands succeeded
          - success: bool        True if both returncodes are 0
        """
        result = {
            "diff_stat": "",
            "files": [],
            "returncode": 0,
            "success": True,
        }

        # git diff --stat
        try:
            proc = subprocess.run(
                ["git", "diff", "--stat"],
                capture_output=True, text=True, timeout=10,
            )
            result["diff_stat"] = proc.stdout.strip()
            if proc.returncode != 0:
                result["returncode"] = proc.returncode
                result["success"] = False
        except subprocess.TimeoutExpired:
            result["returncode"] = -1
            result["success"] = False

        # git diff --name-only
        try:
            proc = subprocess.run(
                ["git", "diff", "--name-only"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.stdout.strip():
                result["files"] = [
                    f for f in proc.stdout.strip().splitlines() if f
                ]
            if proc.returncode != 0:
                result["returncode"] = proc.returncode
                result["success"] = False
        except subprocess.TimeoutExpired:
            result["returncode"] = -1
            result["success"] = False

        return result

    @staticmethod
    def suggest_commit_message(task_plan, changed_files, validation_passed):
        """Generate a rule-based commit message suggestion.

        Args:
            task_plan: TaskPlan or LLMTaskPlan with task_type and target_transport.
            changed_files: list of changed file paths (from collect_diff_summary).
            validation_passed: bool — whether all post-apply validation passed.

        Returns:
            Commit message string. If validation_passed is False, the message
            is prefixed with "[DO NOT COMMIT] " to signal that failures remain.
        """
        task_type = getattr(task_plan, "task_type", TASK_UNKNOWN)
        prefix = _TASK_PREFIX_MAP.get(task_type, "chore")
        target = getattr(task_plan, "target_transport", None)
        description = getattr(task_plan, "description", "") or getattr(task_plan, "summary", "")

        # Build scope from target transport if available
        scope = f"({target})" if target else ""

        # Build subject from description or changed files
        if description:
            # Limit subject line to 72 chars
            subject = description[:72] if len(description) > 72 else description
        elif changed_files:
            subject = f"changes to {', '.join(changed_files[:3])}"
        else:
            subject = "apply changes"

        msg = f"{prefix}{scope}: {subject}"

        if not validation_passed:
            msg = f"[DO NOT COMMIT] {msg}"

        return msg

    @staticmethod
    def write_commit_advice(task_dir, advice):
        """Write commit advice files to the task directory.

        Args:
            task_dir: str — path to the task record directory.
            advice: dict with keys:
                - message: str          suggested commit message
                - changed_files: list   changed file paths
                - diff_stat: str        git diff --stat output
                - validation_passed: bool

        Writes:
            - suggested_commit_message.txt  (single-line commit message)
            - commit_summary.md             (markdown summary for human review)
        """
        os.makedirs(task_dir, exist_ok=True)

        message = advice.get("message", "")
        changed_files = advice.get("changed_files", [])
        diff_stat = advice.get("diff_stat", "")
        validation_passed = advice.get("validation_passed", False)

        # Write commit message file
        msg_path = os.path.join(task_dir, "suggested_commit_message.txt")
        with open(msg_path, "w", encoding="utf-8") as f:
            f.write(message + "\n")

        # Write summary markdown
        summary_path = os.path.join(task_dir, "commit_summary.md")
        lines = [
            "# Commit Summary",
            "",
            "## Suggested Commit Message",
            "",
            "```",
            message,
            "```",
            "",
            "## Changed Files",
            "",
        ]
        if changed_files:
            for fp in changed_files:
                lines.append(f"- `{fp}`")
        else:
            lines.append("- _(no changes detected)_")

        lines.append("")
        lines.append("## Diff Stat")
        lines.append("")
        if diff_stat:
            lines.append("```")
            for line in diff_stat.splitlines():
                lines.append(line)
            lines.append("```")
        else:
            lines.append("_(no diff stat available)_")

        lines.append("")
        lines.append("## Important Notes")
        lines.append("")
        lines.append("- **Commit was suggested but NOT created.**")
        lines.append("- **Push was NOT performed.**")
        lines.append("- Review the changes manually before committing.")
        if not validation_passed:
            lines.append("- **Validation did not fully pass.** Review failures before committing.")

        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
