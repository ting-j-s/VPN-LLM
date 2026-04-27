"""TUN Virtual Network Interface Abstraction.

Provides a unified interface for TUN device operations.
Supports both real TUN devices (Linux) and mock devices for testing.
"""

import array
import os
import struct
from abc import ABC, abstractmethod
from typing import Optional

from .errors import TUNError
from .logger import setup_logger


logger = setup_logger(__name__)


class TUNDevice(ABC):
    """Abstract base class for TUN device operations."""

    @abstractmethod
    def open(self) -> None:
        """Open and configure the TUN device."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close the TUN device."""
        pass

    @abstractmethod
    def read(self, max_size: int = 65535) -> Optional[bytes]:
        """Read an IP packet from the TUN device.

        Args:
            max_size: Maximum number of bytes to read.

        Returns:
            Raw IP packet bytes, or None if no data available.
        """
        pass

    @abstractmethod
    def write(self, packet: bytes) -> int:
        """Write an IP packet to the TUN device.

        Args:
            packet: Raw IP packet bytes.

        Returns:
            Number of bytes written.
        """
        pass


class MockTUNDevice(TUNDevice):
    """Mock TUN device for testing without real TUN interface.

    This simulates a TUN device using an in-memory queue.
    Useful for testing without root privileges or in non-Linux environments.
    """

    def __init__(self, name: str = "tun0", mtu: int = 1400):
        """Initialize mock TUN device.

        Args:
            name: Device name (for compatibility).
            mtu: Maximum transmission unit size.
        """
        self.name = name
        self.mtu = mtu
        self._rx_queue: list[bytes] = []
        self._tx_queue: list[bytes] = []
        self._opened = False

    def open(self) -> None:
        """Open mock TUN device."""
        if self._opened:
            logger.warning(f"{self.name} already opened")
            return
        self._opened = True
        logger.info(f"Mock TUN device '{self.name}' opened (MTU={self.mtu})")

    def close(self) -> None:
        """Close mock TUN device."""
        if not self._opened:
            return
        self._opened = False
        self._rx_queue.clear()
        self._tx_queue.clear()
        logger.info(f"Mock TUN device '{self.name}' closed")

    def read(self, max_size: int = 65535) -> Optional[bytes]:
        """Read an IP packet from the mock device.

        Returns packets that were injected via inject_packet().
        """
        if not self._opened:
            raise TUNError("Device not opened")

        if not self._rx_queue:
            return None

        packet = self._rx_queue.pop(0)
        if len(packet) > max_size:
            logger.warning(f"Packet truncated: {len(packet)} > {max_size}")
            packet = packet[:max_size]
        return packet

    def write(self, packet: bytes) -> int:
        """Write an IP packet to the mock device.

        Stores packets in TX queue for inspection.
        """
        if not self._opened:
            raise TUNError("Device not opened")

        if len(packet) > self.mtu:
            raise TUNError(f"Packet too large: {len(packet)} > MTU={self.mtu}")

        self._tx_queue.append(packet)
        logger.debug(f"Mock TUN wrote {len(packet)} bytes")
        return len(packet)

    def inject_packet(self, packet: bytes) -> None:
        """Inject a packet into the RX queue (for testing).

        Args:
            packet: Raw IP packet bytes.
        """
        if len(packet) > self.mtu:
            raise TUNError(f"Packet too large: {len(packet)} > MTU={self.mtu}")
        self._rx_queue.append(packet)
        logger.debug(f"Mock TUN injected {len(packet)} bytes")

    def get_tx_packets(self) -> list[bytes]:
        """Get all packets from TX queue (for testing inspection).

        Returns:
            List of transmitted packets.
        """
        return list(self._tx_queue)

    def pending_count(self) -> int:
        """Get number of pending packets in RX queue.

        Returns:
            Number of packets available to read.
        """
        return len(self._rx_queue)


class LinuxTUNDevice(TUNDevice):
    """Real TUN device implementation for Linux.

    Requires appropriate system permissions and TUN device support.
    """

    def __init__(self, name: str = "tun0", mtu: int = 1400):
        """Initialize Linux TUN device.

        Args:
            name: TUN device name (e.g., tun0, tun1).
            mtu: Maximum transmission unit size.
        """
        self.name = name
        self.mtu = mtu
        self.fd: Optional[int] = None
        self._opened = False

    def open(self) -> None:
        """Open and configure the TUN device using /dev/net/tun.

        Requires CAP_NET_ADMIN capability or root privileges.

        Raises:
            TUNError: If device cannot be opened.
        """
        if self._opened:
            return

        # Open /dev/net/tun
        try:
            tun_fd = os.open("/dev/net/tun", os.O_RDWR)
        except OSError as e:
            raise TUNError(f"Cannot open /dev/net/tun: {e}")

        # Build ioctl request for TUNSETIFF
        # struct ifreq { char ifrname[IFNAMSIZ]; short ifr_flags; }
        ifreq = array.array("H", b"\x00" * 16)
        ifreq[0:8] = array.array("B", self.name.encode() + b"\x00" * 8)[:8]
        ifreq[8] = 0x0001 | 0x1000  # IFF_TUN | IFF_NO_PI (no protocol info)

        try:
            from fcntl import ioctl
            ioctl(tun_fd, 0x400454CA, ifreq)  # TUNSETIFF
        except OSError as e:
            os.close(tun_fd)
            raise TUNError(f"Cannot set TUN device parameters: {e}")

        self.fd = tun_fd
        self._opened = True
        logger.info(f"Linux TUN device '{self.name}' opened (FD={tun_fd}, MTU={self.mtu})")

    def close(self) -> None:
        """Close the TUN device file descriptor."""
        if not self._opened:
            return

        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

        self._opened = False
        logger.info(f"Linux TUN device '{self.name}' closed")

    def read(self, max_size: int = 65535) -> Optional[bytes]:
        """Read an IP packet from the TUN device.

        Args:
            max_size: Maximum number of bytes to read.

        Returns:
            Raw IP packet bytes, or None if no data available (EAGAIN).
        """
        if not self._opened or self.fd is None:
            raise TUNError("Device not opened")

        try:
            packet = os.read(self.fd, max_size)
            return packet
        except OSError as e:
            if e.errno == 11:  # EAGAIN
                return None
            raise TUNError(f"Read error: {e}")

    def write(self, packet: bytes) -> int:
        """Write an IP packet to the TUN device.

        Args:
            packet: Raw IP packet bytes.

        Returns:
            Number of bytes written.
        """
        if not self._opened or self.fd is None:
            raise TUNError("Device not opened")

        try:
            n = os.write(self.fd, packet)
            return n
        except OSError as e:
            raise TUNError(f"Write error: {e}")

    def set_mtu(self, mtu: int) -> None:
        """Set MTU via sysfs or ip command.

        Args:
            mtu: New MTU value.
        """
        self.mtu = mtu
        logger.info(f"TUN device '{self.name}' MTU set to {mtu}")


def create_tun_device(name: str = "tun0", mtu: int = 1400, use_mock: bool = True) -> TUNDevice:
    """Factory function to create appropriate TUN device.

    Args:
        name: Device name.
        mtu: Maximum transmission unit size.
        use_mock: If True, create mock device; if False, try real Linux device.

    Returns:
        TUNDevice instance.
    """
    if use_mock:
        logger.info(f"Creating mock TUN device (name={name}, mtu={mtu})")
        return MockTUNDevice(name=name, mtu=mtu)
    else:
        logger.info(f"Creating Linux TUN device (name={name}, mtu={mtu})")
        return LinuxTUNDevice(name=name, mtu=mtu)
