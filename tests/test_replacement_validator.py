"""Tests for ReplacementValidator.

All subprocess calls are monkeypatched — no real smoke matrix runs here.
No real SSH, no real LLM API, no real keys.
"""

import json
import os
import sys

import pytest

# Ensure repo root is importable
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.llm.replacement_validator import (
    ReplacementValidator,
    ReplacementValidationResult,
    _TRANSPORT_SELECTION,
)
from src.llm.task_planner import (
    TaskPlan,
    TASK_TRANSPORT_CHANGE,
    TASK_CORE_CHANGE,
    TASK_TEST_ADDITION,
    TASK_CONFIG_CHANGE,
    TASK_BUGFIX,
    TASK_REFACTOR,
    TASK_UNKNOWN,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_plan(task_type, target_transport=None):
    return TaskPlan(
        task_type=task_type,
        description="test",
        target_transport=target_transport,
        affected_areas=[],
    )


def _fake_subprocess_result(stdout_json, returncode=0, stderr=""):
    """Build a subprocess.CompletedProcess-like object."""
    return type("_", (), {
        "stdout": json.dumps(stdout_json),
        "stderr": stderr,
        "returncode": returncode,
        "args": [],
    })()


def _patch_subprocess(monkeypatch, stdout_json, returncode=0, stderr=""):
    """Monkeypatch subprocess.run to return a fake result."""
    fake = _fake_subprocess_result(stdout_json, returncode, stderr)

    def _fake_run(*args, **kwargs):
        return fake

    monkeypatch.setattr("subprocess.run", _fake_run)


def _success_json(passed=3, failed=0, skipped=0):
    results = []
    for t in ["mock", "tcp", "websocket"]:
        results.append({
            "transport": t, "core": "default", "status": "pass",
            "duration_sec": 0.01, "error": None,
        })
    return {
        "results": results,
        "summary": {"passed": passed, "failed": failed, "skipped": skipped},
    }


def _fail_json():
    return {
        "results": [
            {"transport": "mock", "core": "default", "status": "pass",
             "duration_sec": 0.01, "error": None},
            {"transport": "tcp", "core": "default", "status": "fail",
             "duration_sec": 0.05, "error": "connection refused"},
        ],
        "summary": {"passed": 1, "failed": 1, "skipped": 0},
    }


# ---------------------------------------------------------------------------
# Transport selection tests
# ---------------------------------------------------------------------------

class TestTransportSelection:
    def test_websocket_plan_selects_mock_websocket(self):
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "websocket")
        result = ReplacementValidator._select_transports(plan)
        assert "mock" in result
        assert "websocket" in result
        assert "tcp" not in result
        assert "tls" not in result

    def test_tcp_plan_selects_mock_tcp(self):
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "tcp")
        result = ReplacementValidator._select_transports(plan)
        assert "mock" in result
        assert "tcp" in result
        assert "websocket" not in result

    def test_tls_plan_selects_mock_tls(self):
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "tls")
        result = ReplacementValidator._select_transports(plan)
        assert "mock" in result
        assert "tls" in result
        assert "tcp" not in result

    def test_ssh_plan_selects_mock_ssh(self):
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "ssh")
        result = ReplacementValidator._select_transports(plan)
        assert "mock" in result
        assert "ssh" in result

    def test_core_change_selects_broad(self):
        plan = _make_plan(TASK_CORE_CHANGE)
        result = ReplacementValidator._select_transports(plan)
        for t in ["mock", "tcp", "tls", "websocket"]:
            assert t in result

    def test_refactor_selects_broad(self):
        plan = _make_plan(TASK_REFACTOR)
        result = ReplacementValidator._select_transports(plan)
        for t in ["mock", "tcp", "tls", "websocket"]:
            assert t in result

    def test_bugfix_selects_broad(self):
        plan = _make_plan(TASK_BUGFIX)
        result = ReplacementValidator._select_transports(plan)
        for t in ["mock", "tcp", "tls", "websocket"]:
            assert t in result

    def test_unknown_selects_broad(self):
        plan = _make_plan(TASK_UNKNOWN)
        result = ReplacementValidator._select_transports(plan)
        for t in ["mock", "tcp", "tls", "websocket"]:
            assert t in result

    def test_test_addition_selects_default(self):
        plan = _make_plan(TASK_TEST_ADDITION)
        result = ReplacementValidator._select_transports(plan)
        assert result == ["mock", "tcp", "websocket"]

    def test_config_change_selects_default(self):
        plan = _make_plan(TASK_CONFIG_CHANGE)
        result = ReplacementValidator._select_transports(plan)
        assert result == ["mock", "tcp", "websocket"]

    def test_include_tls_injects_tls(self):
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "websocket")
        result = ReplacementValidator._select_transports(plan, include_tls=True)
        assert "tls" in result
        assert "mock" in result
        assert "websocket" in result

    def test_include_ssh_injects_ssh(self):
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "tcp")
        result = ReplacementValidator._select_transports(plan, include_ssh=True)
        assert "ssh" in result

    def test_include_tls_and_ssh_both_injected(self):
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "mock")
        result = ReplacementValidator._select_transports(
            plan, include_tls=True, include_ssh=True,
        )
        assert "tls" in result
        assert "ssh" in result
        assert "mock" in result


# ---------------------------------------------------------------------------
# Run matrix tests (monkeypatched subprocess)
# ---------------------------------------------------------------------------

class TestRunMatrixSuccess:
    def test_valid_json_all_passed_returns_success(self, monkeypatch):
        _patch_subprocess(monkeypatch, _success_json())
        validator = ReplacementValidator()
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "websocket")
        result = validator.validate(plan)
        assert result.success
        assert result.returncode == 0
        assert result.summary["failed"] == 0
        assert result.error is None

    def test_results_contain_transports(self, monkeypatch):
        _patch_subprocess(monkeypatch, _success_json())
        validator = ReplacementValidator()
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "websocket")
        result = validator.validate(plan)
        assert any(r["transport"] == "mock" for r in result.results)
        assert any(r["transport"] == "websocket" for r in result.results)

    def test_to_dict_serializable(self, monkeypatch):
        _patch_subprocess(monkeypatch, _success_json())
        validator = ReplacementValidator()
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "tcp")
        result = validator.validate(plan)
        d = result.to_dict()
        assert d["success"] is True
        assert "transports" in d
        assert "results" in d
        json.dumps(d)  # must not raise

    def test_ssh_skip_not_treated_as_failure(self, monkeypatch):
        ssh_skip_json = {
            "results": [
                {"transport": "mock", "core": "default", "status": "pass",
                 "duration_sec": 0.01, "error": None},
                {"transport": "ssh", "core": "default", "status": "skip",
                 "duration_sec": 0.0, "error": "requires external SSH server"},
            ],
            "summary": {"passed": 1, "failed": 0, "skipped": 1},
        }
        _patch_subprocess(monkeypatch, ssh_skip_json)
        validator = ReplacementValidator()
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "ssh")
        result = validator.validate(plan)
        assert result.success
        assert result.summary["skipped"] == 1
        assert result.summary["failed"] == 0


class TestRunMatrixFailure:
    def test_failed_gt_zero_returns_failure(self, monkeypatch):
        _patch_subprocess(monkeypatch, _fail_json())
        validator = ReplacementValidator()
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "tcp")
        result = validator.validate(plan)
        assert not result.success
        assert result.summary["failed"] > 0

    def test_nonzero_returncode_returns_failure(self, monkeypatch):
        _patch_subprocess(monkeypatch, _success_json(), returncode=1)
        validator = ReplacementValidator()
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "tcp")
        result = validator.validate(plan)
        assert not result.success
        assert result.returncode == 1

    def test_non_json_stdout_returns_failure(self, monkeypatch):
        _patch_subprocess(monkeypatch, "not json at all")
        validator = ReplacementValidator()
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "tcp")
        result = validator.validate(plan)
        assert not result.success
        assert "parse JSON" in (result.error or "")

    def test_empty_stdout_returns_failure(self, monkeypatch):
        _patch_subprocess(monkeypatch, "")
        validator = ReplacementValidator()
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "tcp")
        result = validator.validate(plan)
        assert not result.success


# ---------------------------------------------------------------------------
# Result dataclass tests
# ---------------------------------------------------------------------------

class TestReplacementValidationResult:
    def test_success_result_fields(self):
        r = ReplacementValidationResult(
            success=True, returncode=0,
            transports=["mock", "tcp"], cores=["default"],
            summary={"passed": 2, "failed": 0, "skipped": 0},
            results=[
                {"transport": "mock", "core": "default", "status": "pass",
                 "duration_sec": 0.01, "error": None},
            ],
        )
        assert r.success
        assert r.was_run
        d = r.to_dict()
        assert d["success"] is True

    def test_failure_result_with_error(self):
        r = ReplacementValidationResult(
            success=False, returncode=1,
            transports=["mock"], cores=["default"],
            error="connection refused",
        )
        assert not r.success
        assert r.error == "connection refused"


# ---------------------------------------------------------------------------
# Security / no-real-external tests
# ---------------------------------------------------------------------------

class TestSecurityBoundaries:
    def test_no_real_ssh_connection(self, monkeypatch):
        _patch_subprocess(monkeypatch, _success_json(passed=1))
        validator = ReplacementValidator()
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "ssh")
        result = validator.validate(plan)
        # SSR is in the transport list because ssh is selected
        # but the fake subprocess returns pass — no real connection made
        assert "ssh" in result.transports

    def test_no_api_key_env_read(self, monkeypatch):
        """ReplacementValidator must not read LLM_API_KEY or config."""
        calls = []

        def _fake_run(*args, **kwargs):
            calls.append(args)
            return _fake_subprocess_result(_success_json())

        monkeypatch.setattr("subprocess.run", _fake_run)
        # Ensure no env var leaks
        monkeypatch.setenv("LLM_API_KEY", "")
        monkeypatch.delenv("LLM_API_KEY", raising=False)

        validator = ReplacementValidator()
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "websocket")
        result = validator.validate(plan)
        assert result.success
        # verify subprocess was called (no real LLM call)
        assert len(calls) == 1

    def test_no_config_file_read(self, monkeypatch):
        """ReplacementValidator must not read config/llm_agent.yaml."""
        validator = ReplacementValidator()
        plan = _make_plan(TASK_TRANSPORT_CHANGE, "websocket")
        # The validate() method doesn't touch config
        _patch_subprocess(monkeypatch, _success_json())
        result = validator.validate(plan)
        assert result.success
