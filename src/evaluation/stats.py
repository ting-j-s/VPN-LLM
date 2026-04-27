"""Evaluation and Statistics Module.

Provides metrics collection and analysis for tunnel performance evaluation.
"""

import time
from dataclasses import dataclass, field
from typing import Optional

from ..common.logger import setup_logger


logger = setup_logger(__name__)


@dataclass
class TransferStats:
    """Transfer statistics."""
    bytes_sent: int = 0
    bytes_received: int = 0
    packets_sent: int = 0
    packets_received: int = 0
    frames_sent: int = 0
    frames_received: int = 0
    errors: int = 0
    start_time: float = field(default_factory=time.time)


@dataclass
class LatencyStats:
    """Latency statistics."""
    min_ms: float = float("inf")
    max_ms: float = 0.0
    avg_ms: float = 0.0
    samples: int = 0
    _sum_ms: float = 0.0


class TunnelStats:
    """Collects and reports tunnel statistics.

    Tracks:
    - Data transfer volumes
    - Latency measurements
    - Error counts
    - Connection state changes
    """

    def __init__(self):
        """Initialize statistics collector."""
        self.transfer = TransferStats()
        self.latency = LatencyStats()
        self._last_latency_time: Optional[float] = None

        logger.info("Statistics collector initialized")

    def record_bytes_sent(self, num_bytes: int) -> None:
        """Record bytes sent.

        Args:
            num_bytes: Number of bytes sent.
        """
        self.transfer.bytes_sent += num_bytes
        self.transfer.packets_sent += 1

    def record_bytes_received(self, num_bytes: int) -> None:
        """Record bytes received.

        Args:
            num_bytes: Number of bytes received.
        """
        self.transfer.bytes_received += num_bytes
        self.transfer.packets_received += 1

    def record_frame_sent(self) -> None:
        """Record a frame was sent."""
        self.transfer.frames_sent += 1

    def record_frame_received(self) -> None:
        """Record a frame was received."""
        self.transfer.frames_received += 1

    def record_error(self) -> None:
        """Record an error occurrence."""
        self.transfer.errors += 1

    def record_latency(self, latency_ms: float) -> None:
        """Record a latency measurement.

        Args:
            latency_ms: Latency in milliseconds.
        """
        self.latency.min_ms = min(self.latency.min_ms, latency_ms)
        self.latency.max_ms = max(self.latency.max_ms, latency_ms)
        self.latency._sum_ms += latency_ms
        self.latency.samples += 1
        self.latency.avg_ms = self.latency._sum_ms / self.latency.samples

    def start_latency_measurement(self) -> None:
        """Start timing for latency measurement."""
        self._last_latency_time = time.time()

    def end_latency_measurement(self) -> None:
        """End timing and record latency."""
        if self._last_latency_time is not None:
            latency_ms = (time.time() - self._last_latency_time) * 1000
            self.record_latency(latency_ms)
            self._last_latency_time = None

    def get_uptime(self) -> float:
        """Get tunnel uptime in seconds.

        Returns:
            Uptime in seconds.
        """
        return time.time() - self.transfer.start_time

    def get_throughput_sent(self) -> float:
        """Get send throughput in bytes per second.

        Returns:
            Bytes per second.
        """
        uptime = self.get_uptime()
        if uptime > 0:
            return self.transfer.bytes_sent / uptime
        return 0.0

    def get_throughput_received(self) -> float:
        """Get receive throughput in bytes per second.

        Returns:
            Bytes per second.
        """
        uptime = self.get_uptime()
        if uptime > 0:
            return self.transfer.bytes_received / uptime
        return 0.0

    def report(self) -> str:
        """Generate a statistics report.

        Returns:
            Formatted report string.
        """
        uptime = self.get_uptime()
        lines = [
            "=== Tunnel Statistics ===",
            f"Uptime: {uptime:.1f} seconds",
            "",
            "Transfer:",
            f"  Sent: {self.transfer.bytes_sent} bytes ({self.transfer.packets_sent} packets, {self.transfer.frames_sent} frames)",
            f"  Received: {self.transfer.bytes_received} bytes ({self.transfer.packets_received} packets, {self.transfer.frames_received} frames)",
            f"  Send throughput: {self.get_throughput_sent():.1f} B/s",
            f"  Receive throughput: {self.get_throughput_received():.1f} B/s",
            "",
            "Latency:",
            f"  Min: {self.latency.min_ms:.2f} ms" if self.latency.samples > 0 else "  Min: N/A",
            f"  Max: {self.latency.max_ms:.2f} ms" if self.latency.samples > 0 else "  Max: N/A",
            f"  Avg: {self.latency.avg_ms:.2f} ms" if self.latency.samples > 0 else "  Avg: N/A",
            f"  Samples: {self.latency.samples}",
            "",
            f"Errors: {self.transfer.errors}",
        ]
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"TunnelStats(sent={self.transfer.bytes_sent}, "
            f"received={self.transfer.bytes_received}, "
            f"errors={self.transfer.errors})"
        )
