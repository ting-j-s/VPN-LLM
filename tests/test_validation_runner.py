"""Tests for ValidationRunner."""

import pytest
from src.llm.validation_runner import ValidationRunner, ValidationResult


class TestValidationResult:
    """Test ValidationResult dataclass."""

    def test_success_when_returncode_zero(self):
        result = ValidationResult(command="echo hi", returncode=0, stdout="hi", stderr="")
        assert result.success is True

    def test_failure_when_returncode_nonzero(self):
        result = ValidationResult(command="false", returncode=1, stdout="", stderr="err")
        assert result.success is False

    def test_failure_when_returncode_negative(self):
        result = ValidationResult(command="timeout", returncode=-1, stdout="", stderr="timeout")
        assert result.success is False


class TestValidationRunner:
    """Test ValidationRunner command execution."""

    def test_run_successful_command(self):
        runner = ValidationRunner()
        result = runner.run_command("echo hello")
        assert isinstance(result, ValidationResult)
        assert result.success
        assert "hello" in result.stdout

    def test_run_failing_command(self):
        runner = ValidationRunner()
        result = runner.run_command("python3 -c 'exit(1)'")
        assert isinstance(result, ValidationResult)
        assert result.success is False
        assert result.returncode == 1

    def test_run_command_with_stderr(self):
        runner = ValidationRunner()
        result = runner.run_command("python3 -c 'import sys; sys.stderr.write(\"errmsg\")'")
        assert isinstance(result, ValidationResult)
        assert "errmsg" in result.stderr
        assert result.success

    def test_run_compileall(self):
        runner = ValidationRunner()
        result = runner.run_compileall()
        assert isinstance(result, ValidationResult)
        # compileall on valid Python files should succeed
        assert result.success

    def test_run_pytest_subprocess(self):
        runner = ValidationRunner()
        # Run a small, non-recursive test file to avoid infinite nesting
        result = runner.run_command("python3 -m pytest tests/test_frame.py -v --tb=short")
        assert isinstance(result, ValidationResult)
        assert result.success, f"Frame tests failed:\n{result.stderr[:500]}"

    def test_run_git_status(self):
        runner = ValidationRunner()
        result = runner.run_git_status()
        assert isinstance(result, ValidationResult)
        assert result.success

    def test_structured_result_fields(self):
        runner = ValidationRunner()
        result = runner.run_command("echo stdout; echo stderr >&2")
        assert hasattr(result, "command")
        assert hasattr(result, "returncode")
        assert hasattr(result, "stdout")
        assert hasattr(result, "stderr")
        assert hasattr(result, "success")
        assert "stdout" in result.stdout
        assert "stderr" in result.stderr
