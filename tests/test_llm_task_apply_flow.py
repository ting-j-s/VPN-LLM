"""Integration tests for the --apply-patch flow in llm_task.py.

These tests verify the CLI-level behavior by calling main() directly
with mocked sys.argv and urllib.request.urlopen. No subprocesses, no real network.
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

    # .gitignore to keep __pycache__ from dirtying the worktree
    (repo / ".gitignore").write_text("__pycache__/\n")

    # Minimal src/ module so compileall doesn't fail with file-not-found
    (repo / "src").mkdir()
    (repo / "src" / "__init__.py").write_text("")
    (repo / "src" / "transport").mkdir(parents=True)
    (repo / "src" / "transport" / "__init__.py").write_text("")
    (repo / "src" / "transport" / "base.py").write_text("class BaseTransport:\n    pass\n")

    # Minimal tests/ with passing tests
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
    """Set up mock HTTP for LLMTaskPlanner and LLMPatchGenerator.

    plan_extra: extra fields to merge into the plan JSON.
    patch_text: edit text for the patch generator response (default: _valid_edits).
    """
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
    """Mock slow ValidationRunner methods to return fake success.

    Only mocks compileall, pytest, and git-status — keeps run_command real
    so that git apply --check and git apply work correctly.
    """
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

    # Mock tunnel smoke to always pass in tests — real server/client won't
    # start in temp git repos without proper config and transport support.
    from src.llm.tunnel_smoke_validator import TunnelSmokeResult, TunnelSmokeValidation
    _fake_tunnel_smoke = TunnelSmokeValidation(
        mock_tun_smoke=TunnelSmokeResult(
            transport="tcp", status="pass", duration_sec=0.01, log_dir="/fake",
        ),
    )
    monkeypatch.setattr(
        "src.llm.tunnel_smoke_validator.run_tunnel_smoke_validation",
        lambda **kwargs: _fake_tunnel_smoke,
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
        # Capture the exit code
        return e.code if isinstance(e.code, int) else 99
    finally:
        os.chdir(cwd)
    return 0


# ---------------------------------------------------------------------------
# Test: CLI argument gating
# ---------------------------------------------------------------------------

class TestCliArgumentGating:
    """Verify --apply-patch and --generate-patch argument requirements."""

    def test_apply_patch_requires_generate_patch(self, tmp_path, monkeypatch):
        """--apply-patch without --generate-patch should error."""
        repo = _make_temp_git_repo(tmp_path)
        _setup_mock_api(monkeypatch)
        _mock_validation_methods(monkeypatch)
        rc = _run_main(monkeypatch, repo, ["--apply-patch"], str(tmp_path / ".llm_tasks"))
        assert rc == 1

    def test_generate_patch_requires_use_llm_planner(self, tmp_path, monkeypatch):
        """--generate-patch without --use-llm-planner should error."""
        repo = _make_temp_git_repo(tmp_path)
        _setup_mock_api(monkeypatch)
        _mock_validation_methods(monkeypatch)
        # Simulate calling without --use-llm-planner
        config_path = os.path.join(_project_root, "config", "llm_agent.yaml.example")
        monkeypatch.setattr(sys, "argv", [
            "llm_task.py",
            "--request", "test",
            "--generate-patch",
            "--record-dir", str(tmp_path / ".llm_tasks"),
        ])
        cwd = os.getcwd()
        from scripts.llm_task import main
        try:
            os.chdir(repo)
            main()
        except SystemExit as e:
            assert e.code == 1
        finally:
            os.chdir(cwd)


# ---------------------------------------------------------------------------
# Test: git apply --check failure blocks apply
# ---------------------------------------------------------------------------

class TestApplyCheckBlocksApply:
    """When --apply-patch is passed but the patch cannot apply cleanly,
    the CLI must refuse to apply it."""

    def test_apply_blocked_when_check_fails(self, tmp_path, monkeypatch):
        """If git apply --check fails, --apply-patch should refuse."""
        repo = _make_temp_git_repo(tmp_path)

        # Produce a diff that references a non-existent file — check will fail
        bad_patch = """FILE: nonexistent.py
<<<FIND
old
<<<REPLACE
new
"""
        _setup_mock_api(monkeypatch, patch_text=bad_patch)
        _mock_validation_methods(monkeypatch)
        rc = _run_main(monkeypatch, repo, ["--generate-patch", "--apply-patch"],
                       str(tmp_path / ".llm_tasks"))
        assert rc == 1


# ---------------------------------------------------------------------------
# Test: dirty worktree blocks apply
# ---------------------------------------------------------------------------

class TestDirtyWorktreeBlock:
    """When the working tree has uncommitted changes, --apply-patch
    should be blocked unless --allow-dirty-worktree is passed."""

    def test_dirty_worktree_blocks_apply(self, tmp_path, monkeypatch):
        """A repo with uncommitted changes should block --apply-patch."""
        repo = _make_temp_git_repo(tmp_path)
        (tmp_path / "repo" / "dirty.txt").write_text("untracked\n")
        _setup_mock_api(monkeypatch)
        _mock_validation_methods(monkeypatch)
        rc = _run_main(monkeypatch, repo, ["--generate-patch", "--apply-patch"],
                       str(tmp_path / ".llm_tasks"))
        assert rc == 1

    def test_allow_dirty_worktree_bypasses_check(self, tmp_path, monkeypatch):
        """--allow-dirty-worktree should skip the clean worktree check."""
        repo = _make_temp_git_repo(tmp_path)
        (tmp_path / "repo" / "dirty.txt").write_text("untracked\n")
        _setup_mock_api(monkeypatch)
        _mock_validation_methods(monkeypatch)
        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch", "--apply-patch", "--allow-dirty-worktree"],
                       str(tmp_path / ".llm_tasks"))
        # With mocked validation methods, the post-apply checks pass
        assert (tmp_path / "repo" / "a.py").read_text() == "old\n"  # a.py is not the edit target
        base_content = (tmp_path / "repo" / "src" / "transport" / "base.py").read_text()
        assert "Base transport" in base_content


# ---------------------------------------------------------------------------
# Test: default behavior (no apply without --apply-patch)
# ---------------------------------------------------------------------------

class TestDefaultNoApply:
    """Verify that without --apply-patch, the patch is never applied."""

    def test_default_does_not_apply_patch(self, tmp_path, monkeypatch):
        """--generate-patch alone should NOT apply the patch."""
        repo = _make_temp_git_repo(tmp_path)
        _setup_mock_api(monkeypatch)
        _mock_validation_methods(monkeypatch)

        assert (tmp_path / "repo" / "a.py").read_text() == "old\n"
        base_orig = (tmp_path / "repo" / "src" / "transport" / "base.py").read_text()

        rc = _run_main(monkeypatch, repo, ["--generate-patch"],
                       str(tmp_path / ".llm_tasks"))
        assert rc == 0

        assert (tmp_path / "repo" / "a.py").read_text() == "old\n"
        # base.py should be unchanged (patch was not applied)
        assert (tmp_path / "repo" / "src" / "transport" / "base.py").read_text() == base_orig

    def test_generate_with_apply_applies_patch(self, tmp_path, monkeypatch):
        """--generate-patch + --apply-patch should apply the patch."""
        repo = _make_temp_git_repo(tmp_path)
        _setup_mock_api(monkeypatch)
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo, ["--generate-patch", "--apply-patch"],
                       str(tmp_path / ".llm_tasks"))
        # With mocked validation, rc should be 0
        assert rc == 0
        base_content = (tmp_path / "repo" / "src" / "transport" / "base.py").read_text()
        assert "Base transport" in base_content

        # Verify apply artifacts exist
        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        apply_result_path = task_dirs[0] / "apply_result.json"
        assert apply_result_path.exists()
        post_apply_path = task_dirs[0] / "post_apply_validation.json"
        assert post_apply_path.exists()

        # Verify apply_result records success
        import json as _json
        apply_data = _json.loads(apply_result_path.read_text())
        assert apply_data["success"] is True

    def test_report_after_apply_contains_guidance(self, tmp_path, monkeypatch):
        """Report after apply should have manual commit guidance, no auto-push."""
        repo = _make_temp_git_repo(tmp_path)
        _setup_mock_api(monkeypatch)
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo, ["--generate-patch", "--apply-patch"],
                       str(tmp_path / ".llm_tasks"))
        assert rc == 0

        # Read the report from disk
        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        report_path = task_dirs[0] / "report.md"
        report = report_path.read_text()

        assert "Patch Application" in report
        assert "Patch applied" in report
        # The report should mention committing changes (either success or failure path)
        assert "commit" in report.lower()
        assert "push" in report.lower()
