"""TLS Transport Implementation.

Provides TLS-based transport for the VPN tunnel.
Supports both client (connect) and server (bind/listen/accept) modes.
Uses Python ssl module for secure communication.
"""

import socket
import struct
import ssl
from typing import Optional

from ..common.errors import TransportError
from ..common.logger import get_logger
from .base import Transport


logger = get_logger(__name__)

# Length prefix: 4 bytes, big-endian unsigned int
LENGTH_PREFIX_LEN = 4
# Max frame size: 10MB
MAX_FRAME_SIZE = 10 * 1024 * 1024


class TLSTransport(Transport):
    """TLS-based transport implementation.

    Supports two modes:
    - Client mode: connect to server with TLS
    - Server mode: bind/listen/accept with TLS

    Protocol:
        - Each send() sends: [4-byte length (big-endian)][data bytes]
        - Each recv() reads: 4-byte length, then full data bytes
        - Handles partial reads correctly (half-packet problem)

    Usage (client):
        transport = TLSTransport(
            mode="client",
            host="127.0.0.1",
            port=2223,
            certfile="/path/to/client.crt",
            keyfile="/path/to/client.key",
            cafile="/path/to/ca.crt",
            verify_server=True,
        )
        transport.connect()
        # ... use send/recv ...
        transport.close()

    Usage (server):
        transport = TLSTransport(
            mode="server",
            host="0.0.0.0",
            port=2223,
            certfile="/path/to/server.crt",
            keyfile="/path/to/server.key",
            cafile="/path/to/ca.crt",
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
        port: int = 2223,
        backlog: int = 5,
        certfile: Optional[str] = None,
        keyfile: Optional[str] = None,
        cafile: Optional[str] = None,
        verify_server: bool = True,
        server_hostname: Optional[str] = None,
    ):
        """Initialize TLS transport.

        Args:
            mode: "client" or "server".
            host: Host to bind or connect to.
            port: Port number.
            backlog: Listen backlog (server mode only).
            certfile: Path to certificate file (server required, client optional).
            keyfile: Path to private key file (server required).
            cafile: Path to CA certificate for verifying peer.
            verify_server: Whether to verify server certificate (client mode).
            server_hostname: Hostname to verify against server cert (client mode).
        """
        if mode not in (self.MODE_CLIENT, self.MODE_SERVER):
            raise TransportError(f"Invalid mode: {mode}. Must be 'client' or 'server'")

        self.mode = mode
        self.host = host
        self.port = port
        self.backlog = backlog
        self.certfile = certfile
        self.keyfile = keyfile
        self.cafile = cafile
        self.verify_server = verify_server
        self.server_hostname = server_hostname or host

        self._server_socket: Optional[ssl.SSLSocket] = None
        self._client_socket: Optional[ssl.SSLSocket] = None
        self._plain_socket: Optional[socket.socket] = None
        self._connected = False

    def connect(self) -> None:
        """Connect or start listening.

        Client mode: connect to server with TLS.
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

    def _create_ssl_context(self) -> ssl.SSLContext:
        """Create SSL context based on mode.

        Returns:
            Configured SSLContext.
        """
        if self.mode == self.MODE_SERVER:
            # Server context requires cert and key
            if not self.certfile or not self.keyfile:
                raise TransportError(
                    "Server mode requires certfile and keyfile"
                )
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile=self.certfile, keyfile=self.keyfile)
            if self.cafile:
                context.load_verify_locations(cafile=self.cafile)
                context.verify_mode = ssl.CERT_REQUIRED
        else:
            # Client context
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            if self.certfile:
                context.load_cert_chain(certfile=self.certfile, keyfile=self.keyfile)
            if self.cafile:
                context.load_verify_locations(cafile=self.cafile)
                if self.verify_server:
                    context.verify_mode = ssl.CERT_REQUIRED
                    context.check_hostname = True
                else:
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
            else:
                # No CA file - default to no verification
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            if self.server_hostname:
                context.server_hostname = self.server_hostname

        return context

    def _connect_client(self) -> None:
        """Connect as client to server with TLS."""
        logger.info(f"TLS client connecting to {self.host}:{self.port}")

        plain_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        plain_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            context = self._create_ssl_context()
            wrapped_sock = context.wrap_socket(
                plain_sock,
                server_hostname=self.server_hostname,
            )
            wrapped_sock.connect((self.host, self.port))
        except ssl.SSLError as e:
            plain_sock.close()
            raise TransportError(f"TLS connection failed: {e}")

        self._plain_socket = plain_sock
        self._client_socket = wrapped_sock
        self._connected = True
        logger.info("TLS client connected")

    def _start_server(self) -> None:
        """Start TLS server (bind/listen)."""
        logger.info(f"TLS server binding to {self.host}:{self.port}")

        plain_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        plain_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            plain_sock.bind((self.host, self.port))
            plain_sock.listen(self.backlog)
        except socket.error as e:
            plain_sock.close()
            raise TransportError(f"Cannot bind to {self.host}:{self.port}: {e}")

        self._plain_socket = plain_sock
        logger.info(f"TLS server listening on {self.host}:{self.port}")

    def accept(self, timeout: Optional[float] = None) -> None:
        """Accept incoming connection (server mode only).

        Args:
            timeout: Maximum time to wait for connection.

        Raises:
            TransportError: If not in server mode or accept fails.
        """
        if self.mode != self.MODE_SERVER:
            raise TransportError("accept() is only available in server mode")

        if self._plain_socket is None:
            raise TransportError("Server not listening, call connect() first")

        logger.info("Waiting for TLS connection...")

        self._plain_socket.settimeout(timeout)

        try:
            plain_client, addr = self._plain_socket.accept()
        except socket.timeout:
            raise TransportError("Accept timeout")
        except socket.error as e:
            raise TransportError(f"Accept failed: {e}")
        finally:
            self._plain_socket.settimeout(None)

        try:
            context = self._create_ssl_context()
            client_sock = context.wrap_socket(plain_client, server_side=True)
        except ssl.SSLError as e:
            plain_client.close()
            raise TransportError(f"TLS handshake failed: {e}")

        self._client_socket = client_sock
        self._connected = True
        logger.info(f"TLS connection accepted from {addr}")

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
            logger.debug(f"TLSTransport sent {len(data)} bytes (with 4-byte prefix)")
        except (ssl.SSLError, socket.error) as e:
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

            logger.debug(f"TLSTransport received {len(data)} bytes")
            return data

        except socket.timeout:
            return None
        except (ssl.SSLError, socket.error) as e:
            self._connected = False
            raise TransportError(f"Receive failed: {e}")
        finally:
            self._client_socket.settimeout(None)

    def _recv_exact(self, n: int) -> Optional[bytes]:
        """Receive exactly n bytes.

        Handles partial reads (half-packet problem).

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
            except ssl.SSLError as e:
                if e.errno == ssl.SSL_ERROR_WANT_READ:
                    continue
                return None
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
        logger.info("Closing TLS transport")

        # Close client connection
        if self._client_socket:
            try:
                self._client_socket.close()
            except Exception:
                pass
            self._client_socket = None

        # Close server socket
        if self._plain_socket:
            try:
                self._plain_socket.close()
            except Exception:
                pass
            self._plain_socket = None

        self._connected = False
        logger.info("TLS transport closed")

    def is_connected(self) -> bool:
        """Check if transport is connected.

        Returns:
            True if connected, False otherwise.
        """
        return self._connected

    def __repr__(self) -> str:
        return (
            f"TLSTransport(mode={self.mode}, host={self.host}, port={self.port}, "
            f"connected={self._connected})"
        )