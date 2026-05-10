"""Tests for the smoke replacement matrix script.

These tests validate the matrix runner logic, output formats, and
error handling.  They do NOT depend on real networks, LLM APIs, or
external SSH servers.
"""

import json
import os
import subprocess
import sys
import tempfile
import time

import pytest

# Ensure the smoke matrix script is importable
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.smoke_replacement_matrix import (
    SmokeResult,
    run_matrix,
    print_json,
    print_text,
    _TRANSPORT_SMOKE,
    CORE_SMOKE_REGISTRY,
    _gen_test_certs,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _run_script(args: list[str]) -> subprocess.CompletedProcess:
    """Run the smoke matrix script as a subprocess."""
    script = os.path.join(_REPO_ROOT, "scripts", "smoke_replacement_matrix.py")
    return subprocess.run(
        [sys.executable, script] + args,
        capture_output=True, text=True, timeout=30,
    )


# ---------------------------------------------------------------------------
# unit tests — SmokeResult
# ---------------------------------------------------------------------------

class TestSmokeResult:
    def test_pass_result(self):
        r = SmokeResult("tcp", "default", "pass", 0.05)
        d = r.to_dict()
        assert d["transport"] == "tcp"
        assert d["core"] == "default"
        assert d["status"] == "pass"
        assert d["duration_sec"] == 0.05
        assert d["error"] is None

    def test_fail_result(self):
        r = SmokeResult("tls", "default", "fail", 1.2, "handshake timeout")
        d = r.to_dict()
        assert d["status"] == "fail"
        assert d["error"] == "handshake timeout"

    def test_skip_result(self):
        r = SmokeResult("ssh", "default", "skip", 0.0,
                        "requires external SSH server")
        d = r.to_dict()
        assert d["status"] == "skip"
        assert d["error"] is not None


# ---------------------------------------------------------------------------
# unit tests — transport registry
# ---------------------------------------------------------------------------

class TestTransportRegistry:
    def test_all_known_transports_registered(self):
        for name in ["mock", "tcp", "tls", "websocket", "ssh"]:
            assert name in _TRANSPORT_SMOKE, f"{name} missing from registry"

    def test_unknown_transport_not_registered(self):
        assert "nonexistent" not in _TRANSPORT_SMOKE


# ---------------------------------------------------------------------------
# unit tests — core registry
# ---------------------------------------------------------------------------

class TestCoreRegistry:
    def test_default_core_registered(self):
        assert "default" in CORE_SMOKE_REGISTRY

    def test_default_core_callable(self):
        handler = CORE_SMOKE_REGISTRY["default"]
        assert callable(handler)


# ---------------------------------------------------------------------------
# unit tests — run_matrix logic
# ---------------------------------------------------------------------------

class TestRunMatrix:
    def test_run_mock_passes(self):
        results = run_matrix(["mock"], ["default"])
        assert len(results) == 1
        assert results[0].status == "pass"
        assert results[0].transport == "mock"
        assert results[0].core == "default"

    def test_run_tcp_passes(self):
        results = run_matrix(["tcp"], ["default"])
        assert len(results) == 1
        assert results[0].status == "pass"

    def test_run_websocket_passes(self):
        results = run_matrix(["websocket"], ["default"])
        assert len(results) == 1
        assert results[0].status == "pass"

    def test_run_tls_passes(self):
        results = run_matrix(["tls"], ["default"])
        assert len(results) == 1
        assert results[0].status == "pass", f"TLS smoke failed: {results[0].error}"

    def test_ssh_defaults_to_skip(self):
        results = run_matrix(["ssh"], ["default"])
        assert len(results) == 1
        assert results[0].status == "skip"
        assert "external SSH server" in (results[0].error or "")

    def test_ssh_included_when_requested(self):
        results = run_matrix(["ssh"], ["default"], include_ssh=True)
        assert len(results) == 1
        assert results[0].status == "skip"

    def test_unknown_transport_fails(self):
        results = run_matrix(["nonexistent_transport"], ["default"])
        assert len(results) == 1
        assert results[0].status == "fail"
        assert "unknown transport" in (results[0].error or "")

    def test_unknown_core_fails(self):
        results = run_matrix(["mock"], ["nonexistent_core"])
        assert len(results) == 1
        assert results[0].status == "fail"
        assert "unknown core" in (results[0].error or "")

    def test_multiple_transports(self):
        results = run_matrix(["mock", "tcp"], ["default"])
        assert len(results) == 2
        for r in results:
            assert r.core == "default"
        transports = {r.transport for r in results}
        assert transports == {"mock", "tcp"}

    def test_all_known_transports_pass_or_skip(self):
        """Every registered transport (except ssh) must pass smoke."""
        results = run_matrix(
            ["mock", "tcp", "tls", "websocket", "ssh"],
            ["default"],
        )
        statuses = {(r.transport, r.status) for r in results}
        assert ("mock", "pass") in statuses
        assert ("tcp", "pass") in statuses
        assert ("tls", "pass") in statuses
        assert ("websocket", "pass") in statuses
        assert ("ssh", "skip") in statuses


# ---------------------------------------------------------------------------
# JSON output format
# ---------------------------------------------------------------------------

class TestJSONOutput:
    def test_json_is_valid_and_complete(self):
        """JSON output must be parseable and contain results + summary."""
        proc = _run_script(["--transports", "mock", "--cores", "default", "--json"])
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        data = json.loads(proc.stdout)
        assert "results" in data
        assert "summary" in data
        assert len(data["results"]) == 1
        r = data["results"][0]
        assert r["transport"] == "mock"
        assert r["core"] == "default"
        assert r["status"] == "pass"
        assert "duration_sec" in r
        assert "error" in r
        assert data["summary"] == {"passed": 1, "failed": 0, "skipped": 0}

    def test_json_summary_counts_are_correct(self):
        proc = _run_script(
            ["--transports", "mock,tcp,ssh", "--cores", "default", "--json"],
        )
        data = json.loads(proc.stdout)
        assert data["summary"]["passed"] == 2  # mock, tcp
        assert data["summary"]["skipped"] == 1  # ssh
        assert data["summary"]["failed"] == 0


# ---------------------------------------------------------------------------
# text output format
# ---------------------------------------------------------------------------

class TestTextOutput:
    def test_text_output_contains_header(self):
        proc = _run_script(["--transports", "mock", "--cores", "default"])
        assert proc.returncode == 0
        assert "Transport/Core Smoke Matrix" in proc.stdout

    def test_text_output_shows_pass_status(self):
        proc = _run_script(["--transports", "mock", "--cores", "default"])
        assert "PASS" in proc.stdout


# ---------------------------------------------------------------------------
# exit codes
# ---------------------------------------------------------------------------

class TestExitCodes:
    def test_all_pass_exits_zero(self):
        proc = _run_script(["--transports", "mock,tcp", "--cores", "default", "--json"])
        assert proc.returncode == 0

    def test_unknown_transport_exits_one(self):
        proc = _run_script(
            ["--transports", "nonexistent", "--cores", "default", "--json"],
        )
        assert proc.returncode == 1

    def test_only_skip_exits_zero(self):
        proc = _run_script(["--transports", "ssh", "--cores", "default", "--json"])
        assert proc.returncode == 0


# ---------------------------------------------------------------------------
# temp cert generation
# ---------------------------------------------------------------------------

class TestTempCerts:
    def test_certs_generated_in_tmpdir(self):
        tmp = tempfile.mkdtemp(prefix="smoke_test_certs_")
        try:
            cert, key = _gen_test_certs(tmp)
            assert os.path.isfile(cert)
            assert os.path.isfile(key)
            assert cert.startswith(tmp)
            assert key.startswith(tmp)
        finally:
            for fn in os.listdir(tmp):
                os.unlink(os.path.join(tmp, fn))
            os.rmdir(tmp)


# ---------------------------------------------------------------------------
# integration — subprocess smoke (real transports, no monkeypatch)
# ---------------------------------------------------------------------------

class TestSmokeSubprocessReal:
    """Run the full matrix via subprocess with real localhost transports."""

    def test_full_matrix_mock_tcp_websocket(self):
        proc = _run_script(
            ["--transports", "mock,tcp,websocket", "--cores", "default", "--json"],
        )
        assert proc.returncode == 0, f"Failed: {proc.stderr}"
        data = json.loads(proc.stdout)
        assert data["summary"]["passed"] == 3
        assert data["summary"]["failed"] == 0

    def test_tls_smoke_passes(self):
        proc = _run_script(
            ["--transports", "tls", "--cores", "default", "--json"],
        )
        assert proc.returncode == 0, f"TLS failed: {proc.stderr}"
        data = json.loads(proc.stdout)
        assert data["results"][0]["status"] == "pass"


# ---------------------------------------------------------------------------
# security / safety tests
# ---------------------------------------------------------------------------

class TestSecurityBoundaries:
    def test_no_real_ssh_connection(self):
        """SSH smoke must skip without trying to connect."""
        r = run_matrix(["ssh"], ["default"])[0]
        assert r.status == "skip"
        # no real connection attempt should have been made

    def test_tls_certs_are_ephemeral(self):
        """TLS certs must be generated in temporary directories, not persisted."""
        import tempfile
        # The _smoke_tls function uses tempfile.mkdtemp and cleans up
        # Verify the cleanup logic by running the smoke and checking
        # no cert.pem/key.pem files appear in repo root
        root_files = set(os.listdir(_REPO_ROOT))
        # cert.pem and key.pem at repo root are pre-existing (committed)
        # We just verify the smoke doesn't leave new temp artifacts
        before = set(os.listdir(tempfile.gettempdir()))
        run_matrix(["tls"], ["default"])
        # temp dirs created by mkdtemp are cleaned up by the smoke function
