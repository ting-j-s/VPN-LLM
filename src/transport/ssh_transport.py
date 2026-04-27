"""SSH Transport Implementation.

Provides SSH-based transport for the VPN tunnel.
Uses paramiko for SSH connectivity and port forwarding.
"""

import socket
import time
from pathlib import Path
from typing import Optional

import paramiko

from ..common.errors import TransportError, ConnectionError
from ..common.frame import Frame
from ..common.logger import setup_logger
from .base import BaseTransport


logger = setup_logger(__name__)


class SSHTransport(BaseTransport):
    """SSH-based transport implementation.

    Uses SSH local port forwarding to create a tunnel.
    Client connects to server via SSH and creates a local port forward.

    Architecture:
        Client: localhost:local_port -> SSH server -> server:server_port
        Server listens on server_port and forwards to client via the SSH channel.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 22,
        username: Optional[str] = None,
        key_file: Optional[str] = None,
        password: Optional[str] = None,
        remote_host: str = "127.0.0.1",
        remote_port: int = 2222,
    ):
        """Initialize SSH transport.

        Args:
            host: SSH server hostname or IP.
            port: SSH server port.
            username: SSH username for authentication.
            key_file: Path to private key file.
            password: SSH password (if not using key auth).
            remote_host: Remote host to forward to (typically 127.0.0.1).
            remote_port: Remote port to forward to.
        """
        self.host = host
        self.port = port
        self.username = username
        self.key_file = Path(key_file).expanduser() if key_file else None
        self.password = password
        self.remote_host = remote_host
        self.remote_port = remote_port

        self._client: Optional[paramiko.SSHClient] = None
        self._transport: Optional[paramiko.Transport] = None
        self._channel: Optional[paramiko.Channel] = None
        self._connected = False

    def connect(self) -> None:
        """Establish SSH connection and set up port forwarding.

        Raises:
            TransportError: If SSH connection fails.
            ConnectionError: If authentication fails.
        """
        if self._connected:
            logger.warning("Already connected")
            return

        logger.info(f"Connecting to SSH server {self.host}:{self.port}")

        # Create SSH client
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Load host keys
        try:
            host_keys = paramiko.HostKeys()
            known_hosts = Path.home() / ".ssh" / "known_hosts"
            if known_hosts.exists():
                host_keys.load(str(known_hosts))
            self._client._hostkeys = host_keys
        except Exception as e:
            logger.warning(f"Could not load known hosts: {e}")

        # Connect to SSH server
        connect_kwargs: dict = {
            "hostname": self.host,
            "port": self.port,
            "look_for_keys": False,
            "allow_agent": False,
        }

        if self.key_file and self.key_file.exists():
            connect_kwargs["key_filename"] = str(self.key_file)
        elif self.password:
            connect_kwargs["password"] = self.password
        else:
            connect_kwargs["look_for_keys"] = True

        try:
            self._client.connect(**connect_kwargs)
        except paramiko.AuthenticationException as e:
            raise ConnectionError(f"SSH authentication failed: {e}")
        except paramiko.SSHException as e:
            raise TransportError(f"SSH connection failed: {e}")
        except socket.error as e:
            raise TransportError(f"Cannot connect to {self.host}:{self.port}: {e}")

        logger.info("SSH connection established")

        # Open a channel for port forwarding
        # We use reverse port forwarding: server -> client
        try:
            self._channel = self._client.open_channel("direct-tcpip", (self.remote_host, self.remote_port), ("", 0))
        except paramiko.SSHException as e:
            raise TransportError(f"Cannot open SSH channel: {e}")

        logger.info(f"SSH channel opened for {self.remote_host}:{self.remote_port}")
        self._connected = True

    def disconnect(self) -> None:
        """Close SSH connection gracefully."""
        if not self._connected:
            return

        logger.info("Disconnecting SSH transport")

        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass
            self._channel = None

        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

        self._connected = False
        logger.info("SSH transport disconnected")

    def send_frame(self, frame: Frame) -> None:
        """Send a frame through the SSH channel.

        Args:
            frame: Frame object to send.

        Raises:
            TransportError: If send fails or not connected.
        """
        if not self._connected or self._channel is None:
            raise TransportError("Not connected")

        try:
            data = frame.to_bytes()
            self._channel.sendall(data)
            logger.debug(f"Sent frame: {frame}")
        except (socket.error, paramiko.SSHException) as e:
            self._connected = False
            raise TransportError(f"Send failed: {e}")

    def recv_frame(self, timeout: Optional[float] = None) -> Optional[Frame]:
        """Receive a frame from the SSH channel.

        Args:
            timeout: Maximum time to wait (seconds). None means blocking.

        Returns:
            Frame object, or None if no data available (non-blocking).

        Raises:
            TransportError: If receive fails.
        """
        if not self._connected or self._channel is None:
            raise TransportError("Not connected")

        # Set channel timeout
        if timeout is not None:
            self._channel.settimeout(timeout)

        try:
            # Read frame header first (16 bytes)
            header = self._channel.recv(16)
            if not header:
                self._connected = False
                return None

            # Read remaining bytes based on length in header
            # header[6:10] contains length (4 bytes, big-endian)
            import struct
            length = struct.unpack(">I", header[6:10])[0]
            total_len = 16 + length

            data = header
            while len(data) < total_len:
                chunk = self._channel.recv(total_len - len(data))
                if not chunk:
                    self._connected = False
                    return None
                data += chunk

            frame = Frame.from_bytes(data)
            logger.debug(f"Received frame: {frame}")
            return frame

        except socket.timeout:
            return None
        except (socket.error, paramiko.SSHException) as e:
            self._connected = False
            raise TransportError(f"Receive failed: {e}")
        finally:
            if timeout is not None:
                self._channel.settimeout(None)

    def is_connected(self) -> bool:
        """Check if SSH transport is connected.

        Returns:
            True if connected, False otherwise.
        """
        if not self._connected or self._channel is None:
            return False

        # Check if channel is still open
        if self._channel.closed:
            self._connected = False
            return False

        return self._connected

    def __repr__(self) -> str:
        return (
            f"SSHTransport(host={self.host}, port={self.port}, "
            f"remote={self.remote_host}:{self.remote_port})"
        )


class SSHServerTransport(BaseTransport):
    """SSH server-side transport implementation.

    Runs as an SSH server subsystem or accepts incoming SSH channels.
    This is a stub implementation for reference - actual SSH server
    integration requires running an SSH daemon and accepting incoming
    connections rather than initiating outgoing ones.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 2222,
        key_file: Optional[str] = None,
    ):
        """Initialize SSH server transport.

        Args:
            host: Bind address.
            port: Listen port.
            key_file: Path to SSH host private key.
        """
        self.host = host
        self.port = port
        self.key_file = Path(key_file).expanduser() if key_file else None

        self._server_socket: Optional[socket.socket] = None
        self._client_channel: Optional[paramiko.Channel] = None
        self._connected = False

    def connect(self) -> None:
        """Start listening for SSH connections.

        Note: For server-side, this creates a listening socket.
        Actual connection acceptance is done via accept().
        """
        if self._connected:
            return

        logger.info(f"SSH server transport binding to {self.host}:{self.port}")

        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((self.host, self.port))
        self._server_socket.listen(5)

        self._connected = True
        logger.info(f"SSH server transport listening on {self.host}:{self.port}")

    def accept(self, timeout: Optional[float] = None) -> None:
        """Accept an incoming SSH connection.

        Args:
            timeout: Maximum time to wait for connection.

        Raises:
            TransportError: If accept fails.
        """
        if not self._connected or self._server_socket is None:
            raise TransportError("Server not listening")

        self._server_socket.settimeout(timeout)

        try:
            client_socket, addr = self._server_socket.accept()
            logger.info(f"Accepted connection from {addr}")

            # Wrap in paramiko
            self._client_channel = paramiko.Channel(0)
            self._client_channel._conn = client_socket
            self._client_channel._recv_buffer = b""

            self._connected = True

        except socket.timeout:
            raise TransportError("Accept timeout")
        except Exception as e:
            raise TransportError(f"Accept failed: {e}")
        finally:
            self._server_socket.settimeout(None)

    def disconnect(self) -> None:
        """Stop the SSH server transport."""
        if self._client_channel:
            try:
                self._client_channel.close()
            except Exception:
                pass
            self._client_channel = None

        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
            self._server_socket = None

        self._connected = False
        logger.info("SSH server transport stopped")

    def send_frame(self, frame: Frame) -> None:
        """Send a frame to the SSH client.

        Args:
            frame: Frame object to send.

        Raises:
            TransportError: If send fails.
        """
        if self._client_channel is None:
            raise TransportError("No client connected")

        try:
            data = frame.to_bytes()
            self._client_channel.sendall(data)
        except Exception as e:
            raise TransportError(f"Send failed: {e}")

    def recv_frame(self, timeout: Optional[float] = None) -> Optional[Frame]:
        """Receive a frame from the SSH client.

        Args:
            timeout: Maximum time to wait.

        Returns:
            Frame object, or None if no data.
        """
        if self._client_channel is None:
            raise TransportError("No client connected")

        if timeout is not None:
            self._client_channel.settimeout(timeout)

        try:
            header = self._client_channel.recv(16)
            if not header:
                return None

            import struct
            length = struct.unpack(">I", header[6:10])[0]
            total_len = 16 + length

            data = header
            while len(data) < total_len:
                chunk = self._client_channel.recv(total_len - len(data))
                if not chunk:
                    return None
                data += chunk

            return Frame.from_bytes(data)

        except socket.timeout:
            return None
        except Exception as e:
            raise TransportError(f"Receive failed: {e}")
        finally:
            if timeout is not None:
                self._client_channel.settimeout(None)

    def is_connected(self) -> bool:
        """Check if a client is connected."""
        return self._client_channel is not None and not self._client_channel.closed

    def __repr__(self) -> str:
        return f"SSHServerTransport(host={self.host}, port={self.port})"
