"""Tests for CommitAdvisor."""

import os
import subprocess

import pytest

from src.llm.commit_advisor import CommitAdvisor
from src.llm.task_planner import TaskPlan, TASK_TRANSPORT_CHANGE, TASK_DOCS_UPDATE, TASK_UNKNOWN


def _make_temp_git_repo(tmp_path):
    """Create a temporary git repo with a committed file and some uncommitted changes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True)

    (repo / "a.py").write_text("old\n")
    subprocess.run(["git", "add", "a.py"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)

    # Make a change so git diff produces output
    (repo / "a.py").write_text("new\n")
    (repo / "b.py").write_text("new file\n")
    return str(repo)


class TestCollectDiffSummary:
    """Test collect_diff_summary in a temporary git repo."""

    def test_collect_diff_returns_dict(self, tmp_path):
        repo = _make_temp_git_repo(tmp_path)
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            advisor = CommitAdvisor()
            result = advisor.collect_diff_summary()
            assert isinstance(result, dict)
            assert "diff_stat" in result
            assert "files" in result
            assert "returncode" in result
            assert "success" in result
        finally:
            os.chdir(cwd)

    def test_collect_diff_detects_changed_files(self, tmp_path):
        repo = _make_temp_git_repo(tmp_path)
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            advisor = CommitAdvisor()
            result = advisor.collect_diff_summary()
            assert "a.py" in result["files"]
        finally:
            os.chdir(cwd)

    def test_collect_diff_success_is_true(self, tmp_path):
        repo = _make_temp_git_repo(tmp_path)
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            advisor = CommitAdvisor()
            result = advisor.collect_diff_summary()
            assert result["success"] is True
            assert result["returncode"] == 0
        finally:
            os.chdir(cwd)

    def test_collect_diff_stat_has_content(self, tmp_path):
        repo = _make_temp_git_repo(tmp_path)
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            advisor = CommitAdvisor()
            result = advisor.collect_diff_summary()
            assert len(result["diff_stat"]) > 0
        finally:
            os.chdir(cwd)


class TestSuggestCommitMessage:
    """Test rule-based commit message generation."""

    def test_transport_change_produces_feat_prefix(self):
        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch to websocket",
            target_transport="websocket",
            affected_areas=["src/transport/"],
        )
        msg = CommitAdvisor.suggest_commit_message(plan, ["a.py"], True)
        assert msg.startswith("feat(websocket):")

    def test_transport_change_without_transport_no_scope(self):
        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch transport",
            target_transport=None,
            affected_areas=["src/transport/"],
        )
        msg = CommitAdvisor.suggest_commit_message(plan, ["a.py"], True)
        assert msg.startswith("feat: ")

    def test_docs_update_produces_docs_prefix(self):
        plan = TaskPlan(
            task_type=TASK_DOCS_UPDATE,
            description="update readme",
            target_transport=None,
            affected_areas=["docs/"],
        )
        msg = CommitAdvisor.suggest_commit_message(plan, ["README.md"], True)
        assert msg.startswith("docs: ")

    def test_bugfix_produces_fix_prefix(self):
        from src.llm.commit_advisor import TASK_BUGFIX
        plan = TaskPlan(
            task_type=TASK_BUGFIX,
            description="fix null pointer",
            target_transport=None,
            affected_areas=["src/"],
        )
        msg = CommitAdvisor.suggest_commit_message(plan, ["src/main.py"], True)
        assert msg.startswith("fix: ")

    def test_refactor_produces_refactor_prefix(self):
        from src.llm.commit_advisor import TASK_REFACTOR
        plan = TaskPlan(
            task_type=TASK_REFACTOR,
            description="refactor transport layer",
            target_transport=None,
            affected_areas=["src/transport/"],
        )
        msg = CommitAdvisor.suggest_commit_message(plan, ["src/transport/base.py"], True)
        assert msg.startswith("refactor: ")

    def test_unknown_produces_chore_prefix(self):
        plan = TaskPlan(
            task_type=TASK_UNKNOWN,
            description="misc updates",
            target_transport=None,
            affected_areas=[],
        )
        msg = CommitAdvisor.suggest_commit_message(plan, [], True)
        assert msg.startswith("chore: ")

    def test_validation_failed_prefixed_with_do_not_commit(self):
        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch to tcp",
            target_transport="tcp",
            affected_areas=["src/transport/"],
        )
        msg = CommitAdvisor.suggest_commit_message(plan, ["a.py"], False)
        assert msg.startswith("[DO NOT COMMIT]")

    def test_message_includes_description(self):
        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch default transport to WebSocket",
            target_transport="websocket",
            affected_areas=["src/transport/"],
        )
        msg = CommitAdvisor.suggest_commit_message(plan, ["a.py"], True)
        assert "switch default transport to WebSocket" in msg


class TestWriteCommitAdvice:
    """Test write_commit_advice file output."""

    def test_writes_message_txt(self, tmp_path):
        task_dir = str(tmp_path / "task")
        advisor = CommitAdvisor()
        advisor.write_commit_advice(task_dir, {
            "message": "feat(websocket): switch transport",
            "changed_files": ["config/server.yaml"],
            "diff_stat": " config/server.yaml | 2 +-\n 1 file changed",
            "validation_passed": True,
        })
        msg_path = os.path.join(task_dir, "suggested_commit_message.txt")
        assert os.path.isfile(msg_path)
        content = open(msg_path).read()
        assert "feat(websocket): switch transport" in content

    def test_writes_summary_md(self, tmp_path):
        task_dir = str(tmp_path / "task")
        advisor = CommitAdvisor()
        advisor.write_commit_advice(task_dir, {
            "message": "feat: test",
            "changed_files": ["a.py"],
            "diff_stat": " a.py | 1 +\n",
            "validation_passed": True,
        })
        summary_path = os.path.join(task_dir, "commit_summary.md")
        assert os.path.isfile(summary_path)
        content = open(summary_path).read()
        assert "# Commit Summary" in content
        assert "feat: test" in content
        assert "a.py" in content
        assert "Commit was suggested but NOT created" in content
        assert "Push was NOT performed" in content

    def test_summary_contains_no_api_key(self, tmp_path):
        task_dir = str(tmp_path / "task")
        advisor = CommitAdvisor()
        advisor.write_commit_advice(task_dir, {
            "message": "feat: test",
            "changed_files": ["a.py"],
            "diff_stat": "",
            "validation_passed": True,
        })
        summary_path = os.path.join(task_dir, "commit_summary.md")
        content = open(summary_path).read().lower()
        assert "api_key" not in content
        assert "sk-" not in content
        assert "token" not in content
        assert "password" not in content

    def test_validation_failed_warning_in_summary(self, tmp_path):
        task_dir = str(tmp_path / "task")
        advisor = CommitAdvisor()
        advisor.write_commit_advice(task_dir, {
            "message": "[DO NOT COMMIT] fix: bug",
            "changed_files": ["a.py"],
            "diff_stat": "",
            "validation_passed": False,
        })
        summary_path = os.path.join(task_dir, "commit_summary.md")
        content = open(summary_path).read()
        assert "Validation did not fully pass" in content

    def test_empty_changed_files_shows_none(self, tmp_path):
        task_dir = str(tmp_path / "task")
        advisor = CommitAdvisor()
        advisor.write_commit_advice(task_dir, {
            "message": "chore: cleanup",
            "changed_files": [],
            "diff_stat": "",
            "validation_passed": True,
        })
        summary_path = os.path.join(task_dir, "commit_summary.md")
        content = open(summary_path).read()
        assert "no changes detected" in content


class TestNoDestructiveOperations:
    """Verify CommitAdvisor never runs git add, git commit, or git push."""

    def test_no_git_add_in_class(self):
        """Source code must not contain git add."""
        import inspect
        src = inspect.getsource(CommitAdvisor)
        assert "git add" not in src

    def test_no_git_commit_in_class(self):
        """Source code must not contain git commit."""
        import inspect
        src = inspect.getsource(CommitAdvisor)
        assert "git commit" not in src

    def test_no_git_push_in_class(self):
        """Source code must not contain git push."""
        import inspect
        src = inspect.getsource(CommitAdvisor)
        assert "git push" not in src
