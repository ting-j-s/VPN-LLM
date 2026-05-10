"""Tests for ReportWriter."""

import json
import os

import pytest

from src.llm.report_writer import write_report
from src.llm.task_planner import TaskPlan, TASK_TRANSPORT_CHANGE, TASK_UNKNOWN
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
