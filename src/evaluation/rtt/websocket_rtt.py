"""Real local WebSocket echo RTT measurement.

Provides application-layer RTT measurement via a WebSocket echo endpoint.
The target server must echo back every text message along with a nonce so
the client can match requests to responses.

SECURITY: Only localhost targets are allowed. No third-party scanning.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .rtt_measurements import RTTMeasurement, summarize_samples


# ---------------------------------------------------------------------------
# Optional websockets import
# ---------------------------------------------------------------------------

_WEBSOCKETS_AVAILABLE = False
_websockets: Any = None

try:
    import websockets as _ws
    _websockets = _ws
    _WEBSOCKETS_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Host validation (duplicated from rtt_runner to keep this module self-contained)
# ---------------------------------------------------------------------------


def _is_local_host(host: str) -> bool:
    """Check whether host is a loopback address."""
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    if host.startswith("127."):
        try:
            parts = host.split(".")
            if len(parts) == 4 and all(p.isdigit() for p in parts):
                return True
        except Exception:
            pass
    return False


# ---------------------------------------------------------------------------
# Configuration and result types
# ---------------------------------------------------------------------------


@dataclass
class WebSocketRTTConfig:
    """Configuration for a WebSocket echo RTT measurement.

    Attributes:
        host: Target host (must be localhost).
        port: Target port.
        path: WebSocket path (e.g. "/rtt").
        sample_count: Number of echo round-trips to perform.
        timeout_s: Per-connection and per-message timeout in seconds.
        local_only: Reject non-localhost targets (always True in production).
        use_nonce: Include a UUID nonce in each echo message for request/response matching.
    """

    host: str = "127.0.0.1"
    port: int = 8765
    path: str = "/rtt"
    sample_count: int = 10
    timeout_s: float = 2.0
    local_only: bool = True
    use_nonce: bool = True

    def validate(self) -> None:
        if self.local_only and not _is_local_host(self.host):
            raise ValueError(
                f"WebSocketRTT rejects non-local host: {self.host!r}. "
                f"No third-party scanning permitted."
            )
        if self.sample_count < 1:
            raise ValueError(
                f"sample_count must be >= 1, got {self.sample_count}"
            )
        if self.port < 1 or self.port > 65535:
            raise ValueError(f"port must be in 1-65535, got {self.port}")
        if self.timeout_s <= 0:
            raise ValueError(f"timeout_s must be > 0, got {self.timeout_s}")


@dataclass
class WebSocketRTTResult:
    """Result of a WebSocket echo RTT measurement.

    Attributes:
        connected: Whether the WebSocket connection succeeded.
        samples_ms: Collected RTT samples in milliseconds.
        min_ms, median_ms, avg_ms, max_ms: Summary statistics.
        sample_count: Number of valid samples collected.
        error: Error message if the measurement failed.
        notes: Per-measurement annotations.
    """

    connected: bool = False
    samples_ms: list[float] = field(default_factory=list)
    min_ms: float | None = None
    median_ms: float | None = None
    avg_ms: float | None = None
    max_ms: float | None = None
    sample_count: int = 0
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_rtt_measurement(self) -> RTTMeasurement:
        """Convert to an application-layer RTTMeasurement."""
        notes = list(self.notes)
        if self.error:
            notes.append(f"error: {self.error}")

        return summarize_samples(
            name="app_echo_ws",
            layer="application",
            samples_ms=self.samples_ms,
            notes=notes,
        )


# ---------------------------------------------------------------------------
# Core measurement logic
# ---------------------------------------------------------------------------


def measure_websocket_rtt(config: WebSocketRTTConfig | None = None) -> WebSocketRTTResult:
    """Measure application-layer RTT via WebSocket echo.

    Connects to ws://host:port/path, sends sample_count echo messages each
    carrying a UUID nonce, and measures per-message round-trip time.

    Returns a WebSocketRTTResult.  Does NOT raise on connection or timeout
    errors — failures are recorded in result.error and result.notes.

    If the websockets library is not installed, returns a result with
    error set immediately.
    """
    if not _WEBSOCKETS_AVAILABLE:
        return WebSocketRTTResult(
            connected=False,
            error="websockets library not available — install with: pip install websockets",
            notes=["websockets library missing"],
        )

    if config is None:
        config = WebSocketRTTConfig()

    try:
        config.validate()
    except ValueError as e:
        return WebSocketRTTResult(
            connected=False,
            error=str(e),
            notes=["config validation failed"],
        )

    url = f"ws://{config.host}:{config.port}{config.path}"

    try:
        return asyncio.run(_async_measure(config, url))
    except Exception as e:
        return WebSocketRTTResult(
            connected=False,
            error=f"measurement failed: {e}",
            notes=[f"target={url}"],
        )


async def _async_measure(config: WebSocketRTTConfig, url: str) -> WebSocketRTTResult:
    """Async core of the WebSocket RTT measurement."""
    samples: list[float] = []
    notes: list[str] = [f"target={url}", f"sample_count={config.sample_count}"]

    try:
        async with _websockets.connect(
            url,
            ping_interval=None,
            close_timeout=config.timeout_s,
        ) as ws:
            connected = True

            for i in range(config.sample_count):
                nonce = uuid.uuid4().hex if config.use_nonce else str(i)
                message = nonce

                start = time.perf_counter()
                await ws.send(message)
                try:
                    reply = await asyncio.wait_for(ws.recv(), timeout=config.timeout_s)
                except asyncio.TimeoutError:
                    notes.append(f"sample {i}: recv timeout after {config.timeout_s}s")
                    continue

                elapsed_ms = (time.perf_counter() - start) * 1000.0

                if config.use_nonce and reply != nonce:
                    notes.append(
                        f"sample {i}: nonce mismatch (sent={nonce[:8]}..., "
                        f"got={reply[:8]}...)"
                    )
                    continue

                samples.append(round(elapsed_ms, 3))

    except asyncio.TimeoutError:
        return WebSocketRTTResult(
            connected=False,
            error=f"connection timeout to {url}",
            notes=notes,
        )
    except Exception as e:
        return WebSocketRTTResult(
            connected=False,
            error=f"connection failed: {e}",
            notes=notes,
        )

    if not samples:
        notes.append("no valid samples collected")
        return WebSocketRTTResult(
            connected=True,
            sample_count=0,
            error="no valid samples collected",
            notes=notes,
        )

    sorted_samples = sorted(samples)
    n = len(sorted_samples)
    median = (
        sorted_samples[n // 2]
        if n % 2 == 1
        else (sorted_samples[n // 2 - 1] + sorted_samples[n // 2]) / 2.0
    )

    return WebSocketRTTResult(
        connected=True,
        samples_ms=samples,
        min_ms=round(min(samples), 3),
        median_ms=round(median, 3),
        avg_ms=round(sum(samples) / n, 3),
        max_ms=round(max(samples), 3),
        sample_count=n,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Async API (for callers already in an event loop)
# ---------------------------------------------------------------------------


async def async_measure_websocket_rtt(
    config: WebSocketRTTConfig | None = None,
) -> WebSocketRTTResult:
    """Async variant of measure_websocket_rtt for use within an existing event loop.

    Unlike measure_websocket_rtt, this does NOT call asyncio.run() — it can
    be awaited directly.
    """
    if not _WEBSOCKETS_AVAILABLE:
        return WebSocketRTTResult(
            connected=False,
            error="websockets library not available",
            notes=["websockets library missing"],
        )

    if config is None:
        config = WebSocketRTTConfig()

    try:
        config.validate()
    except ValueError as e:
        return WebSocketRTTResult(
            connected=False,
            error=str(e),
            notes=["config validation failed"],
        )

    url = f"ws://{config.host}:{config.port}{config.path}"
    return await _async_measure(config, url)


# ---------------------------------------------------------------------------
# Local echo server (for testing)
# ---------------------------------------------------------------------------


async def _echo_handler(websocket) -> None:
    """Simple echo handler: receive text message, send it back.

    Used by create_local_echo_server() and test fixtures.
    """
    async for message in websocket:
        await websocket.send(message)


async def create_local_echo_server(
    host: str = "127.0.0.1",
    port: int = 0,
) -> tuple[asyncio.AbstractServer, str, int]:
    """Start a local WebSocket echo server for testing.

    Args:
        host: Bind address (must be localhost).
        port: Port number (0 = OS picks a free port).

    Returns:
        Tuple of (server, host, actual_port).

    Raises:
        RuntimeError: If websockets is not available.
    """
    if not _WEBSOCKETS_AVAILABLE:
        raise RuntimeError("websockets library not available")

    if not _is_local_host(host):
        raise ValueError(f"Echo server only binds localhost, got {host!r}")

    server = await _websockets.serve(
        _echo_handler,
        host,
        port,
        ping_interval=None,
    )

    # Retrieve the actual port if port=0 was passed
    actual_port: int = port
    for s in server.sockets:
        sockname = s.getsockname()
        if len(sockname) >= 2:
            actual_port = sockname[1]
            break

    return server, host, actual_port
