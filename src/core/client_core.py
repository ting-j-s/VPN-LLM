"""VPN Client Core Implementation.

Manages client-side tunnel logic:
- Bidirectional forwarding between TUN and Transport
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


class ClientCore:
    """Client-side VPN tunnel core.

    Responsibilities:
    - Open TUN device
    - Connect to server via Transport
    - Bidirectional forwarding:
        - tun_to_transport: TUN -> Frame -> Transport
        - transport_to_tun: Transport -> Frame -> TUN
    - Heartbeat mechanism for connection health

    Usage:
        tun = MockTunDevice()
        transport = SSHTransport(host="127.0.0.1", port=22, username="user")
        client = ClientCore(tun=tun, transport=transport)
        client.start()
        # ... tunnel is running bidirectionally ...
        client.stop()
    """

    def __init__(
        self,
        tun: TunDevice,
        transport: Transport,
        session_id: Optional[bytes] = None,
        heartbeat_interval: float = 10.0,
        heartbeat_timeout: float = 30.0,
    ):
        """Initialize client core.

        Args:
            tun: TUN device for reading/writing IP packets.
            transport: Transport for sending/receiving frames.
            session_id: 16-byte session ID. Auto-generated if None.
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
        self._tun_to_transport_thread: Optional[threading.Thread] = None
        self._transport_to_tun_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None

        # Heartbeat state
        self._last_sent_time: float = 0
        self._last_received_time: float = 0

        # Statistics
        self._tun_to_transport_bytes = 0
        self._transport_to_tun_bytes = 0

    def start(self) -> None:
        """Start the client tunnel.

        Opens TUN device and connects transport.
        Starts all loops in separate threads.

        Raises:
            VPNError: If TUN or Transport fails.
        """
        if self._running:
            logger.warning("Client core already running")
            return

        logger.info("Starting client core")

        # Open TUN device
        try:
            self.tun.open()
        except Exception as e:
            raise VPNError(f"Failed to open TUN device: {e}")

        # Connect transport
        try:
            self.transport.connect()
        except Exception as e:
            self.tun.close()
            raise VPNError(f"Failed to connect transport: {e}")

        if not self.transport.is_connected():
            self.tun.close()
            raise VPNError("Transport connection failed")

        # Initialize heartbeat state
        now = time.time()
        self._last_sent_time = now
        self._last_received_time = now

        # Reset statistics
        self._tun_to_transport_bytes = 0
        self._transport_to_tun_bytes = 0

        # Start threads
        self._running = True
        self._stop_event.clear()

        self._transport_to_tun_thread = threading.Thread(
            target=self._transport_to_tun_loop,
            name="client-transport-to-tun",
            daemon=True,
        )
        self._tun_to_transport_thread = threading.Thread(
            target=self._tun_to_transport_loop,
            name="client-tun-to-transport",
            daemon=True,
        )
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="client-heartbeat",
            daemon=True,
        )

        self._transport_to_tun_thread.start()
        self._tun_to_transport_thread.start()
        self._heartbeat_thread.start()

        session_hex = uuid.UUID(bytes=self.session_id).hex[:8]
        logger.info(f"Client core started (session_id={session_hex}...)")

    def stop(self) -> None:
        """Stop the client tunnel gracefully.

        This method is idempotent and can be called multiple times.

        Order: set stop_event -> close transport -> close TUN -> join threads
        This ensures blocking reads in worker threads are interrupted.
        """
        if not self._running:
            logger.debug("Client core already stopped")
            return

        logger.info("Stopping client core")
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
            self._tun_to_transport_thread,
            self._transport_to_tun_thread,
            self._heartbeat_thread,
        ]
        for t in threads:
            if t:
                t.join(timeout=5.0)

        logger.info(f"Graceful shutdown completed (tun->transport={self._tun_to_transport_bytes} bytes, "
                    f"transport->tun={self._transport_to_tun_bytes} bytes)")

        logger.info(f"Client core stopped (tun->transport={self._tun_to_transport_bytes} bytes, "
                    f"transport->tun={self._transport_to_tun_bytes} bytes)")

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

                # Refresh last received time on any valid frame
                self._last_received_time = time.time()

                frame = decode_frame(data)
                self._handle_frame(frame)

            except VPNError as e:
                if self._stop_event.is_set():
                    # Normal shutdown, connection closed by peer
                    logger.debug(f"Connection closed: {e}")
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
        if frame.frame_type == FrameType.DATA:
            if frame.payload:
                try:
                    self.tun.write_packet(frame.payload)
                    self._transport_to_tun_bytes += len(frame.payload)
                    logger.debug(f"Transport->TUN: wrote {len(frame.payload)} bytes")
                except Exception as e:
                    logger.error(f"TUN write error: {e}")

        elif frame.frame_type == FrameType.HEARTBEAT:
            logger.debug("Received HEARTBEAT from server")

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
            "tun_to_transport_bytes": self._tun_to_transport_bytes,
            "transport_to_tun_bytes": self._transport_to_tun_bytes,
            "last_heartbeat_sent_ago": time.time() - self._last_sent_time,
            "last_heartbeat_received_ago": time.time() - self._last_received_time,
        }

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False
