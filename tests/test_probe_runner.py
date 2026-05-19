"""Tests for probe runners (MockProbeRunner + LocalSocketProbeRunner safety)."""

import pytest
from src.evaluation.probe import (
    ProbeScenario,
    PROBE_SCENARIOS,
    MockProbeRunner,
    LocalSocketProbeRunner,
    ProbeResult,
)


class TestMockProbeRunner:
    """Test MockProbeRunner deterministic behavior."""

    def test_run_single_scenario(self):
        runner = MockProbeRunner(seed=42)
        s = ProbeScenario(
            name="test_mock",
            description="mock test",
            payload=b"\x00",
            expected_policy="silent close",
            tags=["short"],
        )
        result = runner.run_scenario(s)
        assert isinstance(result, ProbeResult)
        assert result.scenario.name == "test_mock"
        assert result.connected
        assert result.close_observed

    def test_empty_connection_timeout(self):
        runner = MockProbeRunner()
        s = ProbeScenario(
            name="empty_test",
            description="test empty",
            payload=None,
            expected_policy="timeout",
            timeout_s=4.0,
            tags=["empty"],
        )
        result = runner.run_scenario(s)
        assert result.timeout_observed
        assert not result.close_observed
        assert result.error_type == "timeout"

    def test_session_scenario_not_closed(self):
        runner = MockProbeRunner()
        s = ProbeScenario(
            name="session_test",
            description="test session",
            payload=b"VTUN\x01\x01\x00\x00\x00\x04" + b"\x00" * 16 + b"abcd",
            expected_policy="silent drop",
            tags=["session"],
        )
        result = runner.run_scenario(s)
        assert result.connected
        assert not result.close_observed
        assert not result.timeout_observed

    def test_run_all_builtin_scenarios(self):
        runner = MockProbeRunner()
        results = runner.run_all()
        assert len(results) == len(PROBE_SCENARIOS)

    def test_run_all_returns_consistent_results(self):
        runner1 = MockProbeRunner(seed=42)
        runner2 = MockProbeRunner(seed=42)
        r1 = runner1.run_all()
        r2 = runner2.run_all()
        for a, b in zip(r1, r2):
            assert a.close_observed == b.close_observed
            assert a.timeout_observed == b.timeout_observed
            assert a.error_type == b.error_type

    def test_result_to_dict(self):
        runner = MockProbeRunner()
        s = PROBE_SCENARIOS[0]
        result = runner.run_scenario(s)
        d = result.to_dict()
        assert d["scenario"] == s.name
        assert "connected" in d
        assert "elapsed_ms" in d


class TestLocalSocketProbeRunnerSafety:
    """Test LocalSocketProbeRunner's local-only enforcement."""

    def test_accepts_localhost(self):
        runner = LocalSocketProbeRunner(host="127.0.0.1", port=9000)
        assert runner.host == "127.0.0.1"

    def test_accepts_localhost_ipv6(self):
        runner = LocalSocketProbeRunner(host="::1", port=9000)
        assert runner.host == "::1"

    def test_accepts_localhost_name(self):
        runner = LocalSocketProbeRunner(host="localhost", port=9000)
        assert runner.host == "localhost"

    def test_rejects_remote_host(self):
        with pytest.raises(ValueError, match="local"):
            LocalSocketProbeRunner(host="192.168.1.1", port=9000)

    def test_rejects_external_domain(self):
        with pytest.raises(ValueError, match="local"):
            LocalSocketProbeRunner(host="example.com", port=9000)

    def test_rejects_public_ip(self):
        with pytest.raises(ValueError, match="local"):
            LocalSocketProbeRunner(host="8.8.8.8", port=9000)

    def test_rejects_empty_host(self):
        with pytest.raises(ValueError, match="local"):
            LocalSocketProbeRunner(host="0.0.0.0", port=9000)
