"""Evaluation and Statistics Module.

Collects and reports tunnel performance statistics.
Thread-safe implementation using locks.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from ..common.logger import get_logger


logger = get_logger(__name__)


@dataclass
class TunnelStats:
    """Thread-safe statistics collector for VPN tunnel.

    Tracks:
    - Frame counts (sent/received)
    - Byte counts (sent/received)
    - Frame type breakdown (DATA/HEARTBEAT)
    - Error counts
    - Connection uptime and throughput

    Usage:
        stats = TunnelStats()
        stats.record_sent(frame_type=FrameType.DATA, bytes=1400)
        stats.record_received(frame_type=FrameType.HEARTBEAT, bytes=0)
        print(stats.report())
    """

    def __init__(self):
        """Initialize statistics collector."""
        self._lock = threading.Lock()
        self._start_time = time.time()

        # Frame counts
        self._frames_sent = 0
        self._frames_received = 0

        # Byte counts
        self._bytes_sent = 0
        self._bytes_received = 0

        # Frame type breakdown
        self._data_frames_sent = 0
        self._data_frames_received = 0
        self._heartbeat_frames_sent = 0
        self._heartbeat_frames_received = 0

        # Error counts
        self._errors = 0

        logger.debug("TunnelStats initialized")

    def record_sent(
        self,
        frame_type: Optional[str] = None,
        bytes_count: int = 0,
    ) -> None:
        """Record frames sent.

        Args:
            frame_type: Type of frame ("DATA", "HEARTBEAT", etc.).
            bytes_count: Number of bytes sent.
        """
        with self._lock:
            self._frames_sent += 1
            self._bytes_sent += bytes_count

            if frame_type == "DATA":
                self._data_frames_sent += 1
            elif frame_type == "HEARTBEAT":
                self._heartbeat_frames_sent += 1

    def record_received(
        self,
        frame_type: Optional[str] = None,
        bytes_count: int = 0,
    ) -> None:
        """Record frames received.

        Args:
            frame_type: Type of frame ("DATA", "HEARTBEAT", etc.).
            bytes_count: Number of bytes received.
        """
        with self._lock:
            self._frames_received += 1
            self._bytes_received += bytes_count

            if frame_type == "DATA":
                self._data_frames_received += 1
            elif frame_type == "HEARTBEAT":
                self._heartbeat_frames_received += 1

    def record_error(self) -> None:
        """Record an error occurrence."""
        with self._lock:
            self._errors += 1

    def get_sent_frames(self) -> int:
        """Get total frames sent."""
        with self._lock:
            return self._frames_sent

    def get_received_frames(self) -> int:
        """Get total frames received."""
        with self._lock:
            return self._frames_received

    def get_sent_bytes(self) -> int:
        """Get total bytes sent."""
        with self._lock:
            return self._bytes_sent

    def get_received_bytes(self) -> int:
        """Get total bytes received."""
        with self._lock:
            return self._bytes_received

    def get_data_frames_sent(self) -> int:
        """Get DATA frames sent."""
        with self._lock:
            return self._data_frames_sent

    def get_data_frames_received(self) -> int:
        """Get DATA frames received."""
        with self._lock:
            return self._data_frames_received

    def get_heartbeat_frames_sent(self) -> int:
        """Get HEARTBEAT frames sent."""
        with self._lock:
            return self._heartbeat_frames_sent

    def get_heartbeat_frames_received(self) -> int:
        """Get HEARTBEAT frames received."""
        with self._lock:
            return self._heartbeat_frames_received

    def get_errors(self) -> int:
        """Get total error count."""
        with self._lock:
            return self._errors

    def get_uptime(self) -> float:
        """Get tunnel uptime in seconds."""
        with self._lock:
            return time.time() - self._start_time

    def get_sent_throughput(self) -> float:
        """Get send throughput in bytes per second.

        Returns:
            Bytes per second, or 0.0 if not running.
        """
        with self._lock:
            uptime = time.time() - self._start_time
            if uptime > 0:
                return self._bytes_sent / uptime
            return 0.0

    def get_received_throughput(self) -> float:
        """Get receive throughput in bytes per second.

        Returns:
            Bytes per second, or 0.0 if not running.
        """
        with self._lock:
            uptime = time.time() - self._start_time
            if uptime > 0:
                return self._bytes_received / uptime
            return 0.0

    def get_snapshot(self) -> dict:
        """Get a snapshot of all statistics.

        Returns:
            Dictionary with all statistics.
        """
        with self._lock:
            uptime = time.time() - self._start_time
            return {
                "frames_sent": self._frames_sent,
                "frames_received": self._frames_received,
                "bytes_sent": self._bytes_sent,
                "bytes_received": self._bytes_received,
                "data_frames_sent": self._data_frames_sent,
                "data_frames_received": self._data_frames_received,
                "heartbeat_frames_sent": self._heartbeat_frames_sent,
                "heartbeat_frames_received": self._heartbeat_frames_received,
                "errors": self._errors,
                "uptime_seconds": uptime,
                "sent_throughput_bps": self._bytes_sent / uptime if uptime > 0 else 0.0,
                "received_throughput_bps": self._bytes_received / uptime if uptime > 0 else 0.0,
            }

    def report(self) -> str:
        """Generate a formatted statistics report.

        Returns:
            Formatted report string.
        """
        stats = self.get_snapshot()
        uptime = stats["uptime_seconds"]

        # Format uptime
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        seconds = int(uptime % 60)
        uptime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        lines = [
            "=" * 50,
            "           Tunnel Statistics Report",
            "=" * 50,
            f"  Uptime:              {uptime_str}",
            "",
            "  Frames:",
            f"    Sent:               {stats['frames_sent']}",
            f"    Received:           {stats['frames_received']}",
            "",
            "  Frame Types:",
            f"    DATA sent:          {stats['data_frames_sent']}",
            f"    DATA received:      {stats['data_frames_received']}",
            f"    HEARTBEAT sent:     {stats['heartbeat_frames_sent']}",
            f"    HEARTBEAT received: {stats['heartbeat_frames_received']}",
            "",
            "  Data Transfer:",
            f"    Sent:               {stats['bytes_sent']:,} bytes",
            f"    Received:           {stats['bytes_received']:,} bytes",
            "",
            "  Throughput:",
            f"    Send:               {stats['sent_throughput_bps']:.2f} bytes/sec",
            f"    Receive:            {stats['received_throughput_bps']:.2f} bytes/sec",
            "",
            "  Errors:",
            f"    Total:              {stats['errors']}",
            "=" * 50,
        ]

        return "\n".join(lines)

    def __repr__(self) -> str:
        stats = self.get_snapshot()
        return (
            f"TunnelStats("
            f"sent={stats['frames_sent']} frames / {stats['bytes_sent']} bytes, "
            f"received={stats['frames_received']} frames / {stats['bytes_received']} bytes, "
            f"errors={stats['errors']})"
        )


# Global stats instance for convenience
_global_stats: Optional[TunnelStats] = None
_global_lock = threading.Lock()


def get_global_stats() -> TunnelStats:
    """Get or create the global TunnelStats instance.

    Returns:
        Global TunnelStats instance.
    """
    global _global_stats
    with _global_lock:
        if _global_stats is None:
            _global_stats = TunnelStats()
        return _global_stats


def reset_global_stats() -> None:
    """Reset the global statistics instance."""
    global _global_stats
    with _global_lock:
        _global_stats = None
