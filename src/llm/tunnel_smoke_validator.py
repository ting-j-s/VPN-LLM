"""End-to-End Tunnel Smoke Validator.

Runs mock-tun client/server tunnel smoke to verify that a patched codebase
can actually establish a VPN tunnel and enter a running state. Optionally
runs Phase 9 real-TUN/netns traces when the environment supports it.

This is the final validation gate — compileall + pytest passing is NOT
sufficient for runtime/end-to-end tasks.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TunnelSmokeResult:
    """Result of a single tunnel smoke attempt."""

    transport: str
    status: str  # "pass", "fail", "skipped"
    duration_sec: float
    error: str | None = None
    server_log: str = ""
    client_log: str = ""
    server_errors: list[str] = field(default_factory=list)
    client_errors: list[str] = field(default_factory=list)
    log_dir: str = ""

    def to_dict(self) -> dict:
        return {
            "transport": self.transport,
            "status": self.status,
            "duration_sec": self.duration_sec,
            "error": self.error,
            "server_errors": self.server_errors,
            "client_errors": self.client_errors,
            "log_dir": self.log_dir,
        }

    @property
    def success(self) -> bool:
        return self.status == "pass"


@dataclass
class TunnelSmokeValidation:
    """Aggregated result of all tunnel smoke checks."""

    mock_tun_smoke: TunnelSmokeResult | None = None
    phase9_smoke: dict | None = None  # raw results.json content
    phase9_passed: bool = False
    phase9_skipped: bool = False
    phase9_skip_reason: str = ""
    real_netns_available: bool = False

    def to_dict(self) -> dict:
        d: dict = {
            "mock_tun_smoke": self.mock_tun_smoke.to_dict() if self.mock_tun_smoke else None,
            "phase9_passed": self.phase9_passed,
            "phase9_skipped": self.phase9_skipped,
            "phase9_skip_reason": self.phase9_skip_reason,
            "real_netns_available": self.real_netns_available,
        }
        return d

    @property
    def mock_tun_passed(self) -> bool:
        return self.mock_tun_smoke is not None and self.mock_tun_smoke.success

    @property
    def tunnel_validated(self) -> bool:
        """True if at least one tunnel smoke method passed."""
        return self.mock_tun_passed or self.phase9_passed


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

# Server log markers that indicate successful tunnel startup
_SERVER_RUNNING_MARKERS = [
    "Tunnel listening, running",
    "tunnel started",
]

# Client log markers that indicate successful tunnel establishment
_CLIENT_RUNNING_MARKERS = [
    "Tunnel established, running",
    "tunnel established",
]

# Error markers that indicate tunnel failure
_ERROR_MARKERS = [
    "Traceback (most recent call last)",
    "TransportError",
    "VPNError",
    "session_id mismatch",
    "Connection refused",
    "Connection reset",
    "Address already in use",
    "TUN write error",
    "Cannot assign requested address",
]


def _scan_log(log_text: str, role: str) -> list[str]:
    """Scan a log for error markers and return found issues."""
    errors: list[str] = []
    for line in log_text.splitlines():
        for marker in _ERROR_MARKERS:
            if marker in line:
                errors.append(f"[{role}] {line.strip()[:200]}")
                break
    return errors


def _has_marker(log_text: str, markers: list[str]) -> bool:
    """Check if log contains at least one of the given markers."""
    return any(m in log_text for m in markers)


# ---------------------------------------------------------------------------
# Mock-TUN tunnel smoke
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Return a free TCP port on localhost."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _find_config_dir() -> Path:
    """Find the config directory relative to the project root."""
    # Try common locations
    candidates = [
        Path("config"),
        Path(__file__).resolve().parent.parent.parent / "config",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return Path("config")


def run_mock_tun_smoke(
    transport: str = "tcp",
    timeout: float = 30.0,
    output_dir: str | None = None,
) -> TunnelSmokeResult:
    """Run a mock-TUN client/server tunnel smoke test.

    Starts server.py and client.py with --mock-tun, verifies both reach
    running state, then shuts them down cleanly.

    Args:
        transport: Transport type to use (must be a known runtime transport).
        timeout: Max seconds to wait for tunnel establishment.
        output_dir: Directory for smoke logs. Auto-created if None.

    Returns:
        TunnelSmokeResult with status, logs, and errors.
    """
    t0 = time.monotonic()

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="tunnel_smoke_")
    os.makedirs(output_dir, exist_ok=True)

    config_dir = _find_config_dir()
    server_config = config_dir / "server.yaml"
    client_config = config_dir / "client.yaml"

    if not server_config.is_file() or not client_config.is_file():
        return TunnelSmokeResult(
            transport=transport,
            status="fail",
            duration_sec=0.0,
            error=f"Missing config files: server={server_config.is_file()}, client={client_config.is_file()}",
        )

    port = _free_port()
    session_id = "00112233445566778899aabbccddeeff"

    # Prepare temporary configs with the free port
    server_conf_text = server_config.read_text()
    client_conf_text = client_config.read_text()
    server_conf_text = re.sub(r"port:\s*\d+", f"port: {port}", server_conf_text, count=1)
    client_conf_text = re.sub(r"port:\s*\d+", f"port: {port}", client_conf_text, count=1)

    server_conf_path = os.path.join(output_dir, "server_smoke.yaml")
    client_conf_path = os.path.join(output_dir, "client_smoke.yaml")
    Path(server_conf_path).write_text(server_conf_text)
    Path(client_conf_path).write_text(client_conf_text)

    server_log_path = os.path.join(output_dir, "server.log")
    client_log_path = os.path.join(output_dir, "client.log")

    server_proc = None
    client_proc = None
    server_log = ""
    client_log = ""

    try:
        # Start server
        server_cmd = [
            "python3", "-m", "src.server",
            "--config", server_conf_path,
            "--transport", transport,
            "--mock-tun",
            "--session-id", session_id,
        ]
        with open(server_log_path, "w") as s_log:
            server_proc = subprocess.Popen(
                server_cmd,
                stdout=s_log,
                stderr=subprocess.STDOUT,
            )

        # Wait for server to bind
        time.sleep(1.5)

        if server_proc.poll() is not None:
            server_log = Path(server_log_path).read_text()
            return TunnelSmokeResult(
                transport=transport,
                status="fail",
                duration_sec=round(time.monotonic() - t0, 3),
                error="Server process exited prematurely",
                server_log=server_log,
                server_errors=_scan_log(server_log, "server"),
                log_dir=output_dir,
            )

        # Start client
        client_cmd = [
            "python3", "-m", "src.client",
            "--config", client_conf_path,
            "--transport", transport,
            "--mock-tun",
            "--session-id", session_id,
        ]
        with open(client_log_path, "w") as c_log:
            client_proc = subprocess.Popen(
                client_cmd,
                stdout=c_log,
                stderr=subprocess.STDOUT,
            )

        # Wait for tunnel establishment
        deadline = time.monotonic() + timeout
        server_running = False
        client_running = False

        while time.monotonic() < deadline:
            time.sleep(0.5)

            if Path(server_log_path).exists():
                server_log = Path(server_log_path).read_text()
                if _has_marker(server_log, _SERVER_RUNNING_MARKERS):
                    server_running = True
                # Check for early crash
                if server_proc.poll() is not None and not server_running:
                    client_log = Path(client_log_path).read_text() if Path(client_log_path).exists() else ""
                    server_errors = _scan_log(server_log, "server")
                    client_errors = _scan_log(client_log, "client")
                    return TunnelSmokeResult(
                        transport=transport,
                        status="fail",
                        duration_sec=round(time.monotonic() - t0, 3),
                        error=f"Server exited before tunnel was running (rc={server_proc.returncode})",
                        server_log=server_log,
                        client_log=client_log,
                        server_errors=server_errors,
                        client_errors=client_errors,
                        log_dir=output_dir,
                    )

            if Path(client_log_path).exists():
                client_log = Path(client_log_path).read_text()
                if _has_marker(client_log, _CLIENT_RUNNING_MARKERS):
                    client_running = True
                if client_proc.poll() is not None and not client_running:
                    server_log = Path(server_log_path).read_text()
                    server_errors = _scan_log(server_log, "server")
                    client_errors = _scan_log(client_log, "client")
                    return TunnelSmokeResult(
                        transport=transport,
                        status="fail",
                        duration_sec=round(time.monotonic() - t0, 3),
                        error=f"Client exited before tunnel was running (rc={client_proc.returncode})",
                        server_log=server_log,
                        client_log=client_log,
                        server_errors=server_errors,
                        client_errors=client_errors,
                        log_dir=output_dir,
                    )

            if server_running and client_running:
                break

        # Read final logs
        server_log = Path(server_log_path).read_text() if Path(server_log_path).exists() else server_log
        client_log = Path(client_log_path).read_text() if Path(client_log_path).exists() else client_log

        if not server_running or not client_running:
            server_errors = _scan_log(server_log, "server")
            client_errors = _scan_log(client_log, "client")
            return TunnelSmokeResult(
                transport=transport,
                status="fail",
                duration_sec=round(time.monotonic() - t0, 3),
                error=f"Tunnel did not reach running state within {timeout}s "
                f"(server_running={server_running}, client_running={client_running})",
                server_log=server_log,
                client_log=client_log,
                server_errors=server_errors,
                client_errors=client_errors,
                log_dir=output_dir,
            )

        # Graceful shutdown — send SIGTERM
        client_proc.terminate()
        client_proc.wait(timeout=5)
        server_proc.terminate()
        server_proc.wait(timeout=5)

        # Re-read logs after shutdown
        server_log = Path(server_log_path).read_text() if Path(server_log_path).exists() else server_log
        client_log = Path(client_log_path).read_text() if Path(client_log_path).exists() else client_log

        # Check for errors during the run
        server_errors = _scan_log(server_log, "server")
        client_errors = _scan_log(client_log, "client")

        # Verify graceful shutdown
        graceful = "Graceful shutdown completed" in server_log

        all_errors = server_errors + client_errors
        if all_errors:
            return TunnelSmokeResult(
                transport=transport,
                status="fail",
                duration_sec=round(time.monotonic() - t0, 3),
                error=f"Tunnel ran but errors detected: {len(all_errors)} issue(s)",
                server_log=server_log,
                client_log=client_log,
                server_errors=server_errors,
                client_errors=client_errors,
                log_dir=output_dir,
            )

        return TunnelSmokeResult(
            transport=transport,
            status="pass",
            duration_sec=round(time.monotonic() - t0, 3),
            server_log=server_log,
            client_log=client_log,
            log_dir=output_dir,
        )

    except Exception as exc:
        return TunnelSmokeResult(
            transport=transport,
            status="fail",
            duration_sec=round(time.monotonic() - t0, 3),
            error=f"Tunnel smoke exception: {exc}",
            server_log=server_log,
            client_log=client_log,
            log_dir=output_dir,
        )
    finally:
        # Ensure processes are killed
        for proc in [client_proc, server_proc]:
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Phase 9 real-TUN/netns smoke
# ---------------------------------------------------------------------------


def _find_phase9_script() -> Path | None:
    """Locate the Phase 9 runner script."""
    candidates = [
        Path("scripts/run_phase9_real_trace_matrix.py"),
        Path(__file__).resolve().parent.parent.parent / "scripts" / "run_phase9_real_trace_matrix.py",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def check_phase9_env() -> tuple[bool, str]:
    """Check whether Phase 9 real-TUN/netns environment is available.

    Returns:
        (available, reason) tuple.
    """
    script = _find_phase9_script()
    if script is None:
        return False, "Phase 9 runner script not found"

    try:
        result = subprocess.run(
            ["python3", str(script), "env-check"],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout + result.stderr
        if result.returncode == 0 and "/dev/net/tun" in output:
            return True, "Phase 9 environment available"
        else:
            return False, f"Phase 9 env-check failed: {output[:300]}"
    except Exception as exc:
        return False, f"Phase 9 env-check error: {exc}"


def run_phase9_smoke(
    transport: str = "tcp",
    output_dir: str | None = None,
    timeout: float = 120.0,
) -> dict:
    """Run Phase 9 real-TUN/netns trace matrix.

    Args:
        transport: Transport to test.
        output_dir: Directory for Phase 9 outputs.
        timeout: Max seconds for the run.

    Returns:
        Dict with keys: status, results_json, error, output_dir.
    """
    script = _find_phase9_script()
    if script is None:
        return {"status": "skipped", "error": "Phase 9 script not found"}

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="phase9_smoke_")
    os.makedirs(output_dir, exist_ok=True)

    results_path = os.path.join(output_dir, "results.json")

    cmd = [
        "python3", str(script), "run",
        "--output-dir", output_dir,
        "--transports", transport,
        "--scenarios", "idle",
        "--repeat-count", "1",
        "--capture-duration", "10",
        "--min-packet-count", "1",
        "--execute",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=timeout,
        )
        if os.path.exists(results_path):
            with open(results_path) as f:
                results_data = json.load(f)
            total = results_data.get("total", 0)
            failed = results_data.get("failed", 0)
            if total >= 2 and failed == 0:
                return {
                    "status": "pass",
                    "results_json": results_data,
                    "output_dir": output_dir,
                }
            else:
                return {
                    "status": "fail",
                    "error": f"Phase 9: total={total}, failed={failed}",
                    "results_json": results_data,
                    "output_dir": output_dir,
                }
        else:
            return {
                "status": "fail",
                "error": f"Phase 9 runner produced no results.json. stdout: {result.stdout[:500]}",
                "output_dir": output_dir,
            }
    except subprocess.TimeoutExpired:
        return {"status": "fail", "error": f"Phase 9 timed out after {timeout}s"}
    except Exception as exc:
        return {"status": "fail", "error": f"Phase 9 exception: {exc}"}


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

_SKIP_TUNNEL_SMOKE_TYPES = frozenset({
    "docs_update",
    "test_addition",
})


def task_needs_tunnel_smoke(task_type: str | None, intent_contract=None) -> bool:
    """Determine whether a task type requires tunnel smoke validation.

    Default rule: every user request must pass tunnel smoke.
    Only docs_update and test_addition are exempt (unless their contract
    explicitly demands runtime/end_to_end).
    """
    if intent_contract is not None:
        if getattr(intent_contract, "runtime_required", False):
            return True
        if getattr(intent_contract, "end_to_end_required", False):
            return True
    if task_type in _SKIP_TUNNEL_SMOKE_TYPES:
        return False
    # Default: always verify the tunnel actually runs.
    return True


def run_tunnel_smoke_validation(
    task_type: str | None = None,
    target_transport: str | None = None,
    intent_contract=None,
    output_dir: str | None = None,
    run_phase9: bool | None = None,
) -> TunnelSmokeValidation:
    """Run complete tunnel smoke validation.

    Always runs mock-tun smoke with a known-good transport. Optionally
    runs Phase 9 real-TUN/netns traces if the environment supports it
    and the task type warrants it.

    Args:
        task_type: Task type from the plan.
        target_transport: Target transport name.
        intent_contract: IntentContract for additional context.
        output_dir: Base directory for smoke outputs.
        run_phase9: If True, force Phase 9; if False, skip; if None, auto-detect.

    Returns:
        TunnelSmokeValidation with mock-tun and Phase 9 results.
    """
    needs_smoke = task_needs_tunnel_smoke(task_type, intent_contract)
    if not needs_smoke:
        return TunnelSmokeValidation()

    base_dir = output_dir or tempfile.mkdtemp(prefix="tunnel_validation_")
    os.makedirs(base_dir, exist_ok=True)

    # Choose a known-good transport for smoke (not the target if it's new/skeleton)
    smoke_transport = "tcp"
    if target_transport and target_transport in ("tcp", "tls", "websocket", "ssh", "mock"):
        smoke_transport = target_transport

    # ----- Mock-TUN smoke -----
    mock_dir = os.path.join(base_dir, "mock_tun_smoke")
    mock_result = run_mock_tun_smoke(
        transport=smoke_transport,
        output_dir=mock_dir,
    )

    # ----- Phase 9 real-TUN/netns smoke -----
    phase9_result = None
    phase9_passed = False
    phase9_skipped = False
    phase9_skip_reason = ""
    real_netns_available = False

    should_run_phase9 = run_phase9
    if should_run_phase9 is None:
        # Auto-detect: run for transport/shaping/core/config tasks
        should_run_phase9 = task_type in (
            "transport_addition", "transport_change", "feature_addition",
            "mixed_feature_change", "core_change", "traffic_shaping",
            "config_change",
        )

    if should_run_phase9:
        available, reason = check_phase9_env()
        real_netns_available = available
        if available:
            phase9_dir = os.path.join(base_dir, "phase9_smoke")
            phase9_result = run_phase9_smoke(
                transport=smoke_transport,
                output_dir=phase9_dir,
            )
            phase9_passed = phase9_result.get("status") == "pass"
            phase9_skipped = False
        else:
            phase9_skipped = True
            phase9_skip_reason = reason

    return TunnelSmokeValidation(
        mock_tun_smoke=mock_result,
        phase9_smoke=phase9_result,
        phase9_passed=phase9_passed,
        phase9_skipped=phase9_skipped,
        phase9_skip_reason=phase9_skip_reason,
        real_netns_available=real_netns_available,
    )
