"""VPN Client Core Implementation.

Manages client-side tunnel logic including:
- TUN device interaction
- Frame encoding/decoding
- Transport layer management
- Session handling and reconnection
"""

import select
import threading
import time
from typing import Optional

from ..common.errors import VPNError
from ..common.frame import Frame, FrameType, create_frame
from ..common.logger import setup_logger
from ..transport.base import BaseTransport
from ..tun.tun_device import TUNDevice


logger = setup_logger(__name__)


class ClientCore:
    """Client-side VPN tunnel core.

    Coordinates TUN device reads, frame encoding, and transport sending.
    Runs receive and transmit loops in separate threads.
    """

    def __init__(
        self,
        tun: TUNDevice,
        transport: BaseTransport,
        session_id: int = 1,
        keepalive_interval: float = 30.0,
    ):
        """Initialize client core.

        Args:
            tun: TUN device for reading/writing IP packets.
            transport: Transport layer for frame transmission.
            session_id: Session identifier (used in frame headers).
            keepalive_interval: Interval for keepalive frames (seconds).
        """
        self.tun = tun
        self.transport = transport
        self.session_id = session_id
        self.keepalive_interval = keepalive_interval

        self._rx_thread: Optional[threading.Thread] = None
        self._tx_thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False

    def start(self) -> None:
        """Start the client tunnel.

        Opens TUN device, connects transport, and starts rx/tx threads.
        """
        if self._running:
            logger.warning("Client core already running")
            return

        logger.info("Starting client core")

        # Open TUN device
        self.tun.open()

        # Connect transport
        self.transport.connect()
        self._connected = self.transport.is_connected()

        if not self._connected:
            raise VPNError("Transport connection failed")

        # Send hello handshake
        hello_frame = create_frame(FrameType.HELLO, self.session_id, b"HELLO")
        self.transport.send_frame(hello_frame)
        logger.info("Sent HELLO frame")

        # Wait for hello ack
        ack = self.transport.recv_frame(timeout=10.0)
        if ack is None or ack.frame_type != FrameType.HELLO_ACK:
            raise VPNError("Hello acknowledgment not received")

        logger.info("Received HELLO_ACK, tunnel established")

        # Start rx and tx threads
        self._running = True
        self._rx_thread = threading.Thread(target=self._rx_loop, name="client-rx", daemon=True)
        self._tx_thread = threading.Thread(target=self._tx_loop, name="client-tx", daemon=True)
        self._rx_thread.start()
        self._tx_thread.start()

        logger.info("Client core started successfully")

    def stop(self) -> None:
        """Stop the client tunnel gracefully."""
        if not self._running:
            return

        logger.info("Stopping client core")
        self._running = False

        # Wait for threads to finish
        if self._rx_thread:
            self._rx_thread.join(timeout=5.0)
        if self._tx_thread:
            self._tx_thread.join(timeout=5.0)

        # Send disconnect
        try:
            disconnect_frame = create_frame(FrameType.CLOSE, self.session_id)
            self.transport.send_frame(disconnect_frame)
        except Exception:
            pass

        # Disconnect transport
        self.transport.disconnect()

        # Close TUN device
        self.tun.close()

        logger.info("Client core stopped")

    def _rx_loop(self) -> None:
        """Receive loop: read frames from transport, write to TUN.

        Runs in separate thread.
        """
        logger.info("Client RX loop started")

        while self._running and self._connected:
            # Use select for portable I/O monitoring
            # For now, use recv_frame with timeout
            frame = self.transport.recv_frame(timeout=1.0)

            if frame is None:
                continue

            # Handle different frame types
            if frame.frame_type == FrameType.DATA:
                # Write IP packet to TUN
                if frame.payload:
                    try:
                        self.tun.write(frame.payload)
                        logger.debug(f"TUN wrote {len(frame.payload)} bytes")
                    except Exception as e:
                        logger.error(f"TUN write error: {e}")
            elif frame.frame_type == FrameType.KEEPALIVE:
                logger.debug("Received KEEPALIVE")
            elif frame.frame_type == FrameType.DISCONNECT:
                logger.info("Received DISCONNECT from server")
                break
            else:
                logger.warning(f"Unknown frame type: {frame.frame_type:#04x}")

            self._connected = self.transport.is_connected()

        logger.info("Client RX loop finished")

    def _tx_loop(self) -> None:
        """Transmit loop: read from TUN, send frames via transport.

        Also sends periodic keepalive frames.
        Runs in separate thread.
        """
        logger.info("Client TX loop started")
        last_keepalive = time.time()

        while self._running and self._connected:
            # Poll TUN device for data
            packet = self.tun.read(max_size=65535)

            if packet:
                # Encode as data frame
                frame = create_frame(FrameType.DATA, self.session_id, packet)
                try:
                    self.transport.send_frame(frame)
                    logger.debug(f"Sent {len(packet)} bytes as frame")
                except Exception as e:
                    logger.error(f"Transport send error: {e}")

            # Check keepalive
            now = time.time()
            if now - last_keepalive >= self.keepalive_interval:
                try:
                    keepalive = create_frame(FrameType.HEARTBEAT, self.session_id)
                    self.transport.send_frame(keepalive)
                    last_keepalive = now
                    logger.debug("Sent KEEPALIVE")
                except Exception as e:
                    logger.error(f"Keepalive send error: {e}")

            self._connected = self.transport.is_connected()

        logger.info("Client TX loop finished")

    def is_connected(self) -> bool:
        """Check if tunnel is active.

        Returns:
            True if connected, False otherwise.
        """
        return self._running and self._connected
