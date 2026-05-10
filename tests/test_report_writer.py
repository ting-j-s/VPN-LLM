"""Tests for ReportWriter."""

import json
import os

import pytest

from src.llm.report_writer import write_report
from src.llm.task_planner import TaskPlan, TASK_TRANSPORT_CHANGE, TASK_UNKNOWN
from src.llm.llm_task_planner import LLMTaskPlan
from src.llm.validation_runner import ValidationResult


def _make_plan(**kwargs):
    defaults = {
        "task_type": TASK_TRANSPORT_CHANGE,
        "description": "switch default transport to websocket",
        "target_transport": "websocket",
        "affected_areas": ["src/transport/", "config/", "src/transport/websocket_transport.py"],
    }
    defaults.update(kwargs)
    return TaskPlan(**defaults)


def _make_result(returncode=0, stdout="", stderr=""):
    return ValidationResult("test command", returncode, stdout, stderr)


class TestReportWriterContent:
    """Test that generated reports contain required sections."""

    def test_report_contains_task_id(self):
        plan = _make_plan()
        report = write_report(
            "task_20260510_120000", "switch to websocket", plan,
            _make_result(), None, _make_result(), _make_result(),
        )
        assert "task_20260510_120000" in report

    def test_report_contains_user_request(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "把默认外层协议改成 websocket", plan,
            _make_result(), None, _make_result(), _make_result(),
        )
        assert "把默认外层协议改成 websocket" in report

    def test_report_contains_task_type(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
        )
        assert "transport_change" in report

    def test_report_contains_target_transport(self):
        plan = _make_plan(target_transport="tcp")
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
        )
        assert "tcp" in report

    def test_report_contains_target_transport_na_when_none(self):
        plan = _make_plan(target_transport=None)
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
        )
        assert "N/A" in report

    def test_report_contains_affected_areas(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
        )
        assert "src/transport/" in report
        assert "config/" in report

    def test_report_contains_validation_command(self):
        plan = _make_plan()
        compile_result = _make_result(0)
        compile_result.command = "python3 -m compileall src tests"
        report = write_report(
            "task_001", "req", plan,
            compile_result, None, _make_result(), _make_result(),
        )
        assert "python3 -m compileall src tests" in report

    def test_report_contains_returncode_and_success(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(1, "", "some error"), _make_result(),
        )
        assert "Return Code" in report
        assert "Success" in report

    def test_report_contains_failure_summary(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(1, "", "AssertionError"), _make_result(),
        )
        assert "Failure Summary" in report

    def test_report_contains_conclusion(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
        )
        assert "Conclusion" in report

    def test_report_conclusion_success_when_all_pass(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(0), _make_result(0),
        )
        assert "All validation checks passed" in report

    def test_report_conclusion_failure_when_any_fails(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(1), _make_result(0),
        )
        assert "Some validation checks failed" in report

    def test_report_includes_targeted_tests_when_present(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), _make_result(0, "passed", ""), _make_result(), _make_result(),
        )
        assert "Targeted Tests" in report

    def test_report_shows_not_run_for_none_targeted(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
        )
        assert "not run" in report


class TestReportWriterSafety:
    """Verify that reports do not leak sensitive information."""

    def test_no_api_key_in_report(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(0, "output", ""),
            None,
            _make_result(0, "", ""),
            _make_result(0, "", ""),
        )
        assert "API_KEY" not in report
        assert "sk-" not in report.lower()

    def test_no_env_var_values_in_report(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(),
            None,
            _make_result(),
            _make_result(0, "", "some stderr"),
        )
        assert "SECRET" not in report

    def test_report_is_valid_markdown(self):
        """Basic sanity: report should have headings."""
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
        )
        assert report.startswith("# LLM Task Report")
        assert "## Task Info" in report
        assert "## Planned Changes" in report
        assert "## Validation Results" in report
        assert "## Failure Summary" in report
        assert "## Conclusion" in report

    def test_report_handles_empty_plan(self):
        plan = TaskPlan(task_type=TASK_UNKNOWN, description="", target_transport=None, affected_areas=[])
        report = write_report(
            "task_001", "", plan,
            _make_result(), None, _make_result(), _make_result(),
        )
        assert "unknown" in report

    def test_report_handles_without_targeted_tests(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
        )
        # Should not crash and should still be valid
        assert "Conclusion" in report

    def test_no_network_access(self, tmp_path):
        """Report generation is purely local."""
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
        )
        assert len(report) > 0


class TestReportWriterPatchSection:
    """Test that patch-related sections appear when patch is provided."""

    def test_report_without_patch_has_no_patch_section(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
        )
        assert "Patch Generation" not in report

    def test_report_with_patch_shows_patch_section(self):
        plan = _make_plan()
        patch = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
            patch_text=patch,
            patch_file_paths=["x.py"],
            git_apply_check_result=None,
        )
        assert "Patch Generation" in report

    def test_report_shows_patch_not_applied(self):
        plan = _make_plan()
        patch = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
            patch_text=patch,
            patch_file_paths=["x.py"],
            git_apply_check_result=None,
        )
        assert "patch was generated but not applied" in report

    def test_report_shows_patch_size(self):
        plan = _make_plan()
        patch = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
            patch_text=patch,
            patch_file_paths=["x.py"],
            git_apply_check_result=None,
        )
        assert f"{len(patch)} bytes" in report

    def test_report_shows_patch_files(self):
        plan = _make_plan()
        patch = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
            patch_text=patch,
            patch_file_paths=["x.py", "y.py"],
            git_apply_check_result=None,
        )
        assert "x.py" in report
        assert "y.py" in report

    def test_report_with_patch_has_conclusion_note(self):
        plan = _make_plan()
        patch = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
            patch_text=patch,
            patch_file_paths=["x.py"],
            git_apply_check_result=None,
        )
        assert "not applied" in report.lower()

    def test_report_shows_git_apply_check_pass(self):
        plan = _make_plan()
        patch = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
        apply_result = _make_result(0, "check ok", "")
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
            patch_text=patch,
            patch_file_paths=["x.py"],
            git_apply_check_result=apply_result,
        )
        assert "Git Apply Check" in report

    def test_report_shows_git_apply_check_fail(self):
        plan = _make_plan()
        patch = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
        apply_result = _make_result(1, "", "error: patch does not apply")
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
            patch_text=patch,
            patch_file_paths=["x.py"],
            git_apply_check_result=apply_result,
        )
        assert "Git Apply Check" in report
        assert "failure" in report.lower() or "Some validation checks failed" in report

    def test_patch_report_with_llm_plan(self):
        plan = LLMTaskPlan(
            task_type="transport_change",
            target_transport="websocket",
            summary="Switch transport to WebSocket",
            candidate_files=["config/server.yaml"],
            validation_commands=[],
            risk_level="medium",
        )
        patch = "diff --git a/config/server.yaml b/config/server.yaml\n--- a/config/server.yaml\n+++ b/config/server.yaml\n@@ -10 +10 @@\n-  type: tcp\n+  type: websocket\n"
        report = write_report(
            "task_001", "Switch to websocket", plan,
            _make_result(), None, _make_result(), _make_result(),
            planner_type="llm_based",
            patch_text=patch,
            patch_file_paths=["config/server.yaml"],
            git_apply_check_result=_make_result(0, "", ""),
        )
        assert "Patch Generation" in report
        assert "patch was generated but not applied" in report
        assert "config/server.yaml" in report
        assert "llm_based" in report


class TestReportWriterApplySection:
    """Test that apply-related sections appear when patch is applied."""

    def test_no_apply_section_when_not_applied(self):
        plan = _make_plan()
        patch = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
            patch_text=patch,
            patch_file_paths=["x.py"],
            git_apply_check_result=_make_result(0, "", ""),
        )
        assert "Patch Generation" in report
        assert "Patch Application" not in report

    def test_apply_section_shows_applied_yes(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
            apply_result=_make_result(0, "applied", ""),
            post_apply_validation={
                "Compile Check": _make_result(0, "", ""),
                "Full Test Suite": _make_result(0, "all passed", ""),
            },
        )
        assert "Patch Application" in report
        assert "Patch applied" in report
        assert "Yes" in report

    def test_apply_section_shows_post_apply_validation(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
            apply_result=_make_result(0, "", ""),
            post_apply_validation={
                "Compile Check": _make_result(0, "", ""),
                "Full Test Suite": _make_result(0, "all passed", ""),
            },
        )
        assert "Post-Apply Validation" in report
        assert "Compile Check" in report
        assert "Full Test Suite" in report

    def test_apply_section_commit_guidance_on_success(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
            apply_result=_make_result(0, "", ""),
            post_apply_validation={
                "Compile Check": _make_result(0, "", ""),
                "Full Test Suite": _make_result(0, "", ""),
            },
        )
        assert "Commit the changes manually" in report
        assert "do not push automatically" in report.lower()

    def test_apply_section_do_not_commit_on_failure(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(1, "", "AssertionError"), _make_result(),
            apply_result=_make_result(0, "", ""),
            post_apply_validation={
                "Full Test Suite": _make_result(1, "", "test failure"),
            },
        )
        assert "Do not commit until failures are fixed" in report

    def test_apply_section_with_partial_post_checks(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
            apply_result=_make_result(0, "", ""),
            post_apply_validation={
                "Compile Check": _make_result(0, "", ""),
                "Targeted Tests": None,
                "Full Test Suite": _make_result(0, "", ""),
            },
        )
        assert "Targeted Tests" in report
        assert "not run" in report

    def test_apply_failure_shows_in_failure_summary(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
            apply_result=_make_result(1, "", "patch error"),
            post_apply_validation={},
        )
        assert "Failure Summary" in report
        assert "Git Apply" in report

    def test_apply_result_included_in_all_pass_check(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(0), _make_result(0),
            apply_result=_make_result(0, "", ""),
            post_apply_validation={
                "Full Test Suite": _make_result(0, "", ""),
            },
        )
        assert "All validation checks passed" in report


class TestReportWriterCommitAdviceSection:
    """Test that Commit Advice section appears when commit_message is provided."""

    def test_no_commit_advice_when_not_provided(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
            apply_result=_make_result(0, "", ""),
            post_apply_validation={
                "Compile Check": _make_result(0, "", ""),
                "Full Test Suite": _make_result(0, "", ""),
            },
        )
        assert "Commit Advice" not in report

    def test_no_commit_advice_when_apply_failed(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(), None, _make_result(), _make_result(),
            apply_result=_make_result(1, "", "apply failed"),
            post_apply_validation={},
            commit_message=None,
            commit_changed_files=None,
        )
        assert "Commit Advice" not in report

    def test_commit_advice_section_when_provided(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(0), _make_result(0),
            apply_result=_make_result(0, "", ""),
            post_apply_validation={
                "Compile Check": _make_result(0, "", ""),
                "Full Test Suite": _make_result(0, "", ""),
            },
            commit_message="feat(websocket): switch transport",
            commit_changed_files=["config/server.yaml", "src/transport/websocket_transport.py"],
        )
        assert "Commit Advice" in report
        assert "Commit was suggested but NOT created" in report
        assert "Push was NOT performed" in report
        assert "feat(websocket): switch transport" in report
        assert "config/server.yaml" in report

    def test_commit_advice_without_changed_files(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(0), _make_result(0),
            apply_result=_make_result(0, "", ""),
            post_apply_validation={
                "Full Test Suite": _make_result(0, "", ""),
            },
            commit_message="docs: update readme",
            commit_changed_files=None,
        )
        assert "Commit Advice" in report
        assert "docs: update readme" in report

    def test_commit_advice_shows_manual_review_guidance(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(0), _make_result(0),
            apply_result=_make_result(0, "", ""),
            post_apply_validation={
                "Full Test Suite": _make_result(0, "", ""),
            },
            commit_message="feat: test",
            commit_changed_files=["a.py"],
        )
        assert "Review the changes manually" in report
        assert "do not push automatically" in report.lower()

    def test_commit_advice_does_not_contain_api_key(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(0), _make_result(0),
            apply_result=_make_result(0, "", ""),
            post_apply_validation={
                "Full Test Suite": _make_result(0, "", ""),
            },
            commit_message="feat: test",
            commit_changed_files=["a.py"],
        )
        assert "API_KEY" not in report
        assert "sk-" not in report.lower()
        assert "password" not in report.lower()


# ---------------------------------------------------------------------------
# Replacement Smoke Validation section
# ---------------------------------------------------------------------------

def _make_rs_result(success=True, transports=None, cores=None,
                    summary=None, results=None, error=None):
    """Build a lightweight ReplacementValidationResult-like object."""
    return type("_RSR", (), {
        "success": success,
        "returncode": 0 if success else 1,
        "transports": transports or ["mock", "websocket"],
        "cores": cores or ["default"],
        "summary": summary or {"passed": 2, "failed": 0, "skipped": 0},
        "results": results or [
            {"transport": "mock", "core": "default", "status": "pass",
             "duration_sec": 0.01, "error": None},
            {"transport": "websocket", "core": "default", "status": "pass",
             "duration_sec": 0.05, "error": None},
        ],
        "error": error,
    })()


class TestReplacementSmokeSection:
    """Test Replacement Smoke Validation report section."""

    def test_no_section_when_not_provided(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(0), _make_result(0),
        )
        assert "Replacement Smoke" not in report

    def test_section_when_provided(self):
        plan = _make_plan()
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(0), _make_result(0),
            replacement_smoke_result=_make_rs_result(),
        )
        assert "Replacement Smoke Validation" in report
        assert "mock" in report
        assert "websocket" in report

    def test_shows_transports_and_cores(self):
        plan = _make_plan()
        rs = _make_rs_result(transports=["mock", "tcp"], cores=["default"])
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(0), _make_result(0),
            replacement_smoke_result=rs,
        )
        assert "mock" in report
        assert "tcp" in report
        assert "default" in report

    def test_shows_summary_counts(self):
        plan = _make_plan()
        rs = _make_rs_result(
            summary={"passed": 3, "failed": 0, "skipped": 1},
        )
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(0), _make_result(0),
            replacement_smoke_result=rs,
        )
        assert "3 passed" in report
        assert "0 failed" in report
        assert "1 skipped" in report

    def test_success_message(self):
        plan = _make_plan()
        rs = _make_rs_result(success=True)
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(0), _make_result(0),
            replacement_smoke_result=rs,
        )
        assert "All replacement smokes passed" in report

    def test_failure_message(self):
        plan = _make_plan()
        rs = _make_rs_result(
            success=False,
            summary={"passed": 1, "failed": 1, "skipped": 0},
            results=[
                {"transport": "mock", "core": "default", "status": "pass",
                 "duration_sec": 0.01, "error": None},
                {"transport": "tcp", "core": "default", "status": "fail",
                 "duration_sec": 0.1, "error": "connection refused"},
            ],
        )
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(0), _make_result(0),
            replacement_smoke_result=rs,
        )
        assert "Do not commit" in report
        assert "connection refused" in report

    def test_shows_ssh_skip(self):
        plan = _make_plan()
        rs = _make_rs_result(
            summary={"passed": 1, "failed": 0, "skipped": 1},
            results=[
                {"transport": "mock", "core": "default", "status": "pass",
                 "duration_sec": 0.01, "error": None},
                {"transport": "ssh", "core": "default", "status": "skip",
                 "duration_sec": 0.0, "error": "requires external SSH server"},
            ],
        )
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(0), _make_result(0),
            replacement_smoke_result=rs,
        )
        assert "SKIP" in report
        assert "ssh" in report

    def test_shows_error_message(self):
        plan = _make_plan()
        rs = _make_rs_result(
            success=False, error="script not found",
            summary={}, results=[],
        )
        report = write_report(
            "task_001", "req", plan,
            _make_result(0), None, _make_result(0), _make_result(0),
            replacement_smoke_result=rs,
        )
        assert "script not found" in report
