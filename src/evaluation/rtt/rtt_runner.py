"""RTT runners — mock, local TCP, and optional ping.

Provides:
- MockRTTRunner: deterministic RTT samples for testing (direct / proxy_like profiles)
- LocalTCPRTTRunner: TCP connect timing for transport RTT (localhost only)
- OptionalPingRunner: ICMP ping wrapper (best-effort, no root required)
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from typing import Any

from .rtt_measurements import (
    CrossLayerRTTReport,
    RTTMeasurement,
    compute_rtt_diff,
    score_cross_layer_rtt,
    summarize_samples,
    timing_stability_score,
)


# ---------------------------------------------------------------------------
# Host validation
# ---------------------------------------------------------------------------


def _is_local_host(host: str) -> bool:
    """Check whether host is a loopback address."""
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    # Also match 127.x.x.x range (some systems resolve localhost to different 127/8)
    if host.startswith("127."):
        try:
            parts = host.split(".")
            if len(parts) == 4 and all(p.isdigit() for p in parts):
                return True
        except Exception:
            pass
    return False


# ---------------------------------------------------------------------------
# Mock RTT Runner
# ---------------------------------------------------------------------------

# Preset profiles: sample values for direct and proxy-like connections
DIRECT_SAMPLES = {
    "app_echo": [1.2, 1.0, 1.4, 1.1, 1.3, 0.9, 1.2, 1.0, 1.5, 1.1],
    "tcp_connect": [0.5, 0.4, 0.6, 0.5, 0.7, 0.4, 0.5, 0.6, 0.4, 0.5],
}

PROXY_LIKE_SAMPLES = {
    "app_echo": [42.0, 45.0, 38.0, 41.0, 44.0, 47.0, 39.0, 43.0, 46.0, 40.0],
    "tcp_connect": [8.0, 9.0, 7.0, 8.5, 9.5, 7.5, 8.0, 9.0, 8.5, 7.0],
}


class MockRTTRunner:
    """Deterministic RTT runner for testing.

    Supports two preset profiles:
    - direct: application RTT ≈ transport RTT (low cross-layer diff)
    - proxy_like: application RTT noticeably higher than transport (high diff)
    """

    def __init__(self, profile: str = "direct", seed: int = 42):
        if profile not in ("direct", "proxy_like"):
            raise ValueError(f"Unknown profile: {profile!r}. Use 'direct' or 'proxy_like'.")
        self.profile = profile
        self.seed = seed

    def run(self, target: str = "127.0.0.1") -> CrossLayerRTTReport:
        """Run mock RTT measurements and produce a CrossLayerRTTReport."""
        samples_source = DIRECT_SAMPLES if self.profile == "direct" else PROXY_LIKE_SAMPLES

        measurements: list[RTTMeasurement] = [
            summarize_samples(
                name="app_echo",
                layer="application",
                samples_ms=samples_source["app_echo"],
                notes=[f"mock {self.profile} profile, seed={self.seed}"],
            ),
            summarize_samples(
                name="tcp_connect",
                layer="transport",
                samples_ms=samples_source["tcp_connect"],
                notes=[f"mock {self.profile} profile, seed={self.seed}"],
            ),
        ]

        app_rtt = measurements[0].avg_ms
        tcp_rtt = measurements[1].avg_ms

        diffs = compute_rtt_diff(app_rtt, tcp_rtt, None)
        stability = timing_stability_score(samples_source["app_echo"])
        risk_score, risk_level = score_cross_layer_rtt(
            diffs["app_transport_diff_ms"], stability
        )

        notes: list[str] = []
        if self.profile == "proxy_like":
            notes.append(
                f"proxy_like profile: app-transport diff={diffs['app_transport_diff_ms']}ms "
                f"— cross-layer RTT fingerprint detectable"
            )
        else:
            notes.append(
                f"direct profile: app-transport diff={diffs['app_transport_diff_ms']}ms "
                f"— low cross-layer RTT risk"
            )

        return CrossLayerRTTReport(
            target=target,
            trace_type="mock",
            application_rtt_ms=app_rtt,
            transport_rtt_ms=tcp_rtt,
            network_rtt_ms=None,
            app_transport_diff_ms=diffs["app_transport_diff_ms"],
            app_network_diff_ms=None,
            timing_stability_score=stability,
            risk_score=risk_score,
            risk_level=risk_level,
            measurements=measurements,
            notes=notes,
            raw={
                "runner": "MockRTTRunner",
                "profile": self.profile,
                "seed": self.seed,
                "samples": {k: v for k, v in samples_source.items()},
            },
        )


# ---------------------------------------------------------------------------
# Local TCP RTT Runner
# ---------------------------------------------------------------------------


class LocalTCPRTTRunner:
    """Measure transport-layer RTT via TCP connect timing.

    Only allows localhost targets. Uses non-blocking connect with timing
    for a simple transport RTT estimate. Does not require root.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9000, samples: int = 5):
        if not _is_local_host(host):
            raise ValueError(
                f"LocalTCPRTTRunner rejects non-local host: {host!r}. "
                f"No third-party scanning permitted."
            )
        self.host = host
        self.port = port
        self.samples = samples

    def _measure_one_tcp_rtt(self, timeout_s: float = 2.0) -> float | None:
        """Perform one TCP connect and measure elapsed time.

        Returns RTT in ms or None if connection fails.
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout_s)
            start = time.perf_counter()
            sock.connect((self.host, self.port))
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            sock.close()
            return elapsed_ms
        except Exception:
            return None

    def run(self, target: str | None = None) -> CrossLayerRTTReport:
        """Collect TCP RTT samples and produce a report.

        Application-layer RTT is not measured directly (would need an echo
        server or WebSocket). Set to None in the report unless an
        app_echo_ms hint is passed via the raw dict.
        """
        host = target or self.host

        tcp_samples: list[float] = []
        notes: list[str] = []

        for i in range(self.samples):
            rtt = self._measure_one_tcp_rtt()
            if rtt is not None:
                tcp_samples.append(rtt)
            if i < self.samples - 1:
                time.sleep(0.05)  # small gap between measurements

        if not tcp_samples:
            return CrossLayerRTTReport(
                target=host,
                trace_type="real",
                risk_level="insufficient_data",
                notes=["TCP connect to %s:%d failed for all samples" % (host, self.port)],
                raw={"runner": "LocalTCPRTTRunner", "samples_attempted": self.samples},
            )

        measurement = summarize_samples(
            name="tcp_connect",
            layer="transport",
            samples_ms=tcp_samples,
            notes=[f"TCP connect to {host}:{self.port}"],
        )

        tcp_rtt = measurement.avg_ms
        stability = timing_stability_score(tcp_samples)

        # Without app-layer measurement, diff is unknown
        risk_score, risk_level = score_cross_layer_rtt(None, stability)

        notes.append(
            f"transport_rtt_ms={tcp_rtt} "
            f"(no application-layer measurement; set risk_level=insufficient_data)"
        )

        return CrossLayerRTTReport(
            target=host,
            trace_type="real",
            transport_rtt_ms=tcp_rtt,
            application_rtt_ms=None,
            network_rtt_ms=None,
            app_transport_diff_ms=None,
            app_network_diff_ms=None,
            timing_stability_score=stability,
            risk_score=risk_score,
            risk_level="insufficient_data",
            measurements=[measurement],
            notes=notes,
            raw={
                "runner": "LocalTCPRTTRunner",
                "samples_attempted": self.samples,
                "samples_collected": len(tcp_samples),
            },
        )


# ---------------------------------------------------------------------------
# Optional Ping Runner
# ---------------------------------------------------------------------------


class OptionalPingRunner:
    """Best-effort ICMP ping wrapper for network-layer RTT.

    If ping is not available or fails (no root, blocked), returns a report
    with notes explaining the limitation. Unit tests MUST mock this runner.
    """

    def __init__(self, host: str = "127.0.0.1", count: int = 3, timeout_s: float = 2.0):
        if not _is_local_host(host):
            raise ValueError(
                f"OptionalPingRunner rejects non-local host: {host!r}. "
                f"No third-party scanning permitted."
            )
        self.host = host
        self.count = count
        self.timeout_s = timeout_s

    def _ping_available(self) -> bool:
        """Check if ping binary is available."""
        return (
            os.path.exists("/bin/ping") or
            os.path.exists("/usr/bin/ping") or
            os.path.exists("/sbin/ping")
        )

    def _parse_ping_output(self, output: str) -> list[float]:
        """Parse ping output for RTT values.

        Looks for lines like: time=1.23 ms
        """
        samples: list[float] = []
        for line in output.splitlines():
            if "time=" in line.lower():
                try:
                    idx = line.lower().find("time=")
                    rest = line[idx + 5:]
                    # Extract the number before " ms"
                    num_str = rest.strip().split()[0]
                    samples.append(float(num_str))
                except (ValueError, IndexError):
                    pass
        return samples

    def run(self, target: str | None = None) -> CrossLayerRTTReport:
        """Attempt to collect ICMP RTT samples via ping.

        Returns a report with network_rtt_ms if successful, or notes
        explaining why ping was unavailable.
        """
        host = target or self.host

        if not self._ping_available():
            return CrossLayerRTTReport(
                target=host,
                trace_type="real",
                risk_level="insufficient_data",
                notes=["ping binary not found — network RTT unavailable"],
                raw={"runner": "OptionalPingRunner", "error": "ping not found"},
            )

        try:
            result = subprocess.run(
                ["ping", "-c", str(self.count), "-W", str(int(self.timeout_s)), host],
                capture_output=True,
                text=True,
                timeout=self.timeout_s + 5.0,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as e:
            return CrossLayerRTTReport(
                target=host,
                trace_type="real",
                risk_level="insufficient_data",
                notes=[f"ping failed: {e}"],
                raw={"runner": "OptionalPingRunner", "error": str(e)},
            )

        samples = self._parse_ping_output(result.stdout + result.stderr)

        if not samples:
            return CrossLayerRTTReport(
                target=host,
                trace_type="real",
                risk_level="insufficient_data",
                notes=[f"ping to {host} produced no parseable RTT values (stderr: {result.stderr.strip()})"],
                raw={
                    "runner": "OptionalPingRunner",
                    "returncode": result.returncode,
                    "stdout": result.stdout[:500],
                    "stderr": result.stderr[:500],
                },
            )

        measurement = summarize_samples(
            name="icmp_ping",
            layer="network",
            samples_ms=samples,
            notes=[f"ICMP ping to {host}, count={self.count}"],
        )

        return CrossLayerRTTReport(
            target=host,
            trace_type="real",
            network_rtt_ms=measurement.avg_ms,
            application_rtt_ms=None,
            transport_rtt_ms=None,
            app_transport_diff_ms=None,
            app_network_diff_ms=None,
            timing_stability_score=timing_stability_score(samples),
            risk_score=0.0,
            risk_level="insufficient_data",
            measurements=[measurement],
            notes=[f"network_rtt_ms={measurement.avg_ms} (ping only, no app/transport reference)"],
            raw={
                "runner": "OptionalPingRunner",
                "samples_collected": measurement.sample_count,
            },
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_rtt_runner(
    mode: str = "mock",
    profile: str = "direct",
    host: str = "127.0.0.1",
    port: int = 9000,
    seed: int = 42,
    **kwargs: Any,
) -> MockRTTRunner | LocalTCPRTTRunner:
    """Create an RTT runner by mode.

    Args:
        mode: "mock", "tcp", or "ping".
        profile: For mock mode, "direct" or "proxy_like".
        host, port: For TCP mode.

    Returns:
        An RTT runner instance (MockRTTRunner, LocalTCPRTTRunner, or
        OptionalPingRunner).
    """
    if mode == "mock":
        return MockRTTRunner(profile=profile, seed=seed)
    elif mode in ("tcp", "transport"):
        return LocalTCPRTTRunner(host=host, port=port, **kwargs)
    elif mode == "ping":
        return OptionalPingRunner(host=host, **kwargs)
    else:
        raise ValueError(f"Unknown RTT runner mode: {mode!r}")
