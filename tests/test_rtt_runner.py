"""Tests for RTT runners: MockRTTRunner, LocalTCPRTTRunner, OptionalPingRunner."""

import pytest
from src.evaluation.rtt.rtt_runner import (
    MockRTTRunner,
    LocalTCPRTTRunner,
    OptionalPingRunner,
    _is_local_host,
)
from src.evaluation.rtt.rtt_measurements import CrossLayerRTTReport


class TestIsLocalHost:
    """Test _is_local_host validation."""

    def test_localhost_ipv4(self):
        assert _is_local_host("127.0.0.1") is True

    def test_localhost_name(self):
        assert _is_local_host("localhost") is True

    def test_localhost_ipv6(self):
        assert _is_local_host("::1") is True

    def test_rejects_remote_ip(self):
        assert _is_local_host("192.168.1.1") is False

    def test_rejects_external(self):
        assert _is_local_host("example.com") is False

    def test_rejects_public_ip(self):
        assert _is_local_host("8.8.8.8") is False

    def test_accepts_127_range(self):
        assert _is_local_host("127.0.0.42") is True


class TestMockRTTRunner:
    """Test MockRTTRunner direct and proxy_like profiles."""

    def test_direct_profile_creates_report(self):
        runner = MockRTTRunner(profile="direct", seed=42)
        report = runner.run()
        assert isinstance(report, CrossLayerRTTReport)
        assert report.trace_type == "mock"
        assert report.application_rtt_ms is not None
        assert report.transport_rtt_ms is not None
        assert report.app_transport_diff_ms is not None

    def test_direct_profile_low_risk(self):
        runner = MockRTTRunner(profile="direct")
        report = runner.run()
        assert report.risk_level == "low"
        assert report.risk_score < 0.3

    def test_direct_profile_low_diff(self):
        runner = MockRTTRunner(profile="direct")
        report = runner.run()
        # direct: app ~= transport, diff should be small
        assert report.app_transport_diff_ms < 2.0

    def test_proxy_like_profile_high_risk(self):
        runner = MockRTTRunner(profile="proxy_like")
        report = runner.run()
        assert report.risk_level == "high"
        assert report.risk_score >= 0.6

    def test_proxy_like_profile_high_diff(self):
        runner = MockRTTRunner(profile="proxy_like")
        report = runner.run()
        # proxy: app ≈ 42ms, transport ≈ 8ms, diff ≈ 34ms
        assert report.app_transport_diff_ms > 30.0

    def test_invalid_profile_raises(self):
        with pytest.raises(ValueError, match="Unknown profile"):
            MockRTTRunner(profile="invalid")

    def test_measurements_populated(self):
        runner = MockRTTRunner(profile="direct")
        report = runner.run()
        assert len(report.measurements) == 2
        layers = sorted(m.layer for m in report.measurements)
        assert layers == ["application", "transport"]

    def test_timing_stability_computed(self):
        runner = MockRTTRunner(profile="direct")
        report = runner.run()
        assert report.timing_stability_score is not None
        assert 0.0 <= report.timing_stability_score <= 1.0

    def test_raw_contains_runner_info(self):
        runner = MockRTTRunner(profile="proxy_like", seed=123)
        report = runner.run()
        assert report.raw["runner"] == "MockRTTRunner"
        assert report.raw["profile"] == "proxy_like"
        assert report.raw["seed"] == 123

    def test_reproducible_with_seed(self):
        r1 = MockRTTRunner(profile="direct", seed=42).run()
        r2 = MockRTTRunner(profile="direct", seed=42).run()
        assert r1.app_transport_diff_ms == r2.app_transport_diff_ms
        assert r1.risk_score == r2.risk_score


class TestLocalTCPRTTRunnerSafety:
    """Test LocalTCPRTTRunner local-only enforcement."""

    def test_accepts_localhost(self):
        runner = LocalTCPRTTRunner(host="127.0.0.1", port=9000)
        assert runner.host == "127.0.0.1"

    def test_accepts_localhost_ipv6(self):
        runner = LocalTCPRTTRunner(host="::1", port=9000)
        assert runner.host == "::1"

    def test_accepts_localhost_name(self):
        runner = LocalTCPRTTRunner(host="localhost", port=9000)
        assert runner.host == "localhost"

    def test_rejects_remote_host(self):
        with pytest.raises(ValueError, match="non-local"):
            LocalTCPRTTRunner(host="192.168.1.1", port=9000)

    def test_rejects_external_domain(self):
        with pytest.raises(ValueError, match="non-local"):
            LocalTCPRTTRunner(host="example.com", port=9000)

    def test_rejects_public_ip(self):
        with pytest.raises(ValueError, match="non-local"):
            LocalTCPRTTRunner(host="8.8.8.8", port=9000)


class TestOptionalPingRunner:
    """Test OptionalPingRunner behavior."""

    def test_missing_ping_tool_returns_insufficient_data(self, monkeypatch):
        def _ping_not_available():
            return False
        runner = OptionalPingRunner(host="127.0.0.1")
        monkeypatch.setattr(runner, "_ping_available", _ping_not_available)
        report = runner.run()
        assert report.risk_level == "insufficient_data"
        assert "ping binary not found" in " ".join(report.notes)

    def test_ping_returns_network_rtt(self, monkeypatch):
        """Simulate successful ping with parseable output."""
        def _ping_available():
            return True

        def _fake_run(cmd, capture_output, text, timeout):
            class FakeResult:
                stdout = (
                    "PING 127.0.0.1 (127.0.0.1) 56(84) bytes of data.\n"
                    "64 bytes from 127.0.0.1: icmp_seq=1 ttl=64 time=0.050 ms\n"
                    "64 bytes from 127.0.0.1: icmp_seq=2 ttl=64 time=0.045 ms\n"
                    "64 bytes from 127.0.0.1: icmp_seq=3 ttl=64 time=0.055 ms\n"
                )
                stderr = ""
                returncode = 0
            return FakeResult()

        import subprocess
        runner = OptionalPingRunner(host="127.0.0.1", count=3)
        monkeypatch.setattr(runner, "_ping_available", _ping_available)
        monkeypatch.setattr(subprocess, "run", _fake_run)

        report = runner.run()
        assert report.network_rtt_ms is not None
        assert 0.04 <= report.network_rtt_ms <= 0.06

    def test_ping_rejects_non_local(self):
        with pytest.raises(ValueError, match="non-local"):
            OptionalPingRunner(host="8.8.8.8")

    def test_ping_timeout_graceful(self, monkeypatch):
        def _ping_available():
            return True

        def _raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("ping", 5.0)

        import subprocess
        runner = OptionalPingRunner(host="127.0.0.1")
        monkeypatch.setattr(runner, "_ping_available", _ping_available)
        monkeypatch.setattr(subprocess, "run", _raise_timeout)

        report = runner.run()
        assert report.risk_level == "insufficient_data"
        assert any("ping failed" in n.lower() for n in report.notes)
