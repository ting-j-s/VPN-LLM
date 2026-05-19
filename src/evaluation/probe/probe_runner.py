"""Probe runners for active probe resistance testing.

Two runner types:
- MockProbeRunner: Simulates server behavior without real sockets (unit testing).
- LocalSocketProbeRunner: Connects to a real local server on 127.0.0.1 / ::1 only.

SECURITY: LocalSocketProbeRunner rejects non-localhost targets. No third-party scanning.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field

from .probe_scenarios import ProbeScenario


def _is_local_host(host: str) -> bool:
    """Check that host is strictly local."""
    return host in ("127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1")


@dataclass
class ProbeResult:
    """Result of a single probe scenario execution.

    Attributes:
        scenario: The ProbeScenario that was executed.
        connected: Whether the TCP connection succeeded.
        bytes_sent: Number of bytes sent.
        bytes_received: Number of bytes received.
        response_preview_hex: First 64 bytes of response, hex-encoded.
        close_observed: Whether the server closed the connection.
        reset_observed: Whether a TCP RST was observed (if detectable).
        timeout_observed: Whether the probe timed out waiting for response.
        elapsed_ms: Total wall-clock time for the probe attempt.
        error_type: Error category string, or empty if no error.
        notes: Additional notes.
    """

    scenario: ProbeScenario
    connected: bool = False
    bytes_sent: int = 0
    bytes_received: int = 0
    response_preview_hex: str = ""
    close_observed: bool = False
    reset_observed: bool = False
    timeout_observed: bool = False
    elapsed_ms: float = 0.0
    error_type: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario.name,
            "description": self.scenario.description,
            "connected": self.connected,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "response_preview_hex": self.response_preview_hex,
            "close_observed": self.close_observed,
            "reset_observed": self.reset_observed,
            "timeout_observed": self.timeout_observed,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "error_type": self.error_type,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Mock runner — no real network, deterministic for unit testing
# ---------------------------------------------------------------------------


class MockProbeRunner:
    """Simulates probe execution without real sockets for deterministic testing.

    Uses a simple mapping from scenario tag to simulated behavior to produce
    consistent results suitable for verifying report generation, gate
    evaluation, and countermeasure policy lookups.
    """

    def __init__(self, seed: int = 42):
        self._results: list[ProbeResult] = []

    def run_scenario(self, scenario: ProbeScenario) -> ProbeResult:
        """Simulate one probe scenario with deterministic behavior.

        Phase 6B unified policy: all malformed / unexpected input is silently
        dropped — the server never sends an application-layer response and
        never closes the connection on error.  Probes therefore see a timeout
        for every scenario (empty connection naturally times out, all other
        payloads are silently dropped and the connection stays open).

        This gives a single external error_type ("timeout") and zero close
        events, producing minimal probe_response_variance.
        """
        tags = set(scenario.tags)

        result = ProbeResult(scenario=scenario, connected=True)

        if "empty" in tags:
            result.timeout_observed = True
            result.elapsed_ms = scenario.timeout_s * 1000
            result.error_type = "timeout"
            result.notes = "mock: idle timeout (no data sent)"
        else:
            result.bytes_sent = len(scenario.payload or b"")
            result.elapsed_ms = scenario.timeout_s * 1000
            result.timeout_observed = True
            result.error_type = "timeout"
            result.notes = "mock: server silently drops malformed data, probe times out"

        self._results.append(result)
        return result

    def run_all(self, scenarios: list[ProbeScenario] | None = None) -> list[ProbeResult]:
        """Run all scenarios (or a subset)."""
        if scenarios is None:
            from .probe_scenarios import PROBE_SCENARIOS

            scenarios = PROBE_SCENARIOS
        self._results = []
        for s in scenarios:
            self.run_scenario(s)
        return self._results


# ---------------------------------------------------------------------------
# Local socket runner — real TCP connect to 127.0.0.1 / ::1 only
# ---------------------------------------------------------------------------


class LocalSocketProbeRunner:
    """Connect to a local VPN-LLM server and send probe payloads.

    SECURITY: Only allows connections to 127.0.0.1, localhost, or ::1.
    Non-local targets are rejected with a ValueError.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9000):
        if not _is_local_host(host):
            raise ValueError(
                f"LocalSocketProbeRunner only allows localhost targets, got {host!r}. "
                f"No third-party scanning permitted."
            )
        self.host = host
        self.port = port
        self._results: list[ProbeResult] = []

    def run_scenario(self, scenario: ProbeScenario) -> ProbeResult:
        """Execute one probe scenario against the local server."""
        result = ProbeResult(scenario=scenario)
        sock = None
        t0 = time.monotonic()

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(scenario.timeout_s)
            sock.connect((self.host, self.port))
            result.connected = True

            if scenario.payload is not None:
                sock.sendall(scenario.payload)
                result.bytes_sent = len(scenario.payload)

            # Try to receive any response
            try:
                response = sock.recv(4096)
                if response:
                    result.bytes_received = len(response)
                    result.response_preview_hex = response[:64].hex()
            except socket.timeout:
                result.timeout_observed = True
                result.error_type = "timeout"
            except (ConnectionResetError, BrokenPipeError):
                result.reset_observed = True
                result.error_type = "reset"
            except OSError as e:
                result.error_type = f"os_error:{e}"

            # Try to detect close by sending/recving
            try:
                sock.settimeout(0.1)
                data = sock.recv(1)
                if data == b"":
                    result.close_observed = True
            except socket.timeout:
                pass  # still open, not closed yet
            except (ConnectionResetError, BrokenPipeError):
                result.reset_observed = True
                result.close_observed = True
                result.error_type = result.error_type or "reset"
            except OSError:
                result.close_observed = True

        except (ConnectionRefusedError, ConnectionError) as e:
            result.connected = False
            result.error_type = f"connect:{e}"
        except socket.timeout:
            result.timeout_observed = True
            result.error_type = "timeout"
        except OSError as e:
            result.error_type = f"os_error:{e}"
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass

        result.elapsed_ms = (time.monotonic() - t0) * 1000.0
        self._results.append(result)
        return result

    def run_all(self, scenarios: list[ProbeScenario] | None = None) -> list[ProbeResult]:
        """Run all scenarios (or a subset)."""
        if scenarios is None:
            from .probe_scenarios import PROBE_SCENARIOS

            scenarios = PROBE_SCENARIOS
        self._results = []
        for s in scenarios:
            self.run_scenario(s)
        return self._results


def create_probe_runner(
    mock: bool = False, host: str = "127.0.0.1", port: int = 9000
) -> MockProbeRunner | LocalSocketProbeRunner:
    """Factory: create the appropriate probe runner.

    Args:
        mock: If True, create MockProbeRunner for unit testing.
        host: Target host (only localhost allowed for real runner).
        port: Target port.

    Returns:
        MockProbeRunner or LocalSocketProbeRunner.
    """
    if mock:
        return MockProbeRunner()
    return LocalSocketProbeRunner(host=host, port=port)
