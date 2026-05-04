"""Tests for Code Task Manager module."""

import pytest

from src.llm.code_task_manager import CodeTask, CodeTaskManager


class TestCodeTask:
    """Test CodeTask dataclass."""

    def test_create_task(self):
        """Test creating a task with all fields."""
        task = CodeTask(
            title="Add TLS Transport",
            goal="Implement TLS transport support",
            files_to_modify=["src/transport/tls_transport.py"],
            constraints=["Use ssl module", "Support client/server modes"],
            acceptance_criteria=["Can connect", "Can send/receive data"],
            test_commands=["pytest tests/test_tls_transport.py"],
        )

        assert task.title == "Add TLS Transport"
        assert task.goal == "Implement TLS transport support"
        assert len(task.files_to_modify) == 1
        assert len(task.constraints) == 2
        assert len(task.acceptance_criteria) == 2
        assert len(task.test_commands) == 1

    def test_default_fields(self):
        """Test default field values."""
        task = CodeTask(
            title="Simple Task",
            goal="Simple goal",
        )

        assert task.files_to_modify == []
        assert task.constraints == []
        assert task.acceptance_criteria == []
        assert task.test_commands == []


class TestCodeTaskManager:
    """Test CodeTaskManager."""

    def test_create_task(self):
        """Test creating a task from requirement."""
        manager = CodeTaskManager()
        task = manager.create_task("Add TLS transport support")

        assert isinstance(task, CodeTask)
        assert task.title is not None
        assert task.goal is not None
        assert "tls" in task.title.lower() or "tls" in task.goal.lower()

    def test_create_task_extracts_files(self):
        """Test that files are extracted from requirement."""
        manager = CodeTaskManager()
        task = manager.create_task("Add WebSocket transport to the system")

        assert len(task.files_to_modify) > 0
        assert any("websocket" in f for f in task.files_to_modify)

    def test_create_task_has_constraints(self):
        """Test that constraints are generated."""
        manager = CodeTaskManager()
        task = manager.create_task("Add TCP transport with 4-byte length prefix")

        assert len(task.constraints) > 0
        assert any("4-byte" in c or "length prefix" in c for c in task.constraints)

    def test_create_task_has_acceptance_criteria(self):
        """Test that acceptance criteria are generated."""
        manager = CodeTaskManager()
        task = manager.create_task("Add new transport layer")

        assert len(task.acceptance_criteria) > 0
        assert any("test" in c.lower() for c in task.acceptance_criteria)

    def test_create_task_has_test_commands(self):
        """Test that test commands are generated."""
        manager = CodeTaskManager()
        task = manager.create_task("Add TLS transport")

        assert len(task.test_commands) > 0
        assert all("pytest" in cmd or "python3" in cmd for cmd in task.test_commands)

    def test_render_prompt_format(self):
        """Test that rendered prompt is properly formatted markdown."""
        manager = CodeTaskManager()
        task = CodeTask(
            title="Test Task",
            goal="Test the rendering",
            files_to_modify=["src/test.py"],
            constraints=["Must work"],
            acceptance_criteria=["Test passes"],
            test_commands=["pytest tests/"],
        )

        prompt = manager.render_prompt(task)

        assert "# Code Task" in prompt
        assert "## Title:" in prompt
        assert "## Goal" in prompt
        assert "## Files to Modify" in prompt
        assert "## Constraints" in prompt
        assert "## Acceptance Criteria" in prompt
        assert "## Test Commands" in prompt
        assert "src/test.py" in prompt
        assert "Must work" in prompt
        assert "Test passes" in prompt
        assert "pytest tests/" in prompt

    def test_render_prompt_contains_no_auto_execute_warning(self):
        """Test that prompt contains warning not to auto-execute."""
        manager = CodeTaskManager()
        task = CodeTask(title="Test", goal="Test")
        prompt = manager.render_prompt(task)

        assert "Do NOT automatically execute" in prompt
        assert "human review" in prompt.lower() or "human confirmation" in prompt.lower()

    def test_render_prompt_with_minimal_task(self):
        """Test rendering a task with minimal fields."""
        manager = CodeTaskManager()
        task = CodeTask(title="Minimal", goal="Minimal goal")
        prompt = manager.render_prompt(task)

        # Should still render with defaults
        assert "Minimal" in prompt
        assert "Minimal goal" in prompt

    def test_requirement_trimmed(self):
        """Test that requirement is trimmed."""
        manager = CodeTaskManager()
        task = manager.create_task("  Add TLS transport  ")

        assert task.goal == "Implement: Add TLS transport"
        assert "  Add TLS transport  " not in task.goal

    def test_title_truncation(self):
        """Test that long titles are truncated."""
        manager = CodeTaskManager()
        long_requirement = "A" * 100
        task = manager.create_task(long_requirement)

        assert len(task.title) <= 63  # 60 + "..."
        assert task.title.endswith("...")


class TestCodeTaskManagerFileDetection:
    """Test file detection from requirements."""

    def test_detects_tcp_files(self):
        """Test TCP file detection."""
        manager = CodeTaskManager()
        task = manager.create_task("Add TCP transport support")

        files = task.files_to_modify
        assert any("tcp" in f for f in files)

    def test_detects_tls_files(self):
        """Test TLS file detection."""
        manager = CodeTaskManager()
        task = manager.create_task("Add TLS transport")

        files = task.files_to_modify
        assert any("tls" in f for f in files)

    def test_detects_websocket_files(self):
        """Test WebSocket file detection."""
        manager = CodeTaskManager()
        task = manager.create_task("Implement WebSocket transport")

        files = task.files_to_modify
        assert any("websocket" in f for f in files)

    def test_detects_ssh_files(self):
        """Test SSH file detection."""
        manager = CodeTaskManager()
        task = manager.create_task("Add SSH transport")

        files = task.files_to_modify
        assert any("ssh" in f for f in files)

    def test_detects_config_files(self):
        """Test config file detection."""
        manager = CodeTaskManager()
        task = manager.create_task("Update configuration for TLS")

        files = task.files_to_modify
        assert any("config" in f or ".yaml" in f for f in files)


class TestCodeTaskManagerConstraints:
    """Test constraint generation."""

    def test_length_prefix_for_transport(self):
        """Test length prefix constraint for transport types."""
        manager = CodeTaskManager()
        task = manager.create_task("Add TCP transport")

        assert any("4-byte" in c or "length prefix" in c for c in task.constraints)

    def test_mode_constraint(self):
        """Test client/server mode constraint."""
        manager = CodeTaskManager()
        task = manager.create_task("Add client and server support")

        assert any("mode" in c.lower() for c in task.constraints)

    def test_tls_certificate_constraints(self):
        """Test TLS certificate constraints."""
        manager = CodeTaskManager()
        task = manager.create_task("Add TLS with certificates")

        assert any("certificate" in c.lower() or "certfile" in c for c in task.constraints)

    def test_error_handling_constraint(self):
        """Test error handling constraint."""
        manager = CodeTaskManager()
        task = manager.create_task("Add transport")

        assert any("error" in c.lower() or "timeout" in c.lower() for c in task.constraints)