"""SSH Transport Implementation.

Provides SSH-based transport for the VPN tunnel.
Uses paramiko for SSH connectivity.
"""

import struct
import socket
from pathlib import Path
from typing import Optional

import paramiko

from ..common.errors import TransportError
from ..common.logger import get_logger
from .base import Transport


logger = get_logger(__name__)

# Length prefix: 4 bytes, big-endian unsigned int
LENGTH_PREFIX_LEN = 4


class SSHTransport(Transport):
    """SSH-based transport implementation.

    Client connects to SSH server and opens a channel for data transfer.

    Protocol:
        - Each send() sends: [4-byte length][frame bytes]
        - Each recv() reads: 4-byte length, then full frame bytes
        - Handles partial reads (半包问题)

    Args:
        host: SSH server hostname or IP.
        port: SSH server port.
        username: SSH username for authentication.
        ssh_key_path: Path to private key file.
        password: SSH password (optional, if not using key auth).
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 22,
        username: Optional[str] = None,
        ssh_key_path: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.ssh_key_path = Path(ssh_key_path).expanduser() if ssh_key_path else None
        self.password = password

        self._client: Optional[paramiko.SSHClient] = None
        self._channel: Optional[paramiko.Channel] = None
        self._connected = False

    def connect(self) -> None:
        """Establish SSH connection and open a channel.

        Raises:
            TransportError: If connection fails.
        """
        if self._connected:
            logger.warning("Already connected")
            return

        logger.info(f"Connecting to SSH server {self.host}:{self.port}")

        # Create SSH client
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Load system known hosts
        try:
            known_hosts = paramiko.HostKeys()
            # Try loading system known hosts
            system_known_hosts = "/etc/ssh/ssh_known_hosts"
            if Path(system_known_hosts).exists():
                known_hosts.load(system_known_hosts)
            # Also try user known hosts
            user_known_hosts = Path.home() / ".ssh" / "known_hosts"
            if user_known_hosts.exists():
                known_hosts.load(str(user_known_hosts))
            self._client._hostkeys = known_hosts
        except Exception as e:
            logger.warning(f"Could not load known hosts: {e}")

        # Build connect kwargs
        connect_kwargs: dict = {
            "hostname": self.host,
            "port": self.port,
            "look_for_keys": False,
            "allow_agent": False,
        }

        if self.ssh_key_path and self.ssh_key_path.exists():
            connect_kwargs["key_filename"] = str(self.ssh_key_path)
        elif self.password:
            connect_kwargs["password"] = self.password
        else:
            # Allow looking for keys in ~/.ssh
            connect_kwargs["look_for_keys"] = True

        # Connect
        try:
            self._client.connect(**connect_kwargs)
        except paramiko.AuthenticationException as e:
            raise TransportError(f"SSH authentication failed: {e}")
        except paramiko.SSHException as e:
            raise TransportError(f"SSH connection failed: {e}")
        except socket.error as e:
            raise TransportError(f"Cannot connect to {self.host}:{self.port}: {e}")

        logger.info("SSH connection established")

        # Open an interactive session channel
        try:
            self._channel = self._client.open_session()
            self._channel.settimeout(None)  # Blocking mode by default
        except paramiko.SSHException as e:
            self._client.close()
            raise TransportError(f"Cannot open SSH channel: {e}")

        logger.info("SSH channel opened")
        self._connected = True

    def send(self, data: bytes) -> None:
        """Send data with length prefix through SSH channel.

        Format: [4-byte length (big-endian)][data bytes]

        Args:
            data: Frame bytes to send.

        Raises:
            TransportError: If send fails or not connected.
        """
        if not self._connected or self._channel is None:
            raise TransportError("Not connected")

        try:
            # Pack length as 4-byte big-endian unsigned int
            length_prefix = struct.pack(">I", len(data))
            self._channel.sendall(length_prefix + data)
            logger.debug(f"SSHTransport sent {len(data)} bytes (with 4-byte prefix)")
        except (socket.error, paramiko.SSHException) as e:
            self._connected = False
            raise TransportError(f"Send failed: {e}")

    def recv(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Receive data with length prefix from SSH channel.

        First reads 4-byte length, then reads full data.

        Args:
            timeout: Maximum time to wait in seconds.
                    None = blocking, 0 = non-blocking.

        Returns:
            Frame bytes, or None if no data (non-blocking).

        Raises:
            TransportError: If receive fails or not connected.
        """
        if not self._connected or self._channel is None:
            raise TransportError("Not connected")

        self._channel.settimeout(timeout if timeout is not None else None)

        try:
            # Read length prefix (4 bytes)
            length_data = self._recv_exact(LENGTH_PREFIX_LEN)
            if length_data is None:
                self._connected = False
                return None

            length = struct.unpack(">I", length_data)[0]
            if length > 10 * 1024 * 1024:  # Sanity check: max 10MB
                raise TransportError(f"Received invalid length: {length}")

            # Read full data based on length
            data = self._recv_exact(length)
            if data is None:
                self._connected = False
                return None

            logger.debug(f"SSHTransport received {len(data)} bytes")
            return data

        except socket.timeout:
            return None
        except (socket.error, paramiko.SSHException) as e:
            self._connected = False
            raise TransportError(f"Receive failed: {e}")
        finally:
            self._channel.settimeout(None)

    def _recv_exact(self, n: int) -> Optional[bytes]:
        """Receive exactly n bytes from channel.

        Handles partial reads (半包问题).

        Args:
            n: Number of bytes to receive.

        Returns:
            Exactly n bytes, or None if channel closed.
        """
        chunks: list[bytes] = []
        remaining = n

        while remaining > 0:
            chunk = self._channel.recv(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)

        return b"".join(chunks)

    def close(self) -> None:
        """Close SSH channel and connection."""
        logger.info("Closing SSH transport")

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
        logger.info("SSH transport closed")

    def is_connected(self) -> bool:
        """Check if transport is connected.

        Returns:
            True if connected, False otherwise.
        """
        if not self._connected or self._channel is None:
            return False

        if self._channel.closed:
            self._connected = False
            return False

        return self._connected

    def __repr__(self) -> str:
        return (
            f"SSHTransport(host={self.host}, port={self.port}, "
            f"username={self.username})"
        )
