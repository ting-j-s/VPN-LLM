"""VPN Server Core Implementation.

Manages server-side tunnel logic:
- Bidirectional forwarding between Transport and TUN
- Dedicated heartbeat thread for connection health monitoring
"""

import threading
import time
import uuid
from typing import Optional

from ..common.errors import VPNError
from ..common.frame import Frame, FrameType, create_frame, encode_frame, decode_frame
from ..common.logger import get_logger
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
    ):
        """Initialize server core.

        Args:
            tun: TUN device for reading/writing IP packets.
            transport: Transport for receiving/sending frames.
            session_id: Expected session ID. Auto-generated if None.
            heartbeat_interval: Interval between HEARTBEAT frames (seconds).
            heartbeat_timeout: Timeout for no received data (seconds).
        """
        self.tun = tun
        self.transport = transport

        # Generate session ID if not provided
        if session_id is None:
            session_id = uuid.uuid4().bytes
        if len(session_id) != 16:
            raise VPNError("session_id must be 16 bytes")
        self.session_id = session_id
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout

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
        """Stop the server tunnel gracefully."""
        if not self._running:
            return

        logger.info("Stopping server core")
        self._running = False
        self._stop_event.set()

        # Wait for threads
        threads = [
            self._transport_to_tun_thread,
            self._tun_to_transport_thread,
            self._heartbeat_thread,
        ]
        for t in threads:
            if t:
                t.join(timeout=5.0)

        # Close transport
        try:
            self.transport.close()
        except Exception as e:
            logger.error(f"Error closing transport: {e}")

        # Close TUN
        try:
            self.tun.close()
        except Exception as e:
            logger.error(f"Error closing TUN: {e}")

        logger.info(f"Server core stopped (transport->tun={self._transport_to_tun_bytes} bytes, "
                    f"tun->transport={self._tun_to_transport_bytes} bytes)")

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
                    self.transport.send(encode_frame(heartbeat))
                    self._last_sent_time = now
                    logger.debug("Sent HEARTBEAT")
                except Exception as e:
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

                # Refresh last received time on any valid frame
                self._last_received_time = time.time()

                frame = decode_frame(data)
                self._handle_frame(frame)

            except VPNError as e:
                logger.error(f"Frame error: {e}")
                break
            except Exception as e:
                logger.error(f"Error in transport->tun loop: {e}")
                break

        logger.info("Transport->TUN loop finished")

    def _handle_frame(self, frame: Frame) -> None:
        """Handle received frame.

        Args:
            frame: Decoded frame.
        """
        if frame.frame_type == FrameType.DATA:
            if frame.payload:
                try:
                    self.tun.write_packet(frame.payload)
                    self._transport_to_tun_bytes += len(frame.payload)
                    logger.debug(f"Transport->TUN: wrote {len(frame.payload)} bytes")
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
                    data = encode_frame(frame)
                    self.transport.send(data)
                    self._tun_to_transport_bytes += len(packet)
                    self._last_sent_time = time.time()
                    logger.debug(f"TUN->Transport: sent {len(packet)} bytes")

            except VPNError as e:
                logger.error(f"TUN error: {e}")
                break
            except Exception as e:
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
