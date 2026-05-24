"""SOCKS5 Transport Skeleton.

Placeholder implementation for future SOCKS5-based transport.
The class is importable, constructable, and accepts configuration,
but all network methods raise TransportError.
"""

from typing import Optional

from ..common.errors import TransportError
from ..common.logger import get_logger
from .base import Transport


logger = get_logger(__name__)


class Socks5Transport(Transport):
    """SOCKS5 transport (skeleton).

    .. note::
       This is a **skeleton** placeholder.  The transport does not
       implement any real connectivity.  Calling ``connect()`` raises
       ``TransportError``.
    """

    def __init__(self):
        self._connected = False

    def connect(self) -> None:
        """Establish SOCKS5 connection (not implemented).

        Raises:
            TransportError: Always, because this is a skeleton.
        """
        raise TransportError("SOCKS5 transport skeleton is not fully implemented yet")

    def send(self, data: bytes) -> None:
        """Send data (not implemented).

        Args:
            data: Bytes to send.

        Raises:
            TransportError: Always, because this is a skeleton.
        """
        raise TransportError("SOCKS5 transport skeleton is not fully implemented yet")

    def recv(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Receive data (not implemented).

        Args:
            timeout: Ignored.

        Returns:
            Never returns normally.

        Raises:
            TransportError: Always, because this is a skeleton.
        """
        raise TransportError("SOCKS5 transport skeleton is not fully implemented yet")

    def close(self) -> None:
        """Close the transport (no-op in skeleton)."""
        self._connected = False

    def is_connected(self) -> bool:
        """Check connection state (always False in skeleton)."""
        return False

    def __repr__(self) -> str:
        return "Socks5Transport(skeleton)"
