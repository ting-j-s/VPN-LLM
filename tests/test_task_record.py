"""Tests for TaskRecordManager."""

import json
import os

import pytest

from src.llm.task_record import TaskRecordManager
from src.llm.task_planner import TaskPlan, TASK_TRANSPORT_CHANGE
from src.llm.validation_runner import ValidationResult


class TestTaskRecordManager:
    """Test task record creation and file I/O."""

    def test_create_task_returns_task_id(self, tmp_path):
        mgr = TaskRecordManager(str(tmp_path))
        task_id = mgr.create_task("switch default transport to websocket")
        assert task_id.startswith("task_")
        assert task_id.startswith("task_")
        assert len(task_id) == len("task_YYYYMMDD_HHMMSS_ffffff")

    def test_create_task_writes_request_txt(self, tmp_path):
        mgr = TaskRecordManager(str(tmp_path))
        task_id = mgr.create_task("switch default transport to websocket")
        req_path = os.path.join(tmp_path, task_id, "request.txt")
        assert os.path.isfile(req_path)
        content = open(req_path).read()
        assert content == "switch default transport to websocket"

    def test_create_task_creates_directory(self, tmp_path):
        mgr = TaskRecordManager(str(tmp_path))
        task_id = mgr.create_task("test request")
        task_dir = os.path.join(tmp_path, task_id)
        assert os.path.isdir(task_dir)

    def test_save_plan_writes_plan_json(self, tmp_path):
        mgr = TaskRecordManager(str(tmp_path))
        task_id = mgr.create_task("switch to tcp")
        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch to tcp",
            target_transport="tcp",
            affected_areas=["src/transport/", "config/"],
        )
        mgr.save_plan(task_id, plan)
        plan_path = os.path.join(tmp_path, task_id, "plan.json")
        assert os.path.isfile(plan_path)
        data = json.load(open(plan_path))
        assert data["task_type"] == "transport_change"
        assert data["target_transport"] == "tcp"
        assert "src/transport/" in data["affected_areas"]

    def test_save_plan_preserves_chinese_description(self, tmp_path):
        mgr = TaskRecordManager(str(tmp_path))
        task_id = mgr.create_task("把默认外层协议从 TCP 改成 WebSocket")
        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="把默认外层协议从 TCP 改成 WebSocket",
            target_transport="websocket",
            affected_areas=["src/transport/", "config/"],
        )
        mgr.save_plan(task_id, plan)
        plan_path = os.path.join(tmp_path, task_id, "plan.json")
        data = json.load(open(plan_path))
        assert data["description"] == "把默认外层协议从 TCP 改成 WebSocket"
        assert data["target_transport"] == "websocket"

    def test_save_validation_result_writes_validation_json(self, tmp_path):
        mgr = TaskRecordManager(str(tmp_path))
        task_id = mgr.create_task("test")
        results = {
            "compile": ValidationResult("python3 -m compileall src", 0, "ok", ""),
            "full_tests": ValidationResult("pytest tests/", 0, "all passed", ""),
            "targeted": None,
        }
        mgr.save_validation_result(task_id, results)
        val_path = os.path.join(tmp_path, task_id, "validation.json")
        assert os.path.isfile(val_path)
        data = json.load(open(val_path))
        assert data["compile"]["returncode"] == 0
        assert data["compile"]["success"] is True
        assert data["full_tests"]["returncode"] == 0
        assert data["targeted"] is None

    def test_save_validation_result_captures_failure(self, tmp_path):
        mgr = TaskRecordManager(str(tmp_path))
        task_id = mgr.create_task("test")
        results = {
            "compile": ValidationResult("python3 -m compileall src", 1, "", "SyntaxError"),
        }
        mgr.save_validation_result(task_id, results)
        val_path = os.path.join(tmp_path, task_id, "validation.json")
        data = json.load(open(val_path))
        assert data["compile"]["returncode"] == 1
        assert data["compile"]["success"] is False
        assert "SyntaxError" in data["compile"]["stderr"]

    def test_update_status_writes_status_json(self, tmp_path):
        mgr = TaskRecordManager(str(tmp_path))
        task_id = mgr.create_task("test")
        mgr.update_status(task_id, "completed", all_passed=True)
        status_path = os.path.join(tmp_path, task_id, "status.json")
        assert os.path.isfile(status_path)
        data = json.load(open(status_path))
        assert data["status"] == "completed"
        assert data["all_checks_passed"] is True
        assert "timestamp" in data

    def test_update_status_failed(self, tmp_path):
        mgr = TaskRecordManager(str(tmp_path))
        task_id = mgr.create_task("test")
        mgr.update_status(task_id, "failed", all_passed=False)
        status_path = os.path.join(tmp_path, task_id, "status.json")
        data = json.load(open(status_path))
        assert data["status"] == "failed"
        assert data["all_checks_passed"] is False

    def test_save_report_writes_report_md(self, tmp_path):
        mgr = TaskRecordManager(str(tmp_path))
        task_id = mgr.create_task("test")
        report = "# LLM Task Report\n\nAll checks passed."
        mgr.save_report(task_id, report)
        report_path = os.path.join(tmp_path, task_id, "report.md")
        assert os.path.isfile(report_path)
        content = open(report_path).read()
        assert "# LLM Task Report" in content
        assert "All checks passed" in content

    def test_all_artifacts_present_after_full_flow(self, tmp_path):
        mgr = TaskRecordManager(str(tmp_path))
        task_id = mgr.create_task("full flow test")
        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="full flow test",
            target_transport="websocket",
            affected_areas=["src/transport/", "config/"],
        )
        mgr.save_plan(task_id, plan)
        mgr.save_validation_result(task_id, {
            "compile": ValidationResult("compileall", 0, "", ""),
            "full_tests": ValidationResult("pytest", 0, "", ""),
        })
        mgr.update_status(task_id, "completed", all_passed=True)
        mgr.save_report(task_id, "# Report\nDone.")

        task_dir = os.path.join(tmp_path, task_id)
        for fname in ("request.txt", "plan.json", "validation.json", "status.json", "report.md"):
            assert os.path.isfile(os.path.join(task_dir, fname)), f"Missing: {fname}"

    def test_unique_task_ids(self, tmp_path):
        mgr = TaskRecordManager(str(tmp_path))
        id1 = mgr.create_task("first")
        id2 = mgr.create_task("second")
        assert id1 != id2

    def test_get_task_dir(self, tmp_path):
        mgr = TaskRecordManager(str(tmp_path))
        task_id = mgr.create_task("test")
        expected = os.path.join(str(tmp_path), task_id)
        assert mgr.get_task_dir(task_id) == expected

    def test_does_not_access_network(self, tmp_path):
        """TaskRecordManager should work purely on local filesystem."""
        mgr = TaskRecordManager(str(tmp_path))
        task_id = mgr.create_task("local only")
        assert os.path.isdir(os.path.join(tmp_path, task_id))


class TestTaskRecordSafety:
    """Verify that sensitive paths are blocked."""

    def test_blocks_env_directory(self, tmp_path):
        with pytest.raises(Exception):
            TaskRecordManager(os.path.join(str(tmp_path), ".env"))

    def test_blocks_claude_directory(self, tmp_path):
        with pytest.raises(Exception):
            TaskRecordManager(os.path.join(str(tmp_path), ".claude"))

    def test_blocks_git_directory(self, tmp_path):
        with pytest.raises(Exception):
            TaskRecordManager(os.path.join(str(tmp_path), ".git"))

    def test_allows_normal_directory(self, tmp_path):
        mgr = TaskRecordManager(str(tmp_path))
        assert mgr is not None
