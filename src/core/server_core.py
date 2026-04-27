"""VPN Server Core Implementation.

Manages server-side tunnel logic including:
- Transport layer management
- Frame encoding/decoding
- Session handling
- Forwarding to TUN or external network
"""

import select
import threading
import time
from typing import Optional

from ..common.errors import TunnelError
from ..common.frame import Frame, FRAME_TYPE_DATA, FRAME_TYPE_HELLO, FRAME_TYPE_HELLO_ACK, FRAME_TYPE_KEEPALIVE, FRAME_TYPE_DISCONNECT
from ..common.logger import setup_logger
from ..transport.base import BaseTransport
from ..tun.tun_device import TUNDevice


logger = setup_logger(__name__)


class ServerCore:
    """Server-side VPN tunnel core.

    Coordinates frame receiving, forwarding to TUN or NAT/routing layer.
    Runs receive loop in a separate thread.
    """

    def __init__(
        self,
        tun: TUNDevice,
        transport: BaseTransport,
        session_id: int = 1,
        keepalive_interval: float = 30.0,
    ):
        """Initialize server core.

        Args:
            tun: TUN device for writing IP packets (or mock for forwarding).
            transport: Transport layer for frame reception.
            session_id: Session identifier.
            keepalive_interval: Interval for keepalive frames (seconds).
        """
        self.tun = tun
        self.transport = transport
        self.session_id = session_id
        self.keepalive_interval = keepalive_interval

        self._rx_thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False

    def start(self) -> None:
        """Start the server tunnel.

        Opens TUN device, starts listening, and runs receive loop.
        """
        if self._running:
            logger.warning("Server core already running")
            return

        logger.info("Starting server core")

        # Open TUN device
        self.tun.open()

        # Start receive loop thread
        self._running = True
        self._rx_thread = threading.Thread(target=self._rx_loop, name="server-rx", daemon=True)
        self._rx_thread.start()

        logger.info("Server core started successfully")

    def stop(self) -> None:
        """Stop the server tunnel gracefully."""
        if not self._running:
            return

        logger.info("Stopping server core")
        self._running = False

        # Wait for thread to finish
        if self._rx_thread:
            self._rx_thread.join(timeout=5.0)

        # Disconnect transport
        self.transport.disconnect()

        # Close TUN device
        self.tun.close()

        logger.info("Server core stopped")

    def _rx_loop(self) -> None:
        """Receive loop: receive frames from transport, write to TUN/forward.

        Runs in separate thread.
        """
        logger.info("Server RX loop started")

        while self._running:
            # Receive frame
            frame = self.transport.recv_frame(timeout=1.0)

            if frame is None:
                continue

            # Handle handshake
            if frame.frame_type == FRAME_TYPE_HELLO:
                logger.info(f"Received HELLO (session_id={frame.session_id})")
                ack = Frame.create_hello_ack_frame(frame.session_id)
                try:
                    self.transport.send_frame(ack)
                    self._connected = True
                    logger.info("Sent HELLO_ACK")
                except Exception as e:
                    logger.error(f"Handshake error: {e}")
                continue

            if not self._connected:
                logger.warning("Received frame before HELLO")
                continue

            # Handle data frames
            if frame.frame_type == FRAME_TYPE_DATA:
                if frame.payload:
                    try:
                        self.tun.write(frame.payload)
                        logger.debug(f"TUN wrote {len(frame.payload)} bytes")
                    except Exception as e:
                        logger.error(f"TUN write error: {e}")

            elif frame.frame_type == FRAME_TYPE_KEEPALIVE:
                logger.debug("Received KEEPALIVE")
                # Respond with keepalive
                try:
                    ack = Frame.create_keepalive_frame(frame.session_id)
                    self.transport.send_frame(ack)
                except Exception as e:
                    logger.error(f"Keepalive response error: {e}")

            elif frame.frame_type == FRAME_TYPE_DISCONNECT:
                logger.info("Received DISCONNECT from client")
                break

            else:
                logger.warning(f"Unknown frame type: {frame.frame_type:#04x}")

        logger.info("Server RX loop finished")

    def is_connected(self) -> bool:
        """Check if tunnel is active.

        Returns:
            True if connected, False otherwise.
        """
        return self._running and self._connected
