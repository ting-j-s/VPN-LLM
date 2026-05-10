"""Transport Layer Abstract Interface.

Defines the interface that all transport implementations must follow.
VPN Core depends only on this abstraction, not on concrete protocols.
"""

from abc import ABC, abstractmethod
from typing import Optional

from ..common.errors import TransportError
from ..common.logger import get_logger


logger = get_logger(__name__)


class Transport(ABC):
    """Abstract base class for transport implementations.

    All transport protocols (SSH, TCP, TLS, WebSocket) must implement
    this interface to ensure VPN Core remains protocol-agnostic.

    The transport layer handles raw byte transmission between client and server.
    Frame encoding/decoding is handled by the layer above (FrameCodec).
    """

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to the remote endpoint.

        For client: connect to server.
        For server: start listening and accept connection.

        Raises:
            TransportError: If connection fails.
        """
        pass

    @abstractmethod
    def send(self, data: bytes) -> None:
        """Send raw bytes to the remote endpoint.

        Args:
            data: Encoded frame bytes to send.

        Raises:
            TransportError: If send fails or not connected.
        """
        pass

    @abstractmethod
    def recv(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Receive raw bytes from the remote endpoint.

        Args:
            timeout: Maximum time to wait in seconds.
                    None = blocking, 0 = non-blocking.

        Returns:
            Received frame bytes, or None if no data available (non-blocking).

        Raises:
            TransportError: If receive fails or not connected.
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """Close the transport connection gracefully.

        Should be called even if connection is broken.
        """
        pass

    def is_connected(self) -> bool:
        """Check if transport is currently connected.

        Returns:
            True if connected, False otherwise.
        """
        return False


class MockTransport(Transport):
    """Mock transport for testing without real network.

    Uses in-memory queues to simulate packet收发.
    Suitable for unit testing and integration testing.
    """

    def __init__(self):
        self._connected = False
        self._rx_queue: list[bytes] = []
        self._tx_data: list[bytes] = []
        self._error_on_send: Optional[Exception] = None
        self._error_on_recv: Optional[Exception] = None

    def connect(self) -> None:
        """Mark transport as connected."""
        self._connected = True
        self._rx_queue.clear()
        self._tx_data.clear()
        logger.debug("MockTransport connected")

    def send(self, data: bytes) -> None:
        """Store data to TX list.

        Args:
            data: Frame bytes to send.

        Raises:
            TransportError: If not connected or mock error set.
        """
        if not self._connected:
            raise TransportError("Not connected")

        if self._error_on_send:
            raise TransportError(str(self._error_on_send))

        self._tx_data.append(data)
        logger.debug(f"MockTransport sent {len(data)} bytes")

    def recv(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Return next bytes from RX queue.

        Args:
            timeout: Ignored in mock implementation.

        Returns:
            Frame bytes, or None if queue empty.

        Raises:
            TransportError: If not connected or mock error set.
        """
        if not self._connected:
            raise TransportError("Not connected")

        if self._error_on_recv:
            raise TransportError(str(self._error_on_recv))

        if self._rx_queue:
            return self._rx_queue.pop(0)
        return None

    def close(self) -> None:
        """Mark transport as disconnected."""
        self._connected = False
        self._rx_queue.clear()
        self._tx_data.clear()
        logger.debug("MockTransport closed")

    def is_connected(self) -> bool:
        """Return connection state."""
        return self._connected

    # Test helper methods

    def inject(self, data: bytes) -> None:
        """Inject frame bytes into RX queue (simulates incoming data).

        Args:
            data: Frame bytes to receive.
        """
        self._rx_queue.append(data)
        logger.debug(f"MockTransport injected {len(data)} bytes")

    def get_sent(self) -> list[bytes]:
        """Get all bytes sent via send().

        Returns:
            List of sent frame bytes.
        """
        return list(self._tx_data)

    def set_send_error(self, error: Exception) -> None:
        """Configure mock to raise error on next send().

        Args:
            error: Exception to raise.
        """
        self._error_on_send = error

    def set_recv_error(self, error: Exception) -> None:
        """Configure mock to raise error on next recv().

        Args:
            error: Exception to raise.
        """
        self._error_on_recv = error
