"""Task Record Manager for LLM Agent.

Persists task metadata, plans, validation results, and reports to disk.
Each task gets a unique directory under .llm_tasks/.
"""

import json
import os
from dataclasses import asdict
from datetime import datetime

from src.llm.safety_guard import SafetyGuard


class TaskRecordManager:
    """Manages task record directories and file I/O.

    Usage:
        mgr = TaskRecordManager(".llm_tasks")
        task_id = mgr.create_task("switch to websocket")
        mgr.save_plan(task_id, plan)
        mgr.save_validation_result(task_id, results_dict)
        mgr.update_status(task_id, "completed", all_passed=True)
        mgr.save_report(task_id, report_markdown)
    """

    def __init__(self, base_dir: str = ".llm_tasks"):
        self.base_dir = os.path.normpath(base_dir)
        SafetyGuard.validate_write_path(self.base_dir)

    def create_task(self, request: str) -> str:
        """Create a new task record directory and write request.txt.

        Returns the generated task_id.
        """
        task_id = datetime.now().strftime("task_%Y%m%d_%H%M%S_%f")
        task_dir = self._task_dir(task_id)

        full_dir = os.path.join(self.base_dir, task_id)
        SafetyGuard.validate_write_path(full_dir)

        os.makedirs(full_dir, exist_ok=True)
        self._write_file(task_id, "request.txt", request)
        return task_id

    def save_plan(self, task_id: str, plan, planner_type: str = "rule_based") -> None:
        """Serialize a TaskPlan (or LLMTaskPlan) to plan.json."""
        plan_dict = {
            "planner_type": planner_type,
            "task_type": plan.task_type,
            "description": getattr(plan, "description", ""),
            "target_transport": plan.target_transport,
            "affected_areas": plan.affected_areas,
        }
        # Include LLM-specific fields when present
        if hasattr(plan, "summary"):
            plan_dict["summary"] = plan.summary
        if hasattr(plan, "candidate_files"):
            plan_dict["candidate_files"] = plan.candidate_files
        if hasattr(plan, "validation_commands"):
            plan_dict["validation_commands"] = plan.validation_commands
        if hasattr(plan, "risk_level"):
            plan_dict["risk_level"] = plan.risk_level
        self._write_file(task_id, "plan.json", json.dumps(plan_dict, indent=2, ensure_ascii=False))

    def save_validation_result(self, task_id: str, results: dict) -> None:
        """Serialize validation results to validation.json.

        Args:
            task_id: The task identifier.
            results: Dict mapping label (str) to ValidationResult or None.
        """
        serialized = {}
        for label, result in results.items():
            if result is None:
                serialized[label] = None
            else:
                serialized[label] = {
                    "command": result.command,
                    "returncode": result.returncode,
                    "success": result.success,
                    "stdout": result.stdout[-2000:] if result.stdout else "",
                    "stderr": result.stderr[-2000:] if result.stderr else "",
                }
        self._write_file(task_id, "validation.json", json.dumps(serialized, indent=2, ensure_ascii=False))

    def update_status(self, task_id: str, status: str, all_passed: bool = False) -> None:
        """Write status.json with current task status."""
        status_dict = {
            "status": status,
            "all_checks_passed": all_passed,
            "timestamp": datetime.now().isoformat(),
        }
        self._write_file(task_id, "status.json", json.dumps(status_dict, indent=2))

    def save_patch(self, task_id: str, patch_text: str) -> None:
        """Write patch.diff to the task directory.

        The patch is saved but NEVER automatically applied.
        Human review and manual application required.
        """
        filepath = os.path.join(self._task_dir(task_id), "patch.diff")
        SafetyGuard.validate_write_path(filepath)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(patch_text)

    def save_apply_result(self, task_id: str, apply_result, post_apply_results: dict) -> None:
        """Save patch application result and post-apply validation results.

        Args:
            task_id: The task identifier.
            apply_result: ValidationResult from git apply.
            post_apply_results: Dict mapping label (str) to ValidationResult or None.
        """
        apply_data = {
            "command": apply_result.command,
            "returncode": apply_result.returncode,
            "success": apply_result.success,
            "stdout": apply_result.stdout[-2000:] if apply_result.stdout else "",
            "stderr": apply_result.stderr[-2000:] if apply_result.stderr else "",
        }
        self._write_file(task_id, "apply_result.json", json.dumps(apply_data, indent=2, ensure_ascii=False))

        serialized = {}
        for label, result in post_apply_results.items():
            if result is None:
                serialized[label] = None
            else:
                serialized[label] = {
                    "command": result.command,
                    "returncode": result.returncode,
                    "success": result.success,
                    "stdout": result.stdout[-2000:] if result.stdout else "",
                    "stderr": result.stderr[-2000:] if result.stderr else "",
                }
        self._write_file(task_id, "post_apply_validation.json", json.dumps(serialized, indent=2, ensure_ascii=False))

    def save_commit_advice(self, task_id: str, advice: dict) -> None:
        """Write commit advice files to the task directory.

        Args:
            task_id: The task identifier.
            advice: dict with keys: message, changed_files, diff_stat, validation_passed.
        """
        msg = advice.get("message", "")
        self._write_file(task_id, "suggested_commit_message.txt", msg + "\n")
        summary = self._build_commit_summary(advice)
        self._write_file(task_id, "commit_summary.md", summary)

    @staticmethod
    def _build_commit_summary(advice: dict) -> str:
        """Build commit_summary.md content from advice dict."""
        message = advice.get("message", "")
        changed_files = advice.get("changed_files", [])
        diff_stat = advice.get("diff_stat", "")
        validation_passed = advice.get("validation_passed", False)

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

        return "\n".join(lines) + "\n"

    def save_report(self, task_id: str, report: str) -> None:
        """Write report.md."""
        self._write_file(task_id, "report.md", report)

    def get_task_dir(self, task_id: str) -> str:
        """Return the full path to a task record directory."""
        return self._task_dir(task_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _task_dir(self, task_id: str) -> str:
        return os.path.join(self.base_dir, task_id)

    def _write_file(self, task_id: str, filename: str, content: str) -> None:
        filepath = os.path.join(self._task_dir(task_id), filename)
        SafetyGuard.validate_write_path(filepath)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
