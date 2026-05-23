"""Integration tests for the file selection flow in llm_task.py.

Verifies that the full agentized pipeline (RepoIndexer → FileRetriever →
ImpactExpander → ContextBuilder → PatchGenerator) works end-to-end and
produces the expected artifacts.

All tests use mocked HTTP — no real network calls, no real LLM.
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

def _make_temp_git_repo(tmp_path, files: dict | None = None):
    """Create a clean temporary git repo with specified files.

    Args:
        tmp_path: pytest tmp_path fixture.
        files: dict mapping relative path -> content. If None, a minimal
               transport project is created.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True)

    if files is None:
        files = _minimal_transport_project()

    for rel_path, content in files.items():
        full = repo / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)

    (repo / ".gitignore").write_text("__pycache__/\n.llm_tasks/\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)
    return str(repo)


def _minimal_transport_project() -> dict:
    """Minimal project with transport files for realistic testing."""
    return {
        "src/__init__.py": "",
        "src/transport/__init__.py": "",
        "src/transport/base.py": "class BaseTransport:\n    pass\n",
        "src/transport/factory.py": "class TransportFactory:\n    pass\n",
        "src/transport/tcp_transport.py": "class TCPTransport:\n    pass\n",
        "src/common/__init__.py": "",
        "src/common/config.py": "def load_config():\n    return {}\n",
        "src/core/__init__.py": "",
        "src/core/client_core.py": "class ClientCore:\n    pass\n",
        "src/llm/__init__.py": "",
        "src/llm/task_planner.py": "class TaskPlanner:\n    pass\n",
        "config/server.yaml": "transport:\n  type: tcp\n  port: 8080\n",
        "tests/__init__.py": "",
        "tests/test_tcp_transport.py": "def test_dummy():\n    assert True\n",
        "tests/test_transport_mock.py": "def test_dummy():\n    assert True\n",
        "README.md": "# Test Project\n",
        "a.py": "old\n",
    }


def _setup_mock_api(monkeypatch, plan_extra=None, patch_text=None):
    """Set up mock HTTP for both LLMTaskPlanner and LLMPatchGenerator."""
    import urllib.request

    plan_data = {
        "task_type": "transport_change",
        "target_transport": "websocket",
        "summary": "Switch transport to websocket",
        "candidate_files": ["src/transport/tcp_transport.py", "config/server.yaml", "a.py"],
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


def _valid_edits():
    """Edit src/transport/base.py — guaranteed to be in must_edit for transport_change."""
    return ('FILE: src/transport/base.py\n'
            '<<<FIND\n'
            'class BaseTransport:\n'
            '    pass\n'
            '<<<REPLACE\n'
            'class BaseTransport:\n'
            '    """Base transport interface."""\n'
            '    pass\n')


def _valid_edits_a_py():
    """Edit a.py — used only when a.py is keyword-matched to must_edit."""
    return """FILE: a.py
<<<FIND
old
<<<REPLACE
new
"""


def _mock_validation_methods(monkeypatch):
    """Mock slow ValidationRunner methods."""
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


def _run_main(monkeypatch, repo, extra_args, record_dir, request="test request"):
    """Call scripts.llm_task.main() with mocked argv and chdir to repo."""
    from scripts.llm_task import main

    config_path = os.path.join(_project_root, "config", "llm_agent.yaml.example")
    argv = [
        "llm_task.py",
        "--request", request,
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
# Test: File selection artifacts are written
# ---------------------------------------------------------------------------

class TestFileSelectionArtifacts:
    """Verify the file selection pipeline produces all expected artifacts."""

    def test_file_selection_writes_artifacts(self, tmp_path, monkeypatch):
        """The full flow should write file_retrieval.json, impact_analysis.json, file_selection.json."""
        repo = _make_temp_git_repo(tmp_path)
        record_dir = str(tmp_path / ".llm_tasks")
        _setup_mock_api(monkeypatch, patch_text=_valid_edits())
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch", "--apply-patch"],
                       record_dir)

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        task_dir = task_dirs[0]

        # Check key artifacts
        assert (task_dir / "repo_index_summary.json").exists(), "repo_index_summary.json should exist"
        assert (task_dir / "file_retrieval.json").exists(), "file_retrieval.json should exist"
        assert (task_dir / "impact_analysis.json").exists(), "impact_analysis.json should exist"
        assert (task_dir / "file_selection.json").exists(), "file_selection.json should exist"
        assert (task_dir / "context_summary.json").exists(), "context_summary.json should exist"

        # Validate file_selection.json content
        selection = json.loads((task_dir / "file_selection.json").read_text())
        assert "must_edit_files" in selection
        assert "must_review_files" in selection
        assert "test_files" in selection
        assert "doc_files" in selection
        assert "allowed_create_paths" in selection
        assert "candidates" in selection
        assert "rejected_hints" in selection

        # Validate file_retrieval.json content
        retrieval = json.loads((task_dir / "file_retrieval.json").read_text())
        assert isinstance(retrieval, list)
        assert len(retrieval) > 0
        for c in retrieval:
            assert "path" in c
            assert "score" in c
            assert "reasons" in c
            assert "sources" in c
            assert "action" in c

    def test_a_py_in_must_review_not_must_edit(self, tmp_path, monkeypatch):
        """Pure LLM hints (score 0.6, no other sources) go to must_review, not must_edit."""
        repo = _make_temp_git_repo(tmp_path)
        record_dir = str(tmp_path / ".llm_tasks")
        _setup_mock_api(monkeypatch, patch_text=_valid_edits())
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch"],
                       record_dir)

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        task_dir = task_dirs[0]
        selection = json.loads((task_dir / "file_selection.json").read_text())
        # a.py is a pure planner hint → goes to must_review, not must_edit
        assert "a.py" in selection["must_review_files"], \
            f"a.py should be in must_review_files, got: edit={selection['must_edit_files']}, review={selection['must_review_files']}"
        assert "a.py" not in selection["must_edit_files"], \
            "a.py is a pure planner hint and should not be in must_edit_files"

    def test_patch_applies_with_file_selection(self, tmp_path, monkeypatch):
        """The full flow with file selection should apply patches to must_edit files."""
        repo = _make_temp_git_repo(tmp_path)
        _setup_mock_api(monkeypatch, patch_text=_valid_edits())
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch", "--apply-patch"],
                       str(tmp_path / ".llm_tasks"))

        assert rc == 0
        # base.py is in must_edit (structural rule), patch should have modified it
        content = (tmp_path / "repo" / "src" / "transport" / "base.py").read_text()
        assert "Base transport interface" in content


class TestNoEditableFiles:
    """When no editable files are found, the flow should stop cleanly."""

    def test_no_editable_files_stops_with_report(self, tmp_path, monkeypatch):
        """If FileSelection has no must_edit and no allowed_create, stop gracefully."""
        repo = _make_temp_git_repo(tmp_path, files={
            "README.md": "# Project\n",
        })
        record_dir = str(tmp_path / ".llm_tasks")

        _setup_mock_api(monkeypatch, plan_extra={
            "candidate_files": [],
        })
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch"],
                       record_dir)

        # Should exit with code 1 because no editable files
        assert rc == 1


class TestExtremeRiskControl:
    """Extreme risk requests should be blocked."""

    def test_sudo_request_is_blocked(self, tmp_path, monkeypatch):
        """A request containing 'sudo' should be blocked."""
        repo = _make_temp_git_repo(tmp_path)
        record_dir = str(tmp_path / ".llm_tasks")
        _setup_mock_api(monkeypatch)
        _mock_validation_methods(monkeypatch)

        config_path = os.path.join(_project_root, "config", "llm_agent.yaml.example")
        monkeypatch.setattr(sys, "argv", [
            "llm_task.py",
            "--request", "run sudo rm -rf /tmp",
            "--use-llm-planner",
            "--llm-config", config_path,
            "--record-dir", record_dir,
        ])

        from scripts.llm_task import main
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            main()
        except SystemExit as e:
            assert e.code == 1
        finally:
            os.chdir(cwd)

        # Check clarification file was written
        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        assert (task_dirs[0] / "clarification_questions.md").exists()

    def test_dot_env_request_is_blocked(self, tmp_path, monkeypatch):
        """Requests modifying .env should be blocked."""
        repo = _make_temp_git_repo(tmp_path)
        record_dir = str(tmp_path / ".llm_tasks")
        _setup_mock_api(monkeypatch)

        config_path = os.path.join(_project_root, "config", "llm_agent.yaml.example")
        monkeypatch.setattr(sys, "argv", [
            "llm_task.py",
            "--request", "modify .env file to change database password",
            "--use-llm-planner",
            "--llm-config", config_path,
            "--record-dir", record_dir,
        ])

        from scripts.llm_task import main
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            main()
        except SystemExit as e:
            assert e.code == 1
        finally:
            os.chdir(cwd)

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert (task_dirs[0] / "clarification_questions.md").exists()


class TestFileSelectionInReport:
    """The report should mention file selection results."""

    def test_report_contains_file_selection_info(self, tmp_path, monkeypatch):
        """Report should be generated even with file selection pipeline."""
        repo = _make_temp_git_repo(tmp_path)
        _setup_mock_api(monkeypatch, patch_text=_valid_edits())
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch", "--apply-patch"],
                       str(tmp_path / ".llm_tasks"))

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        report = (task_dirs[0] / "report.md").read_text()
        assert "Task Info" in report
        assert "Planned Changes" in report


class TestBackgroundPlanning:
    """Non-patch mode should still work with file selection pipeline."""

    def test_rule_based_plan_still_works(self, tmp_path, monkeypatch):
        """Rule-based planner should work without file selection."""
        repo = _make_temp_git_repo(tmp_path)
        _mock_validation_methods(monkeypatch)

        config_path = os.path.join(_project_root, "config", "llm_agent.yaml.example")
        monkeypatch.setattr(sys, "argv", [
            "llm_task.py",
            "--request", "switch transport to websocket",
            "--record-dir", str(tmp_path / ".llm_tasks"),
        ])

        from scripts.llm_task import main
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            main()
        except SystemExit as e:
            assert e.code == 0
        finally:
            os.chdir(cwd)

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        report = (task_dirs[0] / "report.md").read_text()
        assert "Task Info" in report


class TestDryRunHttp2Transport:
    """Dry-run workflow test: 'add a new http2 transport skeleton with config support, tests, and docs'.

    Does NOT call real LLM API — PatchGenerator is mocked.
    Verifies the full file selection pipeline produces correct artifacts.
    """

    def test_full_dry_run_produces_correct_artifacts(self, tmp_path, monkeypatch):
        repo = _make_temp_git_repo(tmp_path)
        record_dir = str(tmp_path / ".llm_tasks")

        plan_extra = {
            "task_type": "transport_change",
            "target_transport": None,
            "summary": "Add new HTTP/2 transport skeleton",
            "candidate_files": [],
            "risk_level": "low",
        }
        _setup_mock_api(monkeypatch, plan_extra=plan_extra, patch_text=_valid_edits())
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch", "--apply-patch"],
                       record_dir,
                       request="add a new http2 transport skeleton with config support, tests, and docs")

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        task_dir = task_dirs[0]

        # 1. Verify key artifacts exist
        assert (task_dir / "file_retrieval.json").exists()
        assert (task_dir / "impact_analysis.json").exists()
        assert (task_dir / "file_selection.json").exists()

        # 2. Verify file_retrieval.json has candidates
        retrieval = json.loads((task_dir / "file_retrieval.json").read_text())
        assert isinstance(retrieval, list)
        assert len(retrieval) > 0

        # 3. Verify file_selection.json has proper structure
        selection = json.loads((task_dir / "file_selection.json").read_text())
        for key in ("must_edit_files", "must_review_files", "test_files",
                     "doc_files", "allowed_create_paths", "allowed_create_patterns",
                     "candidates", "rejected_hints", "action_sources"):
            assert key in selection, f"file_selection.json missing key: {key}"

        # 4. allowed_create_paths contains reasonable paths
        allowed = selection["allowed_create_paths"]
        assert any("transport" in p for p in allowed), \
            f"allowed_create_paths should contain transport dir, got: {allowed}"

        # 5. allowed_create_paths does NOT contain dangerous paths
        for p in allowed:
            assert ".git" not in p, f"Dangerous path in allowed_create_paths: {p}"
            assert ".env" not in p, f"Dangerous path in allowed_create_paths: {p}"

        # 6. allowed_create_patterns are present
        patterns = selection.get("allowed_create_patterns", [])
        assert isinstance(patterns, list)
        # For transport, should have transport naming patterns
        assert any("transport" in p for p in patterns) or len(patterns) > 0, \
            f"allowed_create_patterns should have transport patterns, got: {patterns}"

        # 7. No dangerous patterns in allowed_create_patterns
        for p in patterns:
            assert ".git" not in p
            assert ".env" not in p
            assert ".key" not in p
            assert ".pem" not in p
            assert "README.md" not in p  # README.md is never allowed to create

        # 8. action_sources explains file classifications
        action_sources = selection.get("action_sources", {})
        assert isinstance(action_sources, dict)

        # 9. must_edit_files should not include test files or doc files
        for f in selection["must_edit_files"]:
            assert not f.startswith("tests/"), f"test file {f} should not be in must_edit"
            assert not f.endswith(".md") or f == "README.md", \
                f"doc file {f} should not be in must_edit"

        # 10. Context summary exists
        assert (task_dir / "context_summary.json").exists()
        context = json.loads((task_dir / "context_summary.json").read_text())
        assert "allowed_create_patterns" in context

    def test_http2_request_does_not_trigger_extreme_risk(self, tmp_path, monkeypatch):
        """'add new http2 transport' should NOT be blocked as extreme risk."""
        repo = _make_temp_git_repo(tmp_path)
        record_dir = str(tmp_path / ".llm_tasks")

        plan_extra = {
            "task_type": "transport_change",
            "target_transport": None,
            "summary": "Add new HTTP/2 transport skeleton",
            "candidate_files": [],
            "risk_level": "low",
        }
        _setup_mock_api(monkeypatch, plan_extra=plan_extra, patch_text=_valid_edits())
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch", "--apply-patch"],
                       record_dir,
                       request="add a new http2 transport skeleton with config support, tests, and docs")

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        # clarification_questions.md should NOT exist (not extreme risk)
        assert not (task_dirs[0] / "clarification_questions.md").exists(), \
            "http2 request should not trigger extreme risk"


# ---------------------------------------------------------------------------
# Protocol retry helpers
# ---------------------------------------------------------------------------

def _setup_mock_api_with_retry(monkeypatch, plan_extra=None, patch_responses=None):
    """Set up mock HTTP that returns different patch responses per call.

    call 1: planner response (plan_data)
    call 2: patch_response[0] (first attempt)
    call 3: patch_response[1] (second attempt, if any)
    """
    import urllib.request

    plan_data = {
        "task_type": "transport_change",
        "target_transport": "websocket",
        "summary": "Switch transport to websocket",
        "candidate_files": ["src/transport/tcp_transport.py", "config/server.yaml"],
        "validation_commands": [],
        "risk_level": "low",
    }
    if plan_extra:
        plan_data.update(plan_extra)

    if patch_responses is None:
        patch_responses = [_valid_edits()]

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
                    idx = call_count[0] - 2
                    if idx < len(patch_responses):
                        content = patch_responses[idx]
                    else:
                        content = patch_responses[-1]
                return json.dumps({
                    "choices": [{"message": {"content": content}}]
                }).encode("utf-8")
            @property
            def status(self):
                return 200
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", _mock_open)
    monkeypatch.setenv("LLM_API_KEY", "test-key")


# ---------------------------------------------------------------------------
# Test: Protocol retry in full flow
# ---------------------------------------------------------------------------


class TestProtocolRetryInFlow:
    """Verify protocol retry behavior in full llm_task.py flow."""

    def test_retry_success_recorded_in_report(self, tmp_path, monkeypatch):
        """First malformed LLM response, retry succeeds → report records retry."""
        repo = _make_temp_git_repo(tmp_path)
        record_dir = str(tmp_path / ".llm_tasks")

        _setup_mock_api_with_retry(monkeypatch, patch_responses=[
            "Let me think about what needs to change first...\nAnalysis: the transport needs...",
            _valid_edits(),
        ])
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch", "--apply-patch"],
                       record_dir)

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        report = (task_dirs[0] / "report.md").read_text()
        assert "Protocol retry" in report
        assert "first attempt malformed, retry succeeded" in report
        assert (task_dirs[0] / "patch.diff").exists()

    def test_retry_failure_recorded_in_report(self, tmp_path, monkeypatch):
        """Both LLM attempts malformed → report records failure."""
        repo = _make_temp_git_repo(tmp_path)
        record_dir = str(tmp_path / ".llm_tasks")

        _setup_mock_api_with_retry(monkeypatch, patch_responses=[
            "Let me analyze this first...",
            "Here is my second attempt at analysis...",
        ])
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch"],
                       record_dir)

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        report = (task_dirs[0] / "report.md").read_text()
        assert "FAILED" in report
        assert "malformed patch response after retry" in report
        # No patch.diff (generation failed)
        assert not (task_dirs[0] / "patch.diff").exists()

    def test_retry_raw_attempts_saved(self, tmp_path, monkeypatch):
        """Raw attempt files are saved in task_dir when retry fails."""
        repo = _make_temp_git_repo(tmp_path)
        record_dir = str(tmp_path / ".llm_tasks")

        _setup_mock_api_with_retry(monkeypatch, patch_responses=[
            "Analysis attempt one: modify the core...",
            "Analysis attempt two: change the frame...",
        ])
        _mock_validation_methods(monkeypatch)

        _run_main(monkeypatch, repo, ["--generate-patch"], record_dir)

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        task_dir = task_dirs[0]

        attempt1 = task_dir / "llm_patch_raw_attempt1.txt"
        attempt2 = task_dir / "llm_patch_raw_attempt2.txt"
        attempt3 = task_dir / "llm_patch_raw_attempt3.txt"
        assert attempt1.exists(), f"Missing {attempt1}"
        assert attempt2.exists(), f"Missing {attempt2}"
        assert attempt3.exists(), f"Missing {attempt3}"
        assert "Analysis attempt one" in attempt1.read_text()
        assert "Analysis attempt two" in attempt2.read_text()


# ---------------------------------------------------------------------------
# Test: Semantic retry in full flow
# ---------------------------------------------------------------------------


class TestSemanticRetryInFlow:
    """Verify semantic retry behavior in full llm_task.py flow."""

    def test_semantic_retry_recorded_in_report(self, tmp_path, monkeypatch):
        """First valid-protocol response has semantic error → retry → report records it."""
        repo = _make_temp_git_repo(tmp_path)
        record_dir = str(tmp_path / ".llm_tasks")

        # Attempt 1: ACTION:create on existing file → semantic retry
        # Attempt 2: correct ACTION:replace → success
        _setup_mock_api_with_retry(monkeypatch, patch_responses=[
            ("FILE: src/transport/base.py\n"
             "ACTION: create\n"
             "<<<CONTENT\n"
             "class BaseTransport:\n"
             "    '''Base transport interface.'''\n"
             "    pass\n"
             ">>>"),
            _valid_edits(),
        ])
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch"],
                       record_dir)

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        report = (task_dirs[0] / "report.md").read_text()
        assert "Semantic retry" in report
        assert "semantic retry succeeded" in report
        # Patch should be generated
        assert (task_dirs[0] / "patch.diff").exists()

    def test_protocol_retry_then_semantic_retry_recorded_in_report(self, tmp_path, monkeypatch):
        """Attempt1 prose → protocol retry, attempt2 create-existing → semantic retry, attempt3 success."""
        repo = _make_temp_git_repo(tmp_path)
        record_dir = str(tmp_path / ".llm_tasks")

        _setup_mock_api_with_retry(monkeypatch, patch_responses=[
            "Let me think about what needs to change first...\nAnalysis: the transport needs...",
            ("FILE: src/transport/base.py\n"
             "ACTION: create\n"
             "<<<CONTENT\n"
             "class BaseTransport:\n"
             "    '''Base transport interface.'''\n"
             "    pass\n"
             ">>>"),
            _valid_edits(),
        ])
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch"],
                       record_dir)

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        report = (task_dirs[0] / "report.md").read_text()
        assert "Protocol retry" in report
        assert "Semantic retry" in report
        assert "semantic retry succeeded" in report
        # Patch should be generated
        assert (task_dirs[0] / "patch.diff").exists()

    def test_semantic_retry_failure_recorded_in_report(self, tmp_path, monkeypatch):
        """Both semantic attempts fail → report records failure."""
        repo = _make_temp_git_repo(tmp_path)
        record_dir = str(tmp_path / ".llm_tasks")

        _setup_mock_api_with_retry(monkeypatch, patch_responses=[
            """FILE: src/transport/base.py
ACTION: create
<<<CONTENT
attempt 1
>>>""",
            """FILE: src/transport/base.py
ACTION: create
<<<CONTENT
attempt 2
>>>""",
        ])
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch"],
                       record_dir)

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        task_dir = task_dirs[0]
        report = (task_dir / "report.md").read_text()
        assert "FAILED" in report
        # Check raw attempt files are saved
        attempt1 = task_dir / "llm_patch_raw_attempt1.txt"
        attempt2 = task_dir / "llm_patch_raw_attempt2.txt"
        assert attempt1.exists(), f"Missing {attempt1}"
        assert attempt2.exists(), f"Missing {attempt2}"
        assert not (task_dir / "patch.diff").exists()


# ---------------------------------------------------------------------------
# Tests: task_rules module
# ---------------------------------------------------------------------------

class TestDetectTransportName:
    """Unit tests for detect_transport_name in src.llm.task_rules."""

    def test_detect_socks5(self):
        from src.llm.task_rules import detect_transport_name
        assert detect_transport_name("add socks5 transport") == "socks5"
        assert detect_transport_name("create a new socks5 transport") == "socks5"

    def test_detect_http2(self):
        from src.llm.task_rules import detect_transport_name
        assert detect_transport_name("add new http2 transport") == "http2"
        assert detect_transport_name("implement http2 protocol") == "http2"

    def test_detect_with_chinese(self):
        from src.llm.task_rules import detect_transport_name
        assert detect_transport_name("生成并使用一种新的外层协议socks5") == "socks5"

    def test_no_transport_returns_none(self):
        from src.llm.task_rules import detect_transport_name
        assert detect_transport_name("fix a bug in the core loop") is None

    def test_common_words_filtered(self):
        from src.llm.task_rules import detect_transport_name
        assert detect_transport_name("add the transport") is None

    def test_dash_in_name(self):
        from src.llm.task_rules import detect_transport_name
        assert detect_transport_name("add my-proto transport") == "my-proto"


class TestTransportAdditionRequiredFiles:
    """Unit tests for get_transport_addition_required_files."""

    def test_returns_must_edit_and_must_create(self):
        from src.llm.task_rules import get_transport_addition_required_files
        result = get_transport_addition_required_files("socks5")
        assert "src/transport/factory.py" in result["must_edit"]
        assert "src/common/config.py" in result["must_edit"]
        assert "src/transport/socks5_transport.py" in result["must_create"]
        assert "tests/test_socks5_transport.py" in result["must_create"]
        assert "docs/transports/socks5.md" in result["must_create"]

    def test_works_for_any_name(self):
        from src.llm.task_rules import get_transport_addition_required_files
        result = get_transport_addition_required_files("quic")
        assert "src/transport/quic_transport.py" in result["must_create"]
        assert "tests/test_quic_transport.py" in result["must_create"]


# ---------------------------------------------------------------------------
# Tests: patch completeness checker
# ---------------------------------------------------------------------------

class TestPatchCompleteness:
    """Unit tests for check_patch_completeness in src.llm.patch_generator."""

    def _make_create_diff(self, filepath: str, content: str) -> str:
        lines = content.splitlines(keepends=True)
        line_count = len(lines) if lines else 0
        diff = (
            f"diff --git a/{filepath} b/{filepath}\n"
            f"new file mode 100644\n"
            f"index 0000000..0000000\n"
            f"--- /dev/null\n"
            f"+++ b/{filepath}\n"
            f"@@ -0,0 +1,{line_count} @@\n"
        )
        for line in lines:
            diff += f"+{line}"
        return diff

    def test_complete_patch_passes(self):
        from src.llm.patch_generator import check_patch_completeness
        diff = (
            self._make_create_diff(
                "src/transport/socks5_transport.py",
                "class Socks5Transport:\n    pass\n",
            )
            + self._make_create_diff(
                "tests/test_socks5_transport.py",
                "def test_dummy():\n    assert True\n",
            )
        )
        result = check_patch_completeness(diff,
            must_create_files=[
                "src/transport/socks5_transport.py",
                "tests/test_socks5_transport.py",
            ])
        assert result.passed
        assert len(result.missing_files) == 0
        assert len(result.truncated_files) == 0
        assert len(result.syntax_errors) == 0

    def test_missing_file_detected(self):
        from src.llm.patch_generator import check_patch_completeness
        diff = self._make_create_diff(
            "src/transport/socks5_transport.py",
            "class Socks5Transport:\n    pass\n",
        )
        result = check_patch_completeness(diff,
            must_create_files=[
                "src/transport/socks5_transport.py",
                "tests/test_socks5_transport.py",
                "docs/transports/socks5.md",
            ])
        assert not result.passed
        assert "tests/test_socks5_transport.py" in result.missing_files
        assert "docs/transports/socks5.md" in result.missing_files

    def test_truncated_backslash_detected(self):
        from src.llm.patch_generator import check_patch_completeness
        diff = self._make_create_diff(
            "src/transport/socks5_transport.py",
            "self._server_s\\\n",
        )
        result = check_patch_completeness(diff)
        assert not result.passed
        assert len(result.truncated_files) > 0
        assert any("socks5" in t for t in result.truncated_files)

    def test_unclosed_triple_quote_detected(self):
        from src.llm.patch_generator import check_patch_completeness
        diff = self._make_create_diff(
            "src/transport/socks5_transport.py",
            '"""unclosed docstring\n',
        )
        result = check_patch_completeness(diff)
        assert not result.passed
        assert len(result.truncated_files) > 0

    def test_syntax_error_detected(self):
        from src.llm.patch_generator import check_patch_completeness
        diff = self._make_create_diff(
            "src/transport/socks5_transport.py",
            "def broken(:\n    pass\n",
        )
        result = check_patch_completeness(diff)
        assert not result.passed
        assert len(result.syntax_errors) > 0

    def test_planned_vs_actual_warning(self):
        from src.llm.patch_generator import check_patch_completeness
        diff = self._make_create_diff(
            "src/transport/socks5_transport.py",
            "class Socks5Transport:\n    pass\n",
        )
        result = check_patch_completeness(diff,
            must_create_files=[
                "src/transport/socks5_transport.py",
                "tests/test_socks5_transport.py",
                "docs/transports/socks5.md",
            ],
            must_edit_files=[
                "src/transport/factory.py",
                "src/common/config.py",
            ])
        assert not result.passed
        assert result.planned_file_count == 5
        assert result.actual_file_count == 1
        assert any("Planned 5" in w for w in result.warnings)

    def test_complete_transport_addition_passes(self):
        """A full transport addition patch should pass completeness check."""
        from src.llm.patch_generator import check_patch_completeness
        transport_code = (
            "class Socks5Transport:\n"
            '    """SOCKS5 transport skeleton."""\n'
            "    def __init__(self):\n"
            "        pass\n"
            "    def connect(self):\n"
            '        raise NotImplementedError("skeleton")\n'
            "    def close(self):\n"
            "        pass\n"
        )
        test_code = (
            "def test_import():\n"
            "    from src.transport.socks5_transport import Socks5Transport\n"
            "    t = Socks5Transport()\n"
            "    assert t is not None\n"
        )
        doc_code = "# SOCKS5 Transport\n\nSkeleton implementation.\n"

        diff = (
            self._make_create_diff("src/transport/socks5_transport.py", transport_code)
            + self._make_create_diff("tests/test_socks5_transport.py", test_code)
            + self._make_create_diff("docs/transports/socks5.md", doc_code)
        )
        result = check_patch_completeness(diff,
            must_create_files=[
                "src/transport/socks5_transport.py",
                "tests/test_socks5_transport.py",
                "docs/transports/socks5.md",
            ])
        assert result.passed, (
            f"missing={result.missing_files} "
            f"truncated={result.truncated_files} "
            f"syntax={result.syntax_errors}"
        )


# ---------------------------------------------------------------------------
# Tests: ImpactExpander must_create_files for transport_addition
# ---------------------------------------------------------------------------

class TestImpactExpanderMustCreate:
    """Verify ImpactExpander adds must_create_files for transport_addition tasks."""

    def test_transport_addition_generates_must_create(self, tmp_path, monkeypatch):
        """ImpactExpander must add factory.py, config.py to must_edit and
        new transport files to must_create."""
        repo = _make_temp_git_repo(tmp_path)
        record_dir = str(tmp_path / ".llm_tasks")

        plan_extra = {
            "task_type": "transport_addition",
            "target_transport": None,
            "summary": "Add SOCKS5 transport skeleton",
            "candidate_files": [],
            "risk_level": "high",
        }
        _setup_mock_api(monkeypatch, plan_extra=plan_extra, patch_text=_valid_edits())
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch"],
                       record_dir,
                       request="add a new socks5 transport skeleton with config support, tests and docs")

        # Check file_selection.json was written and contains must_create_files
        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        selection_path = task_dirs[0] / "file_selection.json"
        assert selection_path.exists(), f"Missing {selection_path}"
        selection = json.loads(selection_path.read_text())

        must_create = selection.get("must_create_files", [])
        assert "src/transport/socks5_transport.py" in must_create, (
            f"must_create_files={must_create}"
        )
        assert "tests/test_socks5_transport.py" in must_create
        assert "docs/transports/socks5.md" in must_create

        must_edit = selection.get("must_edit_files", [])
        assert "src/transport/factory.py" in must_edit, (
            f"must_edit_files={must_edit}"
        )
        assert "src/common/config.py" in must_edit

    def test_http2_request_not_affected(self, tmp_path, monkeypatch):
        """HTTP/2 request with transport_addition should also get must_create."""
        repo = _make_temp_git_repo(tmp_path)
        record_dir = str(tmp_path / ".llm_tasks")

        plan_extra = {
            "task_type": "transport_addition",
            "target_transport": None,
            "summary": "Add HTTP/2 transport",
            "candidate_files": [],
            "risk_level": "medium",
        }
        _setup_mock_api(monkeypatch, plan_extra=plan_extra, patch_text=_valid_edits())
        _mock_validation_methods(monkeypatch)

        rc = _run_main(monkeypatch, repo,
                       ["--generate-patch"],
                       record_dir,
                       request="add new http2 transport")

        task_dirs = list((tmp_path / ".llm_tasks").iterdir())
        assert len(task_dirs) > 0
        selection_path = task_dirs[0] / "file_selection.json"
        selection = json.loads(selection_path.read_text())

        must_create = selection.get("must_create_files", [])
        assert "src/transport/http2_transport.py" in must_create, (
            f"must_create_files={must_create}"
        )
