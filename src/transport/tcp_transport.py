"""TCP Transport Implementation.

Provides TCP-based transport for the VPN tunnel.
Supports both client (connect) and server (bind/listen/accept) modes.
"""

import socket
import struct
from typing import Optional

from ..common.errors import TransportError
from ..common.logger import get_logger
from .base import Transport


logger = get_logger(__name__)

# Length prefix: 4 bytes, big-endian unsigned int
LENGTH_PREFIX_LEN = 4
# Max frame size: 10MB
MAX_FRAME_SIZE = 10 * 1024 * 1024


class TCPTransport(Transport):
    """TCP-based transport implementation.

    Supports two modes:
    - Client mode: connect to server
    - Server mode: bind/listen/accept

    Protocol:
        - Each send() sends: [4-byte length (big-endian)][data bytes]
        - Each recv() reads: 4-byte length, then full data bytes
        - Handles partial reads correctly (半包问题)

    Usage (client):
        transport = TCPTransport(
            mode="client",
            host="127.0.0.1",
            port=2222,
        )
        transport.connect()
        # ... use send/recv ...
        transport.close()

    Usage (server):
        transport = TCPTransport(
            mode="server",
            host="0.0.0.0",
            port=2222,
        )
        transport.connect()  # Sets up listening socket
        # Call accept() to get connection from client
        transport.accept()
        # ... use send/recv ...
        transport.close()
    """

    # Mode constants
    MODE_CLIENT = "client"
    MODE_SERVER = "server"

    def __init__(
        self,
        mode: str = MODE_CLIENT,
        host: str = "127.0.0.1",
        port: int = 2222,
        backlog: int = 5,
    ):
        """Initialize TCP transport.

        Args:
            mode: "client" or "server".
            host: Host to bind or connect to.
            port: Port number.
            backlog: Listen backlog (server mode only).
        """
        if mode not in (self.MODE_CLIENT, self.MODE_SERVER):
            raise TransportError(f"Invalid mode: {mode}. Must be 'client' or 'server'")

        self.mode = mode
        self.host = host
        self.port = port
        self.backlog = backlog

        self._server_socket: Optional[socket.socket] = None
        self._client_socket: Optional[socket.socket] = None
        self._connected = False

    def connect(self) -> None:
        """Connect or start listening.

        Client mode: connect to server.
        Server mode: bind and listen.

        Raises:
            TransportError: If connection fails.
        """
        if self._connected:
            logger.warning("Already connected")
            return

        if self.mode == self.MODE_CLIENT:
            self._connect_client()
        else:
            self._start_server()

    def _connect_client(self) -> None:
        """Connect as client to server."""
        logger.info(f"TCP client connecting to {self.host}:{self.port}")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            sock.connect((self.host, self.port))
        except socket.error as e:
            sock.close()
            raise TransportError(f"Cannot connect to {self.host}:{self.port}: {e}")

        self._client_socket = sock
        self._connected = True
        logger.info("TCP client connected")

    def _start_server(self) -> None:
        """Start TCP server (bind/listen)."""
        logger.info(f"TCP server binding to {self.host}:{self.port}")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            sock.bind((self.host, self.port))
            sock.listen(self.backlog)
        except socket.error as e:
            sock.close()
            raise TransportError(f"Cannot bind to {self.host}:{self.port}: {e}")

        self._server_socket = sock
        logger.info(f"TCP server listening on {self.host}:{self.port}")

    def accept(self, timeout: Optional[float] = None) -> None:
        """Accept incoming connection (server mode only).

        Args:
            timeout: Maximum time to wait for connection.

        Raises:
            TransportError: If not in server mode or accept fails.
        """
        if self.mode != self.MODE_SERVER:
            raise TransportError("accept() is only available in server mode")

        if self._server_socket is None:
            raise TransportError("Server not listening, call connect() first")

        logger.info("Waiting for TCP connection...")

        self._server_socket.settimeout(timeout)

        try:
            client_sock, addr = self._server_socket.accept()
        except socket.timeout:
            raise TransportError("Accept timeout")
        except socket.error as e:
            raise TransportError(f"Accept failed: {e}")
        finally:
            self._server_socket.settimeout(None)

        self._client_socket = client_sock
        self._connected = True
        logger.info(f"TCP connection accepted from {addr}")

    def send(self, data: bytes) -> None:
        """Send data with length prefix.

        Format: [4-byte length (big-endian)][data bytes]

        Args:
            data: Frame bytes to send.

        Raises:
            TransportError: If not connected or send fails.
        """
        if not self._connected or self._client_socket is None:
            raise TransportError("Not connected")

        try:
            # Pack length as 4-byte big-endian unsigned int
            length_prefix = struct.pack(">I", len(data))
            self._client_socket.sendall(length_prefix + data)
            logger.debug(f"TCPTransport sent {len(data)} bytes (with 4-byte prefix)")
        except socket.error as e:
            self._connected = False
            raise TransportError(f"Send failed: {e}")

    def recv(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Receive data with length prefix.

        First reads 4-byte length, then reads full data.

        Args:
            timeout: Maximum time to wait in seconds.
                    None = blocking, 0 = non-blocking.

        Returns:
            Frame bytes, or None if no data (non-blocking).

        Raises:
            TransportError: If receive fails or not connected.
        """
        if not self._connected or self._client_socket is None:
            raise TransportError("Not connected")

        self._client_socket.settimeout(timeout if timeout is not None else None)

        try:
            # Read length prefix (4 bytes)
            length_data = self._recv_exact(LENGTH_PREFIX_LEN)
            if length_data is None:
                self._connected = False
                return None

            length = struct.unpack(">I", length_data)[0]

            if length > MAX_FRAME_SIZE:
                raise TransportError(f"Received invalid length: {length}")

            if length == 0:
                return b""

            # Read full data based on length
            data = self._recv_exact(length)
            if data is None:
                self._connected = False
                return None

            logger.debug(f"TCPTransport received {len(data)} bytes")
            return data

        except socket.timeout:
            return None
        except socket.error as e:
            self._connected = False
            raise TransportError(f"Receive failed: {e}")
        finally:
            self._client_socket.settimeout(None)

    def _recv_exact(self, n: int) -> Optional[bytes]:
        """Receive exactly n bytes.

        Handles partial reads (半包问题).

        Args:
            n: Number of bytes to receive.

        Returns:
            Exactly n bytes, or None if socket closed.
        """
        chunks: list[bytes] = []
        remaining = n

        while remaining > 0:
            try:
                chunk = self._client_socket.recv(remaining)
            except socket.error as e:
                if e.errno == 11:  # EAGAIN
                    continue
                return None

            if not chunk:
                return None

            chunks.append(chunk)
            remaining -= len(chunk)

        return b"".join(chunks)

    def close(self) -> None:
        """Close the connection."""
        logger.info("Closing TCP transport")

        # Close client connection
        if self._client_socket:
            try:
                self._client_socket.close()
            except Exception:
                pass
            self._client_socket = None

        # Close server socket
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
            self._server_socket = None

        self._connected = False
        logger.info("TCP transport closed")

    def is_connected(self) -> bool:
        """Check if transport is connected.

        Returns:
            True if connected, False otherwise.
        """
        return self._connected

    def __repr__(self) -> str:
        return (
            f"TCPTransport(mode={self.mode}, host={self.host}, port={self.port}, "
            f"connected={self._connected})"
        )
