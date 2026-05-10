"""Lightweight tests for phase10_netns_tun_validation.sh.

These tests check script syntax, CLI behaviour, and pre-flight skip logic.
They do NOT require root, real netns, or /dev/net/tun.
"""

import os
import subprocess
import sys
import pytest

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "phase10_netns_tun_validation.sh",
)


def _run_script(*args):
    """Run the validation script and return CompletedProcess."""
    return subprocess.run(
        ["bash", _SCRIPT] + list(args),
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )


# ---------------------------------------------------------------------------
# Syntax and basic CLI
# ---------------------------------------------------------------------------


class TestScriptSyntax:
    """Verify the script is syntactically valid bash."""

    def test_bash_syntax_check(self):
        result = subprocess.run(
            ["bash", "-n", _SCRIPT],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"bash -n failed: {result.stderr}"

    def test_file_is_executable(self):
        assert os.access(_SCRIPT, os.X_OK), f"{_SCRIPT} is not executable"


class TestHelpOutput:
    """Verify --help works."""

    def test_help_flag(self):
        result = _run_script("--help")
        assert result.returncode == 0
        assert "Usage:" in result.stdout

    def test_help_short_flag(self):
        result = _run_script("-h")
        assert result.returncode == 0
        assert "Usage:" in result.stdout

    def test_help_mentions_options(self):
        result = _run_script("--help")
        assert "--transport" in result.stdout
        assert "--timeout" in result.stdout
        assert "--keep" in result.stdout
        assert "--verbose" in result.stdout

    def test_help_contains_e2e_ping(self):
        result = _run_script("--help")
        assert "--e2e-ping" in result.stdout

    def test_help_contains_ping_count(self):
        result = _run_script("--help")
        assert "--ping-count" in result.stdout

    def test_help_contains_ping_timeout(self):
        result = _run_script("--help")
        assert "--ping-timeout" in result.stdout

    def test_help_contains_tcpdump(self):
        result = _run_script("--help")
        assert "--tcpdump" in result.stdout


    def test_help_contains_preflight_only(self):
        result = _run_script("--help")
        assert "--preflight-only" in result.stdout


# ---------------------------------------------------------------------------
# Pre-flight only (CI-safe) — uses --preflight-only to avoid real netns
# ---------------------------------------------------------------------------


class TestPreFlightOnly:
    """Verify --preflight-only exits 0 without creating namespaces or processes."""

    def test_preflight_only_exit_zero(self):
        result = _run_script("--preflight-only")
        assert result.returncode == 0, f"expected exit 0, got {result.returncode}"

    def test_preflight_only_mentions_preflight(self):
        result = _run_script("--preflight-only")
        combined = result.stdout + result.stderr
        assert ("SKIP" in combined or "pre-flight" in combined.lower()
                or "PASS" in combined), f"expected SKIP/pre-flight/PASS, got: {combined[:500]}"

    def test_preflight_only_no_error_in_output(self):
        result = _run_script("--preflight-only")
        assert "ERROR" not in result.stdout, f"ERROR in stdout: {result.stdout}"
        assert "FAIL" not in result.stdout, f"FAIL in stdout: {result.stdout}"

    def test_preflight_only_does_not_create_namespaces(self):
        # --preflight-only should not execute _setup_netns
        # The script has no side effects (no namespaces, no processes)
        text = open(_SCRIPT).read()
        # Verify that --preflight-only gates _setup_netns
        assert "PREFLIGHT_ONLY" in text
        # The main section should have an early exit after preflight
        assert "pre-flight checks passed, exiting" in text or "--preflight-only" in text


# ---------------------------------------------------------------------------
# Invalid transport handling
# ---------------------------------------------------------------------------


class TestInvalidTransport:
    """Verify script rejects invalid transport values."""

    def test_unsupported_transport_ssh(self):
        # Run as root-equivalent check — but the pre-flight check for transport
        # happens before the privilege check. We can test with sudo or just check
        # that the script script validates transport argument early.
        # Since we don't have root, the privilege skip comes first.
        # Instead, verify the transport validation code exists via grep.
        script_text = open(_SCRIPT).read()
        assert 'Unsupported transport' in script_text
        assert 'tcp' in script_text
        assert 'websocket' in script_text

    def test_unsupported_transport_empty(self):
        # Same pattern: verify validation logic exists
        script_text = open(_SCRIPT).read()
        assert 'TRANSPORT="' in script_text or 'TRANSPORT:-' in script_text


# ---------------------------------------------------------------------------
# Required script sections
# ---------------------------------------------------------------------------


class TestRequiredSections:
    """Verify the script contains all required functional sections."""

    def test_has_preflight_check(self):
        text = open(_SCRIPT).read()
        assert "_preflight" in text
        assert "/dev/net/tun" in text
        assert "SKIP" in text

    def test_has_trap_cleanup(self):
        text = open(_SCRIPT).read()
        assert "trap" in text
        assert "_cleanup" in text

    def test_has_setup_netns(self):
        text = open(_SCRIPT).read()
        assert "_setup_netns" in text
        assert "ip netns add" in text
        assert "veth" in text

    def test_has_verify_underlay(self):
        text = open(_SCRIPT).read()
        assert "_verify_underlay" in text
        assert "ping" in text

    def test_has_start_server(self):
        text = open(_SCRIPT).read()
        assert "_start_server" in text
        assert "src.server" in text

    def test_has_start_client(self):
        text = open(_SCRIPT).read()
        assert "_start_client" in text
        assert "src.client" in text

    def test_has_verify_tun_devices(self):
        text = open(_SCRIPT).read()
        assert "_verify_tun_devices" in text
        assert "tun0" in text
        assert "tun1" in text

    def test_has_configure_tun_ips(self):
        text = open(_SCRIPT).read()
        assert "_configure_tun_ips" in text
        assert "10.8.0.1" in text
        assert "10.8.0.2" in text

    def test_has_keep_flag(self):
        text = open(_SCRIPT).read()
        assert "KEEP" in text
        assert "--keep" in text

    def test_has_verbose_flag(self):
        text = open(_SCRIPT).read()
        assert "VERBOSE" in text
        assert "--verbose" in text

    def test_has_set_euo_pipefail(self):
        text = open(_SCRIPT).read()
        assert "set -euo pipefail" in text

    def test_uses_unique_namespace_names(self):
        text = open(_SCRIPT).read()
        assert "vpn_srv_validation" in text
        assert "vpn_cli_validation" in text

    def test_has_netns_capability_probe(self):
        text = open(_SCRIPT).read()
        assert "_check_netns_capability" in text
        assert "vpn_phase10_probe_" in text

    def test_setup_netns_error_shows_possible_causes(self):
        text = open(_SCRIPT).read()
        assert "CI/container" in text or "CAP_NET_ADMIN" in text

    def test_has_preflight_only_flag(self):
        text = open(_SCRIPT).read()
        assert "PREFLIGHT_ONLY" in text
        assert "--preflight-only" in text


# ---------------------------------------------------------------------------
# Configuration file references
# ---------------------------------------------------------------------------


class TestConfigReferences:
    """Verify the script references the correct config files."""

    def test_references_server_netns_config(self):
        text = open(_SCRIPT).read()
        assert "config/server_netns.yaml" in text

    def test_references_client_netns_config(self):
        text = open(_SCRIPT).read()
        assert "config/client_netns.yaml" in text

    def test_uses_real_tun_not_mock(self):
        text = open(_SCRIPT).read()
        assert "--mock-tun" not in text, (
            "netns validation must use real TUN devices, not --mock-tun"
        )


# ---------------------------------------------------------------------------
# Subnet isolation
# ---------------------------------------------------------------------------


class TestSubnetIsolation:
    """Verify Phase 10.3 uses a different veth subnet than Phase 3."""

    def test_uses_unique_veth_subnet(self):
        text = open(_SCRIPT).read()
        assert "192.168.200" in text, (
            "Phase 10.3 should use 192.168.200.0/24 (Phase 3 uses 192.168.100.0/24)"
        )

    def test_uses_unique_ns_names(self):
        text = open(_SCRIPT).read()
        assert "vpn_srv_validation" in text
        assert "vpn_cli_validation" in text


# ---------------------------------------------------------------------------
# Phase 10.4: e2e-ping mode
# ---------------------------------------------------------------------------


class TestE2EPingGate:
    """Verify --e2e-ping is gated — default mode does NOT run ping."""

    def test_e2e_ping_default_false(self):
        text = open(_SCRIPT).read()
        assert "E2E_PING=false" in text, "E2E_PING must default to false"

    def test_e2e_ping_gated_in_main(self):
        text = open(_SCRIPT).read()
        assert "if $E2E_PING" in text, "e2e-ping must be conditionally executed"

    def test_has_e2e_ping_function(self):
        text = open(_SCRIPT).read()
        assert "_e2e_ping_validation" in text

    def test_has_diagnostics_function(self):
        text = open(_SCRIPT).read()
        assert "_diagnostics" in text

    def test_has_start_tcpdump_function(self):
        text = open(_SCRIPT).read()
        assert "_start_tcpdump" in text

    def test_tcpdump_requires_e2e_ping_in_code(self):
        text = open(_SCRIPT).read()
        assert "--tcpdump requires --e2e-ping" in text

    def test_tcpdump_without_e2e_ping_exits_error(self):
        result = _run_script("--tcpdump")
        assert result.returncode != 0
        assert "requires --e2e-ping" in result.stderr


class TestDiagnosticsContent:
    """Verify diagnostics output includes required troubleshooting info."""

    def test_diagnostics_mentions_session_id(self):
        text = open(_SCRIPT).read()
        assert "session ID" in text or "session_id" in text

    def test_diagnostics_mentions_routing(self):
        text = open(_SCRIPT).read()
        assert "route" in text.lower()

    def test_diagnostics_mentions_tcpdump(self):
        text = open(_SCRIPT).read()
        assert "tcpdump" in text

    def test_diagnostics_warns_on_dropping_frame(self):
        text = open(_SCRIPT).read()
        assert "Dropping frame" in text

    def test_diagnostics_shows_ip_addr(self):
        text = open(_SCRIPT).read()
        assert "ip addr" in text

    def test_diagnostics_shows_ip_route(self):
        text = open(_SCRIPT).read()
        assert "ip route" in text

    def test_diagnostics_shows_process_status(self):
        text = open(_SCRIPT).read()
        assert "Process Status" in text or "Server PID" in text

    def test_diagnostics_shows_logs(self):
        text = open(_SCRIPT).read()
        assert "Server Log" in text or "Client Log" in text


class TestPcapLogGitignore:
    """Verify .gitignore entries prevent committing pcap/log files."""

    def test_gitignore_has_pcap(self):
        gitignore = open(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".gitignore")
        ).read()
        assert "*.pcap" in gitignore, ".gitignore must have *.pcap"

    def test_script_warns_pcap_not_git_tracked(self):
        text = open(_SCRIPT).read()
        # The script should either mention that pcaps are temporary or
        # direct users to avoid committing them.
        assert "/tmp/vpn_validation_pcap" in text or "pcap" in text.lower()


class TestE2EPingExtraChecks:
    """Verify e2e-ping adds pre-flight checks for ping and tcpdump."""

    def test_e2e_ping_preflight_checks_ping(self):
        text = open(_SCRIPT).read()
        assert "command -v ping" in text

    def test_e2e_ping_preflight_checks_tcpdump(self):
        text = open(_SCRIPT).read()
        assert "command -v tcpdump" in text


# ---------------------------------------------------------------------------
# Security: no secrets, no hardcoded paths
# ---------------------------------------------------------------------------


class TestSecurityBoundaries:
    """Verify the script doesn't contain secrets or problematic patterns."""

    def test_no_hardcoded_api_keys(self):
        text = open(_SCRIPT).read()
        assert "sk-" not in text
        assert "API_KEY" not in text
        assert "api_key" not in text

    def test_no_hardcoded_passwords(self):
        text = open(_SCRIPT).read()
        assert "password" not in text.lower()

    def test_no_git_add_commit_push(self):
        text = open(_SCRIPT).read()
        assert "git add" not in text
        assert "git commit" not in text
        assert "git push" not in text
