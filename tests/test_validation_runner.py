"""Tests for ValidationRunner."""

import os
import subprocess
import pytest
from src.llm.validation_runner import ValidationRunner, ValidationResult, DirtyWorktreeError


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

    def test_run_git_status(self, tmp_path):
        """run_git_status should succeed inside a git repo."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True)
        (repo / "dummy.txt").write_text("hello\n")
        subprocess.run(["git", "add", "dummy.txt"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)

        cwd = os.getcwd()
        try:
            os.chdir(str(repo))
            runner = ValidationRunner()
            result = runner.run_git_status()
            assert isinstance(result, ValidationResult)
            assert result.success
        finally:
            os.chdir(cwd)

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

    def test_run_git_apply_check_on_valid_patch(self, tmp_path):
        """git apply --check on a valid patch file should succeed."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, text=True)
        (repo / "README.md").write_text("old\n")
        subprocess.run(["git", "add", "README.md"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)

        patch_path = os.path.join(str(tmp_path), "valid.diff")
        with open(patch_path, "w") as f:
            f.write("diff --git a/README.md b/README.md\n")
            f.write("--- a/README.md\n")
            f.write("+++ b/README.md\n")
            f.write("@@ -1,1 +1,1 @@\n")
            f.write("-old\n")
            f.write("+new\n")

        cwd = os.getcwd()
        try:
            os.chdir(str(repo))
            runner = ValidationRunner()
            result = runner.run_git_apply_check(patch_path)
            assert isinstance(result, ValidationResult)
            assert result.success, f"git apply --check failed: {result.stderr}"
        finally:
            os.chdir(cwd)

    def test_run_git_apply_check_on_malformed_patch(self, tmp_path):
        """git apply --check on a malformed patch file should fail."""
        patch_path = os.path.join(str(tmp_path), "bad.diff")
        with open(patch_path, "w") as f:
            f.write("this is not a valid patch")

        runner = ValidationRunner()
        result = runner.run_git_apply_check(patch_path)
        assert isinstance(result, ValidationResult)
        assert not result.success


class TestGitApply:
    """Tests for run_git_apply in temporary git repositories."""

    def _make_temp_git_repo(self, tmp_path):
        """Create a temporary git repo with a committed file, returns repo path."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True)
        # Create and commit a file
        (repo / "a.py").write_text("old\n")
        subprocess.run(["git", "add", "a.py"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)
        # Create .llm_tasks directory inside the repo
        llm_tasks = repo / ".llm_tasks" / "some_task"
        llm_tasks.mkdir(parents=True)
        return str(repo), str(llm_tasks)

    def test_apply_valid_patch_succeeds(self, tmp_path):
        repo, task_dir = self._make_temp_git_repo(tmp_path)
        patch_path = os.path.join(task_dir, "patch.diff")
        with open(patch_path, "w") as f:
            f.write("diff --git a/a.py b/a.py\n")
            f.write("--- a/a.py\n")
            f.write("+++ b/a.py\n")
            f.write("@@ -1 +1 @@\n")
            f.write("-old\n")
            f.write("+new\n")

        runner = ValidationRunner()
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            result = runner.run_git_apply(patch_path)
        finally:
            os.chdir(cwd)
        assert result.success
        assert "new" in (tmp_path / "repo" / "a.py").read_text()

    def test_apply_malformed_patch_fails(self, tmp_path):
        repo, task_dir = self._make_temp_git_repo(tmp_path)
        patch_path = os.path.join(task_dir, "bad.diff")
        with open(patch_path, "w") as f:
            f.write("this is not a patch")

        runner = ValidationRunner()
        result = runner.run_git_apply(patch_path)
        assert not result.success

    def test_rejects_patch_outside_llm_tasks(self, tmp_path):
        runner = ValidationRunner()
        with pytest.raises(ValueError) as excinfo:
            runner.run_git_apply("/tmp/evil.patch")
        assert ".llm_tasks" in str(excinfo.value)


class TestEnsureCleanWorktree:
    """Tests for ensure_clean_worktree."""

    def _make_temp_git_repo(self, tmp_path):
        """Create a clean temp git repo, returns repo path."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True)
        (repo / "committed.txt").write_text("content\n")
        subprocess.run(["git", "add", "committed.txt"], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)
        return str(repo)

    def test_clean_worktree_does_not_raise(self, tmp_path):
        repo = self._make_temp_git_repo(tmp_path)
        runner = ValidationRunner()
        # Run in the clean repo — should not raise
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            runner.ensure_clean_worktree()
        finally:
            os.chdir(cwd)

    def test_dirty_worktree_raises(self, tmp_path):
        repo = self._make_temp_git_repo(tmp_path)
        # Create an uncommitted file
        (tmp_path / "repo" / "dirty.txt").write_text("dirty\n")

        runner = ValidationRunner()
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            with pytest.raises(DirtyWorktreeError) as excinfo:
                runner.ensure_clean_worktree()
            assert "not clean" in str(excinfo.value).lower()
        finally:
            os.chdir(cwd)

    def test_dirty_worktree_from_modified_file(self, tmp_path):
        repo = self._make_temp_git_repo(tmp_path)
        # Modify a tracked file
        (tmp_path / "repo" / "committed.txt").write_text("modified\n")

        runner = ValidationRunner()
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            with pytest.raises(DirtyWorktreeError):
                runner.ensure_clean_worktree()
        finally:
            os.chdir(cwd)

    def test_dirty_worktree_from_staged_file(self, tmp_path):
        repo = self._make_temp_git_repo(tmp_path)
        (tmp_path / "repo" / "staged.txt").write_text("staged\n")
        subprocess.run(["git", "add", "staged.txt"], cwd=repo, capture_output=True)

        runner = ValidationRunner()
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            with pytest.raises(DirtyWorktreeError):
                runner.ensure_clean_worktree()
        finally:
            os.chdir(cwd)
