"""Abstract Base Class for Transport Layer.

Defines the interface that all transport implementations must follow.
Transport layer handles framing and transmission of tunnel data.
"""

from abc import ABC, abstractmethod
from typing import Optional

from ..common.errors import TransportError
from ..common.logger import setup_logger
from ..common.frame import Frame


logger = setup_logger(__name__)


class BaseTransport(ABC):
    """Abstract base class for transport implementations.

    All transport protocols (SSH, TCP, TLS, WebSocket) must implement
    this interface to ensure VPN Core remains protocol-agnostic.
    """

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to the remote endpoint.

        Raises:
            TransportError: If connection fails.
        """
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Close the connection gracefully."""
        pass

    @abstractmethod
    def send_frame(self, frame: Frame) -> None:
        """Send a frame to the remote endpoint.

        Args:
            frame: Frame object to send.

        Raises:
            TransportError: If send fails.
        """
        pass

    @abstractmethod
    def recv_frame(self, timeout: Optional[float] = None) -> Optional[Frame]:
        """Receive a frame from the remote endpoint.

        Args:
            timeout: Maximum time to wait for a frame (seconds).
                    None means blocking, 0 means non-blocking.

        Returns:
            Frame object, or None if no frame available (non-blocking).

        Raises:
            TransportError: If receive fails.
        """
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if transport is currently connected.

        Returns:
            True if connected, False otherwise.
        """
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"
