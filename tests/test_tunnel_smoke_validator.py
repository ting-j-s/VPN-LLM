"""Tests for tunnel smoke validation layer.

Covers TunnelSmokeResult, TunnelSmokeValidation, log scanning,
task_needs_tunnel_smoke, and UserIntentValidator integration.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.llm.intent_contract import AcceptanceCriterion, IntentContract
from src.llm.tunnel_smoke_validator import (
    TunnelSmokeResult,
    TunnelSmokeValidation,
    _has_marker,
    _scan_log,
    check_phase9_env,
    run_mock_tun_smoke,
    task_needs_tunnel_smoke,
)


# ---------------------------------------------------------------------------
# TunnelSmokeResult
# ---------------------------------------------------------------------------

class TestTunnelSmokeResult:
    def test_success_result(self):
        r = TunnelSmokeResult(transport="tcp", status="pass", duration_sec=2.5)
        assert r.success is True
        d = r.to_dict()
        assert d["transport"] == "tcp"
        assert d["status"] == "pass"
        assert d["duration_sec"] == 2.5
        assert d["error"] is None

    def test_fail_result(self):
        r = TunnelSmokeResult(
            transport="tcp", status="fail", duration_sec=0.0,
            error="Server process exited prematurely",
        )
        assert r.success is False
        assert r.to_dict()["error"] == "Server process exited prematurely"

    def test_skipped_result(self):
        r = TunnelSmokeResult(transport="tcp", status="skipped", duration_sec=0.0)
        assert r.success is False

    def test_to_dict_includes_server_client_errors(self):
        r = TunnelSmokeResult(
            transport="websocket", status="fail", duration_sec=1.0,
            server_errors=["[server] Traceback in handler"],
            client_errors=["[client] Connection refused"],
            log_dir="/tmp/smoke",
        )
        d = r.to_dict()
        assert len(d["server_errors"]) == 1
        assert len(d["client_errors"]) == 1
        assert d["log_dir"] == "/tmp/smoke"


# ---------------------------------------------------------------------------
# TunnelSmokeValidation
# ---------------------------------------------------------------------------

class TestTunnelSmokeValidation:
    def test_empty_validation(self):
        v = TunnelSmokeValidation()
        assert v.mock_tun_passed is False
        assert v.tunnel_validated is False
        d = v.to_dict()
        assert d["mock_tun_smoke"] is None
        assert d["phase9_passed"] is False
        assert d["phase9_skipped"] is False

    def test_mock_tun_passed(self):
        mock = TunnelSmokeResult(transport="tcp", status="pass", duration_sec=1.0)
        v = TunnelSmokeValidation(mock_tun_smoke=mock)
        assert v.mock_tun_passed is True
        assert v.tunnel_validated is True

    def test_mock_tun_failed_phase9_passed(self):
        mock = TunnelSmokeResult(transport="tcp", status="fail", duration_sec=0.5)
        v = TunnelSmokeValidation(mock_tun_smoke=mock, phase9_passed=True)
        assert v.mock_tun_passed is False
        assert v.tunnel_validated is True  # phase9 covers it

    def test_both_failed(self):
        mock = TunnelSmokeResult(transport="tcp", status="fail", duration_sec=0.5)
        v = TunnelSmokeValidation(mock_tun_smoke=mock, phase9_passed=False)
        assert v.tunnel_validated is False

    def test_to_dict_with_all_fields(self):
        mock = TunnelSmokeResult(transport="tcp", status="pass", duration_sec=1.5)
        v = TunnelSmokeValidation(
            mock_tun_smoke=mock,
            phase9_passed=True,
            phase9_skipped=False,
            phase9_skip_reason="",
            real_netns_available=True,
        )
        d = v.to_dict()
        assert d["mock_tun_smoke"]["transport"] == "tcp"
        assert d["phase9_passed"] is True


# ---------------------------------------------------------------------------
# Log scanning
# ---------------------------------------------------------------------------

class TestScanLog:
    def test_detects_traceback(self):
        errors = _scan_log("Something happened\nTraceback (most recent call last):\n  File x", "server")
        assert len(errors) == 1
        assert "[server]" in errors[0]
        assert "Traceback" in errors[0]

    def test_detects_transport_error(self):
        errors = _scan_log("TransportError: connection timeout", "client")
        assert len(errors) == 1
        assert "TransportError" in errors[0]

    def test_detects_connection_refused(self):
        errors = _scan_log("Connection refused to 127.0.0.1:8080", "client")
        assert len(errors) == 1

    def test_detects_address_in_use(self):
        errors = _scan_log("Address already in use", "server")
        assert len(errors) == 1

    def test_clean_log_produces_no_errors(self):
        errors = _scan_log("Tunnel listening, running\nSession established\n", "server")
        assert len(errors) == 0

    def test_empty_log(self):
        errors = _scan_log("", "server")
        assert len(errors) == 0

    def test_multiple_error_types(self):
        log = (
            "Starting server...\n"
            "Traceback (most recent call last):\n"
            "  File x, line 42\n"
            "TransportError: bad packet\n"
        )
        errors = _scan_log(log, "server")
        assert len(errors) == 2


class TestHasMarker:
    def test_finds_server_running_marker(self):
        assert _has_marker("Tunnel listening, running on port 9000", ["Tunnel listening, running"])

    def test_finds_client_running_marker(self):
        assert _has_marker("Tunnel established, running with session abc", ["Tunnel established, running"])

    def test_no_marker(self):
        assert not _has_marker("Initializing transport...", ["Tunnel listening, running"])

    def test_empty_text(self):
        assert not _has_marker("", ["Tunnel listening, running"])


# ---------------------------------------------------------------------------
# task_needs_tunnel_smoke
# ---------------------------------------------------------------------------

class TestTaskNeedsTunnelSmoke:
    def test_transport_addition_needs_smoke(self):
        assert task_needs_tunnel_smoke("transport_addition") is True

    def test_transport_change_needs_smoke(self):
        assert task_needs_tunnel_smoke("transport_change") is True

    def test_feature_addition_needs_smoke(self):
        assert task_needs_tunnel_smoke("feature_addition") is True

    def test_core_change_needs_smoke(self):
        assert task_needs_tunnel_smoke("core_change") is True

    def test_config_change_needs_smoke(self):
        assert task_needs_tunnel_smoke("config_change") is True

    def test_traffic_shaping_needs_smoke(self):
        assert task_needs_tunnel_smoke("traffic_shaping") is True

    def test_docs_update_skips_smoke(self):
        assert task_needs_tunnel_smoke("docs_update") is False

    def test_test_addition_skips_smoke(self):
        assert task_needs_tunnel_smoke("test_addition") is False

    def test_unknown_type_defaults_to_smoke(self):
        """Default rule: every user request must pass tunnel smoke."""
        assert task_needs_tunnel_smoke(None) is True
        assert task_needs_tunnel_smoke("some_random_type") is True

    def test_runtime_contract_forces_smoke(self):
        contract = IntentContract(
            task_type="docs_update",
            runtime_required=True,
        )
        assert task_needs_tunnel_smoke("docs_update", contract) is True

    def test_end_to_end_contract_forces_smoke(self):
        contract = IntentContract(
            task_type="test_addition",
            end_to_end_required=True,
        )
        assert task_needs_tunnel_smoke("test_addition", contract) is True


# ---------------------------------------------------------------------------
# check_phase9_env
# ---------------------------------------------------------------------------

class TestCheckPhase9Env:
    def test_script_not_found(self):
        with patch("src.llm.tunnel_smoke_validator._find_phase9_script", return_value=None):
            available, reason = check_phase9_env()
            assert available is False
            assert "not found" in reason

    def test_env_check_success(self):
        fake_script = MagicMock()
        fake_script.__str__ = lambda s: "/fake/phase9.py"
        with patch("src.llm.tunnel_smoke_validator._find_phase9_script", return_value=fake_script):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = "/dev/net/tun available"
                mock_run.return_value.stderr = ""
                available, reason = check_phase9_env()
                assert available is True

    def test_env_check_failure(self):
        fake_script = MagicMock()
        fake_script.__str__ = lambda s: "/fake/phase9.py"
        with patch("src.llm.tunnel_smoke_validator._find_phase9_script", return_value=fake_script):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 1
                mock_run.return_value.stdout = ""
                mock_run.return_value.stderr = "no /dev/net/tun"
                available, reason = check_phase9_env()
                assert available is False

    def test_env_check_exception(self):
        fake_script = MagicMock()
        fake_script.__str__ = lambda s: "/fake/phase9.py"
        with patch("src.llm.tunnel_smoke_validator._find_phase9_script", return_value=fake_script):
            with patch("subprocess.run", side_effect=OSError("no python")):
                available, reason = check_phase9_env()
                assert available is False
                assert "error" in reason.lower()


# ---------------------------------------------------------------------------
# run_mock_tun_smoke — integration tests
# ---------------------------------------------------------------------------

class TestRunMockTunSmoke:
    def test_missing_config_files(self, tmp_path):
        """When config files don't exist, smoke should fail immediately."""
        with patch("src.llm.tunnel_smoke_validator._find_config_dir", return_value=tmp_path):
            result = run_mock_tun_smoke(transport="tcp", output_dir=str(tmp_path / "smoke"))
            assert result.status == "fail"
            assert "Missing config files" in result.error

    def test_server_exits_immediately(self, tmp_path):
        """When server process exits right away, smoke should fail."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "server.yaml").write_text("port: 9999\n")
        (config_dir / "client.yaml").write_text("port: 9999\n")

        with patch("src.llm.tunnel_smoke_validator._find_config_dir", return_value=config_dir):
            with patch("src.llm.tunnel_smoke_validator._free_port", return_value=19999):
                with patch("subprocess.Popen") as mock_popen:
                    mock_proc = MagicMock()
                    mock_proc.poll.return_value = 1  # exited immediately
                    mock_popen.return_value = mock_proc

                    result = run_mock_tun_smoke(
                        transport="tcp",
                        output_dir=str(tmp_path / "smoke"),
                    )
                    assert result.status == "fail"
                    assert "exited prematurely" in result.error


# ---------------------------------------------------------------------------
# UserIntentValidator tunnel smoke integration
# ---------------------------------------------------------------------------

class TestUserIntentValidatorTunnelSmoke:
    """Verify that UserIntentValidator correctly processes tunnel smoke results."""

    def test_determine_final_status_runtime_contract_mock_fail(self):
        from src.llm.user_intent_validator import (
            UserIntentValidationResult,
            UserIntentValidator,
        )
        validator = UserIntentValidator()
        result = UserIntentValidationResult(
            patch_integrity_status="passed",
            functional_validation_status="passed",
            user_intent_status="passed",
        )
        contract = IntentContract(task_type="transport_addition", runtime_required=True)

        mock_smoke = TunnelSmokeResult(
            transport="tcp", status="fail", duration_sec=0.5,
            error="Tunnel did not reach running state",
        )
        smoke_validation = TunnelSmokeValidation(mock_tun_smoke=mock_smoke)

        status = validator._determine_final_status(
            result, contract, tunnel_smoke_result=smoke_validation,
        )
        assert status == "intent_not_satisfied"

    def test_determine_final_status_runtime_contract_mock_pass(self):
        from src.llm.user_intent_validator import (
            UserIntentValidationResult,
            UserIntentValidator,
        )
        validator = UserIntentValidator()
        result = UserIntentValidationResult(
            patch_integrity_status="passed",
            functional_validation_status="passed",
            user_intent_status="passed",
        )
        contract = IntentContract(task_type="transport_addition", runtime_required=True)

        mock_smoke = TunnelSmokeResult(transport="tcp", status="pass", duration_sec=2.0)
        smoke_validation = TunnelSmokeValidation(mock_tun_smoke=mock_smoke)

        status = validator._determine_final_status(
            result, contract, tunnel_smoke_result=smoke_validation,
        )
        assert status == "completed"

    def test_determine_final_status_no_contract_no_impact(self):
        """Without a contract, tunnel smoke failure shouldn't change status."""
        from src.llm.user_intent_validator import (
            UserIntentValidationResult,
            UserIntentValidator,
        )
        validator = UserIntentValidator()
        result = UserIntentValidationResult(
            patch_integrity_status="passed",
            functional_validation_status="passed",
            user_intent_status="passed",
        )

        mock_smoke = TunnelSmokeResult(transport="tcp", status="fail", duration_sec=0.5)
        smoke_validation = TunnelSmokeValidation(mock_tun_smoke=mock_smoke)

        status = validator._determine_final_status(
            result, None, tunnel_smoke_result=smoke_validation,
        )
        assert status == "completed"

    def test_determine_final_status_docs_update_mock_fail_not_runtime(self):
        """docs_update with mock failure but no runtime contract should pass."""
        from src.llm.user_intent_validator import (
            UserIntentValidationResult,
            UserIntentValidator,
        )
        validator = UserIntentValidator()
        result = UserIntentValidationResult(
            patch_integrity_status="passed",
            functional_validation_status="passed",
            user_intent_status="passed",
        )
        contract = IntentContract(task_type="docs_update")

        mock_smoke = TunnelSmokeResult(transport="tcp", status="fail", duration_sec=0.5)
        smoke_validation = TunnelSmokeValidation(mock_tun_smoke=mock_smoke)

        status = validator._determine_final_status(
            result, contract, tunnel_smoke_result=smoke_validation,
        )
        # No runtime_required or end_to_end_required → not blocked by tunnel smoke
        assert status == "completed"

    def test_check_tunnel_smoke_adds_evidence_on_pass(self):
        from src.llm.user_intent_validator import (
            UserIntentValidationResult,
            UserIntentValidator,
        )
        validator = UserIntentValidator()
        result = UserIntentValidationResult()
        contract = IntentContract(task_type="transport_addition", runtime_required=True)

        mock_smoke = TunnelSmokeResult(transport="tcp", status="pass", duration_sec=1.0)
        smoke_validation = TunnelSmokeValidation(mock_tun_smoke=mock_smoke)

        validator._check_tunnel_smoke(result, contract, smoke_validation)
        assert any(e.criterion_name == "tunnel_smoke_mock_tun" and e.satisfied for e in result.evidence)

    def test_check_tunnel_smoke_sets_failed_on_runtime_smoke_fail(self):
        from src.llm.user_intent_validator import (
            UserIntentValidationResult,
            UserIntentValidator,
        )
        validator = UserIntentValidator()
        result = UserIntentValidationResult(user_intent_status="passed")
        contract = IntentContract(task_type="transport_addition", runtime_required=True)

        mock_smoke = TunnelSmokeResult(
            transport="tcp", status="fail", duration_sec=0.5,
            error="connection reset",
        )
        smoke_validation = TunnelSmokeValidation(mock_tun_smoke=mock_smoke)

        validator._check_tunnel_smoke(result, contract, smoke_validation)
        assert result.user_intent_status == "failed"
        assert "tunnel_smoke_mock_tun" in result.unmet_acceptance_criteria
        assert any("connection reset" in e for e in result.errors)

    def test_check_tunnel_smoke_no_smoke_run_adds_warning(self):
        from src.llm.user_intent_validator import (
            UserIntentValidationResult,
            UserIntentValidator,
        )
        validator = UserIntentValidator()
        result = UserIntentValidationResult(user_intent_status="passed")
        contract = IntentContract(task_type="transport_addition", runtime_required=True)

        # No mock_tun_smoke set
        smoke_validation = TunnelSmokeValidation()

        validator._check_tunnel_smoke(result, contract, smoke_validation)
        assert any("not run" in w for w in result.warnings)
        assert "tunnel_smoke_mock_tun" in result.unmet_acceptance_criteria
