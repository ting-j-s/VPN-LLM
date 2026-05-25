"""ShapedTransport — a Transport wrapper that applies traffic shaping.

Wraps any Transport instance with the configured TrafficShaper and
SendScheduler. Padding, aggregation, and jitter are applied transparently
on send/recv.

All shaping is opt-in via configuration — when shaping is disabled the
wrapper is a transparent pass-through.
"""

from __future__ import annotations

from typing import Optional

from ..common.frame import FrameType
from ..common.errors import TransportError
from ..common.logger import get_logger
from ..shaping.base import NoopTrafficShaper, TrafficShaper
from ..shaping.scheduler import SendScheduler
from .base import Transport

logger = get_logger(__name__)


class ShapedTransport(Transport):
    """Decorator that applies traffic shaping to an existing Transport."""

    def __init__(self, transport: Transport, shaper: TrafficShaper | None = None):
        self._transport = transport
        self._shaper: TrafficShaper = shaper or NoopTrafficShaper()
        self._scheduler = SendScheduler(self._shaper)
        self._recv_buffer: list[bytes] = []

    # ------------------------------------------------------------------
    # Transport interface
    # ------------------------------------------------------------------

    def connect(self) -> None:
        self._transport.connect()

    def send(self, data: bytes) -> None:
        if not self._transport.is_connected():
            raise TransportError("Not connected")
        chunks = self._scheduler.send(data, FrameType.DATA)
        for chunk in chunks:
            self._transport.send(chunk.data)

    def recv(self, timeout: Optional[float] = None) -> Optional[bytes]:
        if self._recv_buffer:
            return self._recv_buffer.pop(0)

        raw = self._transport.recv(timeout=timeout)
        if raw is None:
            return None

        frames = self._shaper.decode_chunk(raw)
        if not frames:
            return None

        if len(frames) > 1:
            self._recv_buffer.extend(frames[1:])
        return frames[0]

    def close(self) -> None:
        try:
            self.flush()
        except Exception:
            logger.debug("Flush during close failed", exc_info=True)
        try:
            self._shaper.close()
        except Exception:
            logger.debug("Shaper close failed", exc_info=True)
        self._transport.close()

    def is_connected(self) -> bool:
        return self._transport.is_connected()

    # ------------------------------------------------------------------
    # Shaping helpers
    # ------------------------------------------------------------------

    def flush(self) -> None:
        chunks = self._scheduler.flush()
        for chunk in chunks:
            self._transport.send(chunk.data)

    @property
    def shaper(self) -> TrafficShaper:
        return self._shaper
