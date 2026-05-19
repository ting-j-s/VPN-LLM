"""VPN Server Core Implementation.

Manages server-side tunnel logic:
- Bidirectional forwarding between Transport and TUN
- Dedicated heartbeat thread for connection health monitoring
"""

import threading
import time
import uuid
from typing import Optional

from ..common.errors import VPNError, TransportTimeout
from ..common.frame import Frame, FrameType, create_frame, encode_frame, decode_frame
from ..common.logger import get_logger
from ..shaping.base import NoopTrafficShaper, TrafficShaper
from ..transport.base import Transport
from ..tun.tun_device import TunDevice


logger = get_logger(__name__)


class ServerCore:
    """Server-side VPN tunnel core.

    Responsibilities:
    - Open TUN device
    - Accept Transport connection
    - Bidirectional forwarding:
        - transport_to_tun: Transport -> Frame -> TUN
        - tun_to_transport: TUN -> Frame -> Transport
    - Heartbeat mechanism for connection health

    Usage:
        tun = MockTunDevice()
        transport = MockTransport()  # In real use, server transport
        server = ServerCore(tun=tun, transport=transport)
        server.start()
        # ... tunnel is running bidirectionally ...
        server.stop()
    """

    def __init__(
        self,
        tun: TunDevice,
        transport: Transport,
        session_id: Optional[bytes] = None,
        heartbeat_interval: float = 10.0,
        heartbeat_timeout: float = 30.0,
        traffic_shaper: TrafficShaper | None = None,
    ):
        """Initialize server core.

        Args:
            tun: TUN device for reading/writing IP packets.
            transport: Transport for receiving/sending frames.
            session_id: Expected session ID. Auto-generated if None.
            heartbeat_interval: Interval between HEARTBEAT frames (seconds).
            heartbeat_timeout: Timeout for no received data (seconds).
            traffic_shaper: Optional TrafficShaper. Defaults to NoopTrafficShaper.
        """
        self.tun = tun
        self.transport = transport

        # Generate session ID if not provided
        self._session_id_explicit = session_id is not None
        if session_id is None:
            session_id = uuid.uuid4().bytes
        if len(session_id) != 16:
            raise VPNError("session_id must be 16 bytes")
        self.session_id = session_id
        self._session_adopted = False
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout

        # Traffic shaping (default: no-op, zero impact on existing behavior)
        self.traffic_shaper: TrafficShaper = traffic_shaper or NoopTrafficShaper()

        self._running = False
        self._stop_event: threading.Event = threading.Event()
        self._transport_to_tun_thread: Optional[threading.Thread] = None
        self._tun_to_transport_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None

        # Heartbeat state
        self._last_sent_time: float = 0
        self._last_received_time: float = 0

        # Statistics
        self._transport_to_tun_bytes = 0
        self._tun_to_transport_bytes = 0

    def start(self) -> None:
        """Start the server tunnel.

        Opens TUN device and starts all loops.

        Raises:
            VPNError: If TUN fails.
        """
        if self._running:
            logger.warning("Server core already running")
            return

        logger.info("Starting server core")

        # Open TUN device
        try:
            self.tun.open()
        except Exception as e:
            raise VPNError(f"Failed to open TUN device: {e}")

        # Connect transport (server mode: listen/accept)
        try:
            self.transport.connect()
        except Exception as e:
            self.tun.close()
            raise VPNError(f"Failed to start transport listener: {e}")

        # For server mode transports, wait for client connection
        if hasattr(self.transport, 'accept'):
            try:
                self.transport.accept()
            except Exception as e:
                self.transport.close()
                self.tun.close()
                raise VPNError(f"Failed to accept client connection: {e}")

        if not self.transport.is_connected():
            self.transport.close()
            self.tun.close()
            raise VPNError("Transport connection failed")

        # Initialize heartbeat state
        now = time.time()
        self._last_sent_time = now
        self._last_received_time = now

        # Reset statistics
        self._transport_to_tun_bytes = 0
        self._tun_to_transport_bytes = 0

        # Start threads
        self._running = True
        self._stop_event.clear()

        self._transport_to_tun_thread = threading.Thread(
            target=self._transport_to_tun_loop,
            name="server-transport-to-tun",
            daemon=True,
        )
        self._tun_to_transport_thread = threading.Thread(
            target=self._tun_to_transport_loop,
            name="server-tun-to-transport",
            daemon=True,
        )
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="server-heartbeat",
            daemon=True,
        )

        self._transport_to_tun_thread.start()
        self._tun_to_transport_thread.start()
        self._heartbeat_thread.start()

        session_hex = uuid.UUID(bytes=self.session_id).hex[:8]
        logger.info(f"Server core started (session_id={session_hex}...)")

    def stop(self) -> None:
        """Stop the server tunnel gracefully.

        This method is idempotent and can be called multiple times.

        Order: set stop_event -> close transport -> close TUN -> join threads
        This ensures blocking reads in worker threads are interrupted.
        """
        if not self._running:
            logger.debug("Server core already stopped")
            return

        logger.info("Stopping server core")
        self._running = False
        self._stop_event.set()

        # Close transport first to interrupt blocking recv() calls
        try:
            self.transport.close()
        except Exception as e:
            logger.warning(f"Error closing transport: {e}")

        # Close TUN to interrupt blocking read_packet() calls
        try:
            self.tun.close()
        except Exception as e:
            logger.warning(f"Error closing TUN: {e}")

        # Wait for threads to finish
        threads = [
            self._transport_to_tun_thread,
            self._tun_to_transport_thread,
            self._heartbeat_thread,
        ]
        for t in threads:
            if t:
                t.join(timeout=5.0)

        logger.info(f"Graceful shutdown completed (transport->tun={self._transport_to_tun_bytes} bytes, "
                    f"tun->transport={self._tun_to_transport_bytes} bytes)")

    def _send_shaped(self, encoded_frame: bytes) -> None:
        """Send an encoded frame through the traffic shaper.

        If the shaper buffers the frame (returns empty), flushes immediately.
        Jitter delay_ms metadata is logged but not slept (scheduler not active).
        """
        chunks = self.traffic_shaper.encode_frame(encoded_frame)
        if not chunks:
            chunks = self.traffic_shaper.flush()
        for chunk in chunks:
            if chunk.delay_ms > 0:
                logger.debug(f"Jitter delay {chunk.delay_ms:.1f}ms ignored (no scheduler)")
            self.transport.send(chunk.data)

    def _heartbeat_loop(self) -> None:
        """Dedicated heartbeat thread.

        Sends HEARTBEAT frames periodically.
        Monitors last received time and triggers timeout.
        """
        logger.info("Heartbeat loop started")

        while self._running and not self._stop_event.is_set():
            now = time.time()

            # Check timeout
            if now - self._last_received_time > self.heartbeat_timeout:
                logger.warning(f"Heartbeat timeout ({self.heartbeat_timeout}s), no data received")
                self._stop_event.set()
                break

            # Send HEARTBEAT
            if now - self._last_sent_time >= self.heartbeat_interval:
                try:
                    heartbeat = create_frame(FrameType.HEARTBEAT, self.session_id)
                    self._send_shaped(encode_frame(heartbeat))
                    self._last_sent_time = now
                    logger.debug("Sent HEARTBEAT")
                except Exception as e:
                    if self._stop_event.is_set():
                        # Normal shutdown
                        logger.debug(f"HEARTBEAT send skipped: {e}")
                    else:
                        logger.error(f"HEARTBEAT send error: {e}")
                    self._stop_event.set()
                    break

            # Sleep short interval to avoid busy loop
            time.sleep(1.0)

        logger.info("Heartbeat loop finished")

    def _transport_to_tun_loop(self) -> None:
        """Receive frames from Transport and write to TUN.

        Handles: DATA, HEARTBEAT, CLOSE
        """
        logger.info("Transport->TUN loop started")

        while self._running and not self._stop_event.is_set():
            try:
                data = self.transport.recv(timeout=1.0)
                if data is None:
                    continue

                # Refresh last received time on any valid data
                self._last_received_time = time.time()

                try:
                    encoded_frames = self.traffic_shaper.decode_chunk(data)
                except Exception as e:
                    logger.warning(f"Shaper decode error, dropping chunk (len={len(data)}): {e}")
                    continue

                for encoded in encoded_frames:
                    frame = decode_frame(encoded)
                    logger.debug(
                        f"Transport->TUN RECEIVED frame: type={frame.frame_type.name} "
                        f"session={uuid.UUID(bytes=frame.session_id).hex[:8] if frame.session_id else '?'} "
                        f"payload_len={len(frame.payload) if frame.payload else 0}"
                    )
                    self._handle_frame(frame)

            except VPNError as e:
                if self._stop_event.is_set():
                    # Normal shutdown, connection closed by peer
                    logger.debug(f"Connection closed: {e}")
                    break
                if isinstance(e, TransportTimeout):
                    # Timeout is normal, no data available
                    continue
                # Check if transport is truly disconnected (not just idle)
                if not self.transport.is_connected():
                    logger.debug(f"Transport disconnected, stopping loop: {e}")
                    break
                logger.warning(f"Frame error: {e}")
                break
            except Exception as e:
                if self._stop_event.is_set():
                    # Normal shutdown
                    logger.debug(f"Connection closed: {e}")
                    break
                logger.error(f"Error in transport->tun loop: {e}")
                break

        logger.info("Transport->TUN loop finished")

    def _handle_frame(self, frame: Frame) -> None:
        """Handle received frame.

        Args:
            frame: Decoded frame.
        """
        if frame.session_id != self.session_id:
            if not self._session_id_explicit and not self._session_adopted:
                old_hex = uuid.UUID(bytes=self.session_id).hex[:8]
                self.session_id = frame.session_id
                self._session_adopted = True
                new_hex = uuid.UUID(bytes=self.session_id).hex[:8]
                logger.info(
                    f"Auto-adopted client session_id: {old_hex} -> {new_hex}"
                )
            else:
                logger.warning("Dropping frame with unexpected session_id")
                return

        if frame.frame_type == FrameType.DATA:
            if frame.payload:
                try:
                    self.tun.write_packet(frame.payload)
                    self._transport_to_tun_bytes += len(frame.payload)
                    logger.debug(
                        f"Transport->TUN WROTE packet: len={len(frame.payload)} bytes, "
                        f"total received: {self._transport_to_tun_bytes} bytes"
                    )
                except Exception as e:
                    logger.error(f"TUN write error: {e}")

        elif frame.frame_type == FrameType.HEARTBEAT:
            logger.debug("Received HEARTBEAT from client")

        elif frame.frame_type == FrameType.CLOSE:
            logger.info("Received CLOSE, initiating shutdown")
            self._stop_event.set()

        elif frame.frame_type == FrameType.AUTH:
            logger.debug("Received AUTH")

        else:
            logger.warning(f"Unknown frame type: {frame.frame_type:#04x}")

    def _tun_to_transport_loop(self) -> None:
        """Read from TUN and send frames to Transport.

        Forwards DATA frames only.
        """
        logger.info("TUN->Transport loop started")

        while self._running and not self._stop_event.is_set():
            try:
                packet = self.tun.read_packet()
                if packet:
                    frame = create_frame(FrameType.DATA, self.session_id, packet)
                    self._send_shaped(encode_frame(frame))
                    self._tun_to_transport_bytes += len(packet)
                    self._last_sent_time = time.time()
                    logger.debug(
                        f"TUN->Transport READ packet: len={len(packet)} bytes, "
                        f"total sent: {self._tun_to_transport_bytes} bytes, "
                        f"frame type=DATA session={uuid.UUID(bytes=self.session_id).hex[:8]}"
                    )

            except VPNError as e:
                if self._stop_event.is_set():
                    logger.debug(f"TUN error during shutdown: {e}")
                else:
                    logger.error(f"TUN error: {e}")
                break
            except Exception as e:
                if self._stop_event.is_set():
                    logger.debug(f"Error in tun->transport loop during shutdown: {e}")
                else:
                    logger.error(f"Error in tun->transport loop: {e}")
                break

        logger.info("TUN->Transport loop finished")

    def is_connected(self) -> bool:
        """Check if tunnel is active."""
        return self._running and self.transport.is_connected()

    def get_stats(self) -> dict:
        """Get forwarding statistics.

        Returns:
            Dict with bytes counts and heartbeat state.
        """
        return {
            "transport_to_tun_bytes": self._transport_to_tun_bytes,
            "tun_to_transport_bytes": self._tun_to_transport_bytes,
            "last_heartbeat_sent_ago": time.time() - self._last_sent_time,
            "last_heartbeat_received_ago": time.time() - self._last_received_time,
        }

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False
