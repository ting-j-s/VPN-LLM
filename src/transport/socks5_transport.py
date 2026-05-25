"""SOCKS5 Transport Implementation.

Provides SOCKS5-based transport for the VPN tunnel.
Supports both client (connect via proxy) and server (accept) modes.

Protocol:
  - Handshake: RFC 1928 and RFC 1929 (no-auth / username-password)
  - After SOCKS5 CONNECT is established, data is framed with a 4-byte
    big-endian length prefix (same as TCP/TLS transports).
"""

import socket
import struct
from typing import Optional

from ..common.errors import TransportError, TransportTimeout
from ..common.logger import get_logger
from .base import Transport


logger = get_logger(__name__)

# SOCKS5 constants
SOCKS5_VERSION = 0x05
SOCKS5_AUTH_NO_AUTH = 0x00
SOCKS5_AUTH_USER_PASS = 0x02
SOCKS5_CMD_CONNECT = 0x01
SOCKS5_ATYP_IPV4 = 0x01
SOCKS5_ATYP_DOMAIN = 0x03
SOCKS5_REP_SUCCESS = 0x00

# Length prefix: 4 bytes, big-endian unsigned int
LENGTH_PREFIX_LEN = 4
MAX_FRAME_SIZE = 10 * 1024 * 1024  # 10 MB


class Socks5Transport(Transport):
    """SOCKS5-based transport implementation.

    Supports two modes:
    - Client mode: connect to a SOCKS5 proxy, request a CONNECT to the
      VPN server, then use the established stream.
    - Server mode: bind/listen, then accept a connection that speaks
      SOCKS5 (handshake + CONNECT).

    After the SOCKS5 handshake, data is exchanged with a length prefix
    (4 bytes, big-endian) in each direction, matching the TCP/TLS
    transport framing.

    Usage (client)::

        transport = Socks5Transport(
            mode="client",
            proxy_host="127.0.0.1",
            proxy_port=1080,
            target_host="vpn-server.example.com",
            target_port=2226,
            username="user",
            password="pass",
        )
        transport.connect()
        # ... use send/recv ...
        transport.close()

    Usage (server)::

        transport = Socks5Transport(
            mode="server",
            listen_host="0.0.0.0",
            listen_port=2226,
            username="user",
            password="pass",
        )
        transport.connect()  # binds and listens
        transport.accept()   # waits for client + handshake
        # ... use send/recv ...
        transport.close()
    """

    MODE_CLIENT = "client"
    MODE_SERVER = "server"

    def __init__(
        self,
        mode: str = MODE_CLIENT,
        proxy_host: str = "127.0.0.1",
        proxy_port: int = 1080,
        target_host: Optional[str] = None,
        target_port: Optional[int] = None,
        listen_host: str = "0.0.0.0",
        listen_port: int = 2226,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        if mode not in (self.MODE_CLIENT, self.MODE_SERVER):
            raise TransportError(
                f"Invalid mode: {mode}. Must be 'client' or 'server'"
            )

        self.mode = mode
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.target_host = target_host
        self.target_port = target_port
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.username = username
        self.password = password

        self._sock: Optional[socket.socket] = None
        self._server_sock: Optional[socket.socket] = None
        self._connected = False

    # ---- Transport interface ----------------------------------------------

    def connect(self) -> None:
        """Connect to proxy (client) or start listening (server)."""
        if self._connected:
            logger.warning("Already connected")
            return

        if self.mode == self.MODE_CLIENT:
            self._connect_client()
        else:
            self._start_server()

    def accept(self, timeout: Optional[float] = None) -> None:
        """Accept an incoming SOCKS5 connection (server mode only)."""
        if self.mode != self.MODE_SERVER:
            raise TransportError("accept() is only available in server mode")
        if self._server_sock is None:
            raise TransportError("Server not listening, call connect() first")

        self._server_sock.settimeout(timeout)
        try:
            client_sock, addr = self._server_sock.accept()
        except socket.timeout:
            raise TransportError("Accept timeout")
        except socket.error as e:
            raise TransportError(f"Accept failed: {e}")
        finally:
            if self._server_sock:
                self._server_sock.settimeout(None)

        try:
            self._server_handshake(client_sock)
        except Exception:
            client_sock.close()
            raise

        self._sock = client_sock
        self._connected = True
        logger.info(f"SOCKS5 connection accepted from {addr}")

    def send(self, data: bytes) -> None:
        """Send data with length prefix."""
        if not self._connected or self._sock is None:
            raise TransportError("Not connected")

        try:
            length_prefix = struct.pack(">I", len(data))
            self._sock.sendall(length_prefix + data)
            logger.debug(f"Socks5Transport sent {len(data)} bytes")
        except socket.error as e:
            self._connected = False
            raise TransportError(f"Send failed: {e}")

    def recv(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Receive data with length prefix.

        Returns:
            Frame bytes, or None if connection closed.
        Raises:
            TransportTimeout: if timeout expires without data.
            TransportError: on other errors.
        """
        if not self._connected or self._sock is None:
            raise TransportError("Not connected")

        self._sock.settimeout(timeout if timeout is not None else None)

        try:
            # Read 4-byte length
            length_data = self._recv_exact(LENGTH_PREFIX_LEN)
            if length_data is None:
                self._connected = False
                return None

            length = struct.unpack(">I", length_data)[0]
            if length > MAX_FRAME_SIZE:
                raise TransportError(f"Received invalid length: {length}")
            if length == 0:
                return b""

            data = self._recv_exact(length)
            if data is None:
                self._connected = False
                return None

            logger.debug(f"Socks5Transport received {len(data)} bytes")
            return data
        except socket.timeout:
            raise TransportTimeout("Receive timeout")
        except TimeoutError:
            raise TransportTimeout("Receive timeout")
        except ConnectionResetError:
            self._connected = False
            logger.debug("SOCKS5 connection reset by peer")
            return None
        except BrokenPipeError:
            self._connected = False
            logger.debug("SOCKS5 connection broken")
            return None
        except OSError as e:
            if e.errno == 104:  # ECONNRESET
                self._connected = False
                logger.debug("SOCKS5 connection reset (ECONNRESET)")
                return None
            self._connected = False
            raise TransportError(f"Receive failed: {e}")
        finally:
            if self._sock:
                self._sock.settimeout(None)

    def close(self) -> None:
        """Close the transport and underlying sockets."""
        logger.info("Closing SOCKS5 transport")

        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None

        self._connected = False

    def is_connected(self) -> bool:
        """Return connection state."""
        return self._connected

    def __repr__(self) -> str:
        return (
            f"Socks5Transport(mode={self.mode}, host={self.listen_host if self.mode == self.MODE_SERVER else self.proxy_host},"
            f" port={self.listen_port if self.mode == self.MODE_SERVER else self.proxy_port})"
        )

    # ---- Client connection ------------------------------------------------

    def _connect_client(self) -> None:
        logger.info(
            "SOCKS5 client connecting to proxy %s:%d for target %s:%d",
            self.proxy_host, self.proxy_port,
            self.target_host, self.target_port,
        )
        sock = None
        try:
            sock = socket.create_connection((self.proxy_host, self.proxy_port))
            self._client_handshake(sock)
            self._client_connect_request(sock, self.target_host, self.target_port)
        except TransportError:
            if sock:
                sock.close()
            raise
        except Exception as e:
            if sock:
                sock.close()
            raise TransportError(str(e)) from e

        self._sock = sock
        self._connected = True
        logger.info("SOCKS5 client connected")

    def _client_handshake(self, sock: socket.socket) -> None:
        methods = [SOCKS5_AUTH_NO_AUTH]
        if self.username and self.password:
            methods.append(SOCKS5_AUTH_USER_PASS)

        request = struct.pack("!BB", SOCKS5_VERSION, len(methods)) + bytes(methods)
        sock.sendall(request)

        resp = self._recv_all(sock, 2)
        if resp is None:
            raise TransportError("SOCKS5 handshake failed: connection closed")
        ver, method = struct.unpack("!BB", resp)
        if ver != SOCKS5_VERSION:
            raise TransportError(f"SOCKS5 server version mismatch: {ver}")

        if method == SOCKS5_AUTH_NO_AUTH:
            return
        elif method == SOCKS5_AUTH_USER_PASS:
            self._client_auth_username_password(sock)
        else:
            raise TransportError(f"SOCKS5 server requires unsupported auth method: {method}")

    def _client_auth_username_password(self, sock: socket.socket) -> None:
        if not self.username or not self.password:
            raise TransportError("SOCKS5 server requires username/password, but none provided")

        user_bytes = self.username.encode("ascii")
        pass_bytes = self.password.encode("ascii")
        if len(user_bytes) > 255 or len(pass_bytes) > 255:
            raise TransportError("Username or password too long")

        req = (
            b"\x01"  # version 1
            + bytes([len(user_bytes)])
            + user_bytes
            + bytes([len(pass_bytes)])
            + pass_bytes
        )
        sock.sendall(req)

        resp = self._recv_all(sock, 2)
        if resp is None:
            raise TransportError("Username/password auth failed: connection closed")
        ver, status = struct.unpack("!BB", resp)
        if ver != 0x01:
            raise TransportError(f"Username/password version mismatch: {ver}")
        if status != 0x00:
            raise TransportError("Username/password authentication failed")

    def _client_connect_request(
        self, sock: socket.socket, target_host: Optional[str], target_port: Optional[int]
    ) -> None:
        if not target_host or target_port is None:
            raise TransportError("Client mode requires target_host and target_port")

        # Resolve address type
        try:
            ip_bytes = socket.inet_aton(target_host)
            atyp = SOCKS5_ATYP_IPV4
            addr_data = ip_bytes
        except OSError:
            domain_bytes = target_host.encode("ascii")
            atyp = SOCKS5_ATYP_DOMAIN
            addr_data = bytes([len(domain_bytes)]) + domain_bytes

        request = (
            struct.pack("!BBBB", SOCKS5_VERSION, SOCKS5_CMD_CONNECT, 0x00, atyp)
            + addr_data
            + struct.pack("!H", target_port)
        )
        sock.sendall(request)

        # Read reply header (4 bytes: ver, status, reserved, atyp)
        resp = self._recv_all(sock, 4)
        if resp is None:
            raise TransportError("SOCKS5 connect request failed: connection closed")
        ver, status, rsv, bnd_atyp = struct.unpack("!BBBB", resp)
        if ver != SOCKS5_VERSION:
            raise TransportError(f"SOCKS5 reply version mismatch: {ver}")
        if status != SOCKS5_REP_SUCCESS:
            raise TransportError(f"SOCKS5 connect error: reply code {status}")

        # Read bound address (discard for now)
        if bnd_atyp == SOCKS5_ATYP_IPV4:
            if self._recv_all(sock, 4) is None:
                raise TransportError("SOCKS5 connect reply truncated")
            if self._recv_all(sock, 2) is None:
                raise TransportError("SOCKS5 connect reply truncated")
        elif bnd_atyp == SOCKS5_ATYP_DOMAIN:
            len_byte = self._recv_all(sock, 1)
            if len_byte is None:
                raise TransportError("SOCKS5 connect reply truncated")
            domain_len = ord(len_byte)
            if self._recv_all(sock, domain_len) is None:
                raise TransportError("SOCKS5 connect reply truncated")
            if self._recv_all(sock, 2) is None:
                raise TransportError("SOCKS5 connect reply truncated")
        else:
            raise TransportError(f"SOCKS5 unsupported address type in reply: {bnd_atyp}")

    # ---- Server side ------------------------------------------------------

    def _start_server(self) -> None:
        logger.info("SOCKS5 server binding to %s:%d", self.listen_host, self.listen_port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.listen_host, self.listen_port))
            sock.listen(5)
        except socket.error as e:
            sock.close()
            raise TransportError(f"Cannot bind to {self.listen_host}:{self.listen_port}: {e}")

        self._server_sock = sock
        logger.info("SOCKS5 server listening on %s:%d", self.listen_host, self.listen_port)

    def _server_handshake(self, client: socket.socket) -> None:
        # Read greeting
        resp = self._recv_all(client, 2)
        if resp is None:
            raise TransportError("SOCKS5 handshake: no greeting")
        ver, nmethods = struct.unpack("!BB", resp)
        if ver != SOCKS5_VERSION:
            raise TransportError(f"SOCKS5 client version mismatch: {ver}")

        methods = self._recv_all(client, nmethods)
        if methods is None:
            raise TransportError("SOCKS5 handshake: methods truncated")

        # Select method
        if SOCKS5_AUTH_NO_AUTH in methods:
            chosen = SOCKS5_AUTH_NO_AUTH
        elif SOCKS5_AUTH_USER_PASS in methods and self.username and self.password:
            chosen = SOCKS5_AUTH_USER_PASS
        else:
            client.sendall(struct.pack("!BB", SOCKS5_VERSION, 0xFF))
            raise TransportError("No acceptable authentication method")

        client.sendall(struct.pack("!BB", SOCKS5_VERSION, chosen))

        if chosen == SOCKS5_AUTH_USER_PASS:
            self._server_auth_username_password(client)

        # Handle CONNECT request
        self._server_handle_connect(client)

    def _server_auth_username_password(self, client: socket.socket) -> None:
        # Read version + username length
        resp = self._recv_all(client, 2)
        if resp is None:
            raise TransportError("Username/password auth: no data")
        ver, ulen = struct.unpack("!BB", resp)
        if ver != 0x01:
            raise TransportError(f"Username/password version mismatch: {ver}")

        username = self._recv_all(client, ulen)
        if username is None:
            raise TransportError("Username/password auth: username truncated")

        plen_byte = self._recv_all(client, 1)
        if plen_byte is None:
            raise TransportError("Username/password auth: password length missing")
        plen = ord(plen_byte)
        password = self._recv_all(client, plen)
        if password is None:
            raise TransportError("Username/password auth: password truncated")

        if (
            username.decode("ascii") == self.username
            and password.decode("ascii") == self.password
        ):
            client.sendall(b"\x01\x00")
        else:
            client.sendall(b"\x01\x01")
            raise TransportError("Username/password authentication failed")

    def _server_handle_connect(self, client: socket.socket) -> None:
        header = self._recv_all(client, 4)
        if header is None:
            raise TransportError("SOCKS5 CONNECT request: header truncated")
        ver, cmd, rsv, atyp = struct.unpack("!BBBB", header)
        if ver != SOCKS5_VERSION:
            raise TransportError(f"SOCKS5 CONNECT version mismatch: {ver}")
        if cmd != SOCKS5_CMD_CONNECT:
            raise TransportError(f"SOCKS5 unsupported command: {cmd}")

        # Read address
        if atyp == SOCKS5_ATYP_IPV4:
            addr = self._recv_all(client, 4)
            port = self._recv_all(client, 2)
        elif atyp == SOCKS5_ATYP_DOMAIN:
            len_byte = self._recv_all(client, 1)
            if len_byte is None:
                raise TransportError("SOCKS5 CONNECT: domain length missing")
            addr_len = ord(len_byte)
            addr = self._recv_all(client, addr_len)
            port = self._recv_all(client, 2)
        else:
            raise TransportError(f"SOCKS5 unsupported address type: {atyp}")

        if addr is None or port is None:
            raise TransportError("SOCKS5 CONNECT: address truncated")

        # Send success reply (bound to 0.0.0.0:0 as no actual forwarding)
        response = (
            struct.pack("!BBBB", SOCKS5_VERSION, SOCKS5_REP_SUCCESS, 0x00, SOCKS5_ATYP_IPV4)
            + socket.inet_aton("0.0.0.0")
            + struct.pack("!H", 0)
        )
        client.sendall(response)

    # ---- I/O helpers ------------------------------------------------------

    def _recv_exact(self, n: int) -> Optional[bytes]:
        """Receive exactly *n* bytes from the connected socket.

        Returns None if the connection is closed before *n* bytes are read.
        """
        return self._recv_all(self._sock, n)

    @staticmethod
    def _recv_all(sock: socket.socket, n: int) -> Optional[bytes]:
        """Read exactly *n* bytes from *sock*.

        Handles partial reads.  Returns None if connection closed.
        """
        chunks: list[bytes] = []
        remaining = n
        while remaining > 0:
            try:
                chunk = sock.recv(remaining)
            except socket.timeout:
                raise
            except OSError:
                return None
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
