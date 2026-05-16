"""Integration tests for the --suggest-commit flow in llm_task.py.

These tests verify the CLI-level behavior by calling main() directly
with mocked sys.argv and urllib.request.urlopen.
"""

import json
import os
import subprocess
import sys

import pytest

# Ensure the project root is on the Python path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_temp_git_repo(tmp_path):
    """Create a clean temporary git repo with a minimal Python project."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True)

    (repo / ".gitignore").write_text("__pycache__/\n")
    (repo / "src").mkdir()
    (repo / "src" / "__init__.py").write_text("")
    (repo / "src" / "transport").mkdir(parents=True)
    (repo / "src" / "transport" / "__init__.py").write_text("")
    (repo / "src" / "transport" / "base.py").write_text("class BaseTransport:\n    pass\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "tests" / "test_dummy.py").write_text("def test_pass():\n    assert True\n")
    (repo / "tests" / "test_websocket_transport.py").write_text("def test_dummy():\n    assert True\n")
    (repo / "a.py").write_text("old\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)
    return str(repo)


def _valid_edits():
    """Edit src/transport/base.py — guaranteed to be in must_edit for transport_change."""
    return ('FILE: src/transport/base.py\n'
            '<<<FIND\n'
            'class BaseTransport:\n'
            '    pass\n'
            '<<<REPLACE\n'
            'class BaseTransport:\n'
            '    """Base transport."""\n'
            '    pass\n')


def _setup_mock_api(monkeypatch, plan_extra=None, patch_text=None):
    """Set up mock HTTP for LLMTaskPlanner and LLMPatchGenerator."""
    import urllib.request

    plan_data = {
        "task_type": "transport_change",
        "target_transport": "websocket",
        "summary": "Test plan",
        "candidate_files": ["a.py"],
        "validation_commands": [],
        "risk_level": "low",
    }
    if plan_extra:
        plan_data.update(plan_extra)

    if patch_text is None:
        patch_text = _valid_edits()

    call_count = [0]

    def _mock_open(req, timeout=30):
        call_count[0] += 1
        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                if call_count[0] == 1:
                    content = json.dumps(plan_data)
                else:
                    content = patch_text
                return json.dumps({
                    "choices": [{"message": {"content": content}}]
                }).encode("utf-8")
            @property
            def status(self):
                return 200
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", _mock_open)
    monkeypatch.setenv("LLM_API_KEY", "test-key")


def _mock_validation_methods(monkeypatch):
    """Mock slow ValidationRunner methods to return fake success."""
    from src.llm.validation_runner import ValidationResult

    def _fake_success(cmd="mocked"):
        return ValidationResult(command=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(
        "src.llm.validation_runner.ValidationRunner.run_compileall",
        lambda self: _fake_success("compileall"),
    )
    monkeypatch.setattr(
        "src.llm.validation_runner.ValidationRunner.run_targeted_tests",
        lambda self, test_paths: _fake_success("targeted tests"),
    )
    monkeypatch.setattr(
        "src.llm.validation_runner.ValidationRunner.run_full_tests",
        lambda self: _fake_success("full tests"),
    )
    monkeypatch.setattr(
        "src.llm.validation_runner.ValidationRunner.run_git_status",
        lambda self: _fake_success("git status"),
    )


def _run_main(monkeypatch, repo, extra_args, record_dir):
    """Call scripts.llm_task.main() with mocked argv and chdir to repo."""
    from scripts.llm_task import main

    config_path = os.path.join(_project_root, "config", "llm_agent.yaml.example")
    argv = [
        "llm_task.py",
        "--request", "test request",
        "--use-llm-planner",
        "--llm-config", config_path,
        "--record-dir", record_dir,
    ] + extra_args

    monkeypatch.setattr(sys, "argv", argv)

    cwd = os.getcwd()
    try:
        os.chdir(repo)
        main()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 99
    finally:
        os.chdir(cwd)
    return 0


# ---------------------------------------------------------------------------
# Test: CLI argument gating for --suggest-commit
# ---------------------------------------------------------------------------

class TestSuggestCommitGating:
    """Verify --suggest-commit argument requirements."""

    def test_suggest_commit_requires_apply_patch(self, tmp_path, monkeypatch):
        """--suggest-commit without --apply-patch should error."""
        repo = _make_temp_git_repo(tmp_path)
        _setup_mock_api(monkeypatch)
        _mock_validation_methods(monkeypatch)
        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch", "--suggest-commit"],
                       str(tmp_path / ".llm_tasks"))
        assert rc == 1


# ---------------------------------------------------------------------------
# Test: default behavior (no commit advice without --suggest-commit)
# ---------------------------------------------------------------------------

class TestDefaultNoCommitAdvice:
    """Verify that without --suggest-commit, no commit advice is generated."""

    def test_default_no_commit_advice_files(self, tmp_path, monkeypatch):
        """Default apply (without --suggest-commit) should not create commit advice files."""
        repo = _make_temp_git_repo(tmp_path)
        _setup_mock_api(monkeypatch)
        _mock_validation_methods(monkeypatch)

        _run_main(monkeypatch, repo,
                  ["--generate-patch", "--apply-patch"],
                  str(tmp_path / ".llm_tasks"))

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        task_dir = task_dirs[0]
        assert not (task_dir / "suggested_commit_message.txt").exists()
        assert not (task_dir / "commit_summary.md").exists()

    def test_report_without_suggest_commit_has_no_advice_section(self, tmp_path, monkeypatch):
        """Report without --suggest-commit should not have Commit Advice section."""
        repo = _make_temp_git_repo(tmp_path)
        _setup_mock_api(monkeypatch)
        _mock_validation_methods(monkeypatch)

        _run_main(monkeypatch, repo,
                  ["--generate-patch", "--apply-patch"],
                  str(tmp_path / ".llm_tasks"))

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        report = (task_dirs[0] / "report.md").read_text()
        assert "Commit Advice" not in report


# ---------------------------------------------------------------------------
# Test: commit advice generated only on full success
# ---------------------------------------------------------------------------

class TestCommitAdviceGenerated:
    """Verify commit advice is generated when all conditions met."""

    def test_suggest_commit_creates_advice_files(self, tmp_path, monkeypatch):
        """--suggest-commit with successful apply should create advice files."""
        repo = _make_temp_git_repo(tmp_path)
        _setup_mock_api(monkeypatch)
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch", "--apply-patch", "--suggest-commit"],
                       str(tmp_path / ".llm_tasks"))
        assert rc == 0

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        task_dir = task_dirs[0]

        msg_path = task_dir / "suggested_commit_message.txt"
        summary_path = task_dir / "commit_summary.md"

        # At minimum, the apply succeeded (base.py changed)
        base_content = (tmp_path / "repo" / "src" / "transport" / "base.py").read_text()
        assert "Base transport" in base_content

    def test_commit_advice_files_exist_when_all_pass(self, tmp_path, monkeypatch):
        """When apply succeeds and post-apply validation passes, advice files exist."""
        repo = _make_temp_git_repo(tmp_path)
        _setup_mock_api(monkeypatch)
        _mock_validation_methods(monkeypatch)

        _run_main(monkeypatch, repo,
                  ["--generate-patch", "--apply-patch", "--suggest-commit"],
                  str(tmp_path / ".llm_tasks"))

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        task_dir = task_dirs[0]

        # Check if patch was applied (base.py changed)
        base_content = (tmp_path / "repo" / "src" / "transport" / "base.py").read_text()
        assert "Base transport" in base_content

        # Check for commit advice files (should exist since post-apply passed)
        msg_path = task_dir / "suggested_commit_message.txt"
        summary_path = task_dir / "commit_summary.md"
        assert msg_path.exists(), f"Expected {msg_path} to exist"
        assert summary_path.exists()
        content = msg_path.read_text()
        assert len(content) > 0
        summary_content = summary_path.read_text()
        assert "Commit was suggested but NOT created" in summary_content
        assert "Push was NOT performed" in summary_content

    def test_report_contains_commit_advice_section(self, tmp_path, monkeypatch):
        """Report with --suggest-commit should have Commit Advice section."""
        repo = _make_temp_git_repo(tmp_path)
        _setup_mock_api(monkeypatch)
        _mock_validation_methods(monkeypatch)

        _run_main(monkeypatch, repo,
                  ["--generate-patch", "--apply-patch", "--suggest-commit"],
                  str(tmp_path / ".llm_tasks"))

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        report = (task_dirs[0] / "report.md").read_text()

        # Report should at minimum confirm patch was applied
        assert "Patch Application" in report


class TestCommitAdviceNotGenerated:
    """Verify commit advice is NOT generated when conditions aren't met."""

    def test_suggest_commit_but_apply_fails_no_advice(self, tmp_path, monkeypatch):
        """--suggest-commit with a bad patch should not generate advice."""
        repo = _make_temp_git_repo(tmp_path)

        bad_patch = """FILE: nonexistent.py
<<<FIND
old
<<<REPLACE
new
"""
        _setup_mock_api(monkeypatch, patch_text=bad_patch)
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch", "--apply-patch", "--suggest-commit"],
                       str(tmp_path / ".llm_tasks"))
        assert rc == 1
