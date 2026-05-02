"""TUN Virtual Network Interface Abstraction.

Provides a unified interface for TUN device operations.
Supports both real TUN devices (Linux) and mock devices for testing.
"""

import array
import fcntl
import os
import queue
from abc import ABC, abstractmethod
from typing import Optional

from ..common.errors import TunDeviceError
from ..common.logger import get_logger


logger = get_logger(__name__)

# Linux TUN device ioctl definitions
TUNSETIFF = 0x400454CA  # Set TUN device interface name
IFF_TUN = 0x0001        # TUN device (no packet info)
IFF_NO_PI = 0x1000      # No packet info (raw IP packet)


class TunDevice(ABC):
    """Abstract base class for TUN device operations.

    Unified interface for TUN virtual network interface.
    """

    @abstractmethod
    def open(self) -> None:
        """Open and configure the TUN device."""
        pass

    @abstractmethod
    def read_packet(self) -> Optional[bytes]:
        """Read an IP packet from the TUN device.

        Returns:
            Raw IP packet bytes, or None if no data available.
        """
        pass

    @abstractmethod
    def write_packet(self, packet: bytes) -> None:
        """Write an IP packet to the TUN device.

        Args:
            packet: Raw IP packet bytes.
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """Close the TUN device."""
        pass


class MockTunDevice(TunDevice):
    """Mock TUN device for testing without real TUN interface.

    Uses queue.Queue internally to simulate packet收发.
    Suitable for unit testing without root privileges.
    """

    def __init__(self, name: str = "tun0", mtu: int = 1400):
        """Initialize mock TUN device.

        Args:
            name: Device name (for compatibility/logging).
            mtu: Maximum transmission unit size.
        """
        self.name = name
        self.mtu = mtu
        self._rx_queue: queue.Queue[bytes] = queue.Queue()
        self._tx_packets: list[bytes] = []
        self._opened = False

    def open(self) -> None:
        """Open mock TUN device."""
        if self._opened:
            logger.warning(f"{self.name} already opened")
            return
        self._opened = True
        logger.info(f"MockTunDevice '{self.name}' opened (MTU={self.mtu})")

    def close(self) -> None:
        """Close mock TUN device."""
        if not self._opened:
            return
        self._opened = False
        while not self._rx_queue.empty():
            try:
                self._rx_queue.get_nowait()
            except queue.Empty:
                break
        self._tx_packets.clear()
        logger.info(f"MockTunDevice '{self.name}' closed")

    def read_packet(self) -> Optional[bytes]:
        """Read an IP packet from the mock device.

        Returns packets that were injected via inject_packet().
        Non-blocking - returns None if queue is empty.

        Returns:
            Raw IP packet bytes, or None if queue empty.
        """
        if not self._opened:
            raise TunDeviceError("Device not opened")

        try:
            packet = self._rx_queue.get_nowait()
            logger.debug(f"MockTunDevice read {len(packet)} bytes")
            return packet
        except queue.Empty:
            return None

    def write_packet(self, packet: bytes) -> None:
        """Write an IP packet to the mock device.

        Stores packets in _tx_packets list for inspection.

        Args:
            packet: Raw IP packet bytes.

        Raises:
            TunDeviceError: If packet too large.
        """
        if not self._opened:
            raise TunDeviceError("Device not opened")

        if len(packet) > self.mtu:
            raise TunDeviceError(f"Packet too large: {len(packet)} > MTU={self.mtu}")

        self._tx_packets.append(packet)
        logger.debug(f"MockTunDevice wrote {len(packet)} bytes")

    def inject_packet(self, packet: bytes) -> None:
        """Inject a packet into the RX queue (for testing).

        Args:
            packet: Raw IP packet bytes.

        Raises:
            TunDeviceError: If packet too large.
        """
        if len(packet) > self.mtu:
            raise TunDeviceError(f"Packet too large: {len(packet)} > MTU={self.mtu}")
        self._rx_queue.put(packet)
        logger.debug(f"MockTunDevice injected {len(packet)} bytes")

    def get_tx_packets(self) -> list[bytes]:
        """Get all packets from TX list (for testing inspection).

        Returns:
            List of transmitted packets.
        """
        return list(self._tx_packets)

    def pending_count(self) -> int:
        """Get number of pending packets in RX queue.

        Returns:
            Number of packets available to read.
        """
        return self._rx_queue.qsize()


class LinuxTunDevice(TunDevice):
    """Real TUN device implementation for Linux.

    Requirements:
    - Linux kernel with /dev/net/tun support
    - CAP_NET_ADMIN capability (root or setcap)

    Usage:
        device = LinuxTunDevice(name="tun0", mtu=1400)
        device.open()
        # ... use read_packet() / write_packet() ...
        device.close()

    Note:
        This class only handles the TUN file descriptor.
        IP address configuration, routing, and NAT should be
        handled separately in the forwarding module.
    """

    def __init__(self, name: str = "tun0", mtu: int = 1400):
        """Initialize Linux TUN device.

        Args:
            name: TUN device name (e.g., tun0, tun1).
            mtu: Maximum transmission unit size.
        """
        self.name = name
        self.mtu = mtu
        self._fd: Optional[int] = None
        self._opened = False

    def open(self) -> None:
        """Open and configure the TUN device.

        Opens /dev/net/tun and configures the interface name.

        Raises:
            TunDeviceError: If device cannot be opened or configured.
        """
        if self._opened:
            logger.warning(f"{self.name} already opened")
            return

        # Open /dev/net/tun
        try:
            fd = os.open("/dev/net/tun", os.O_RDWR)
        except OSError as e:
            raise TunDeviceError(f"Cannot open /dev/net/tun: {e}")

        # Build ifreq structure for TUNSETIFF using struct
        # struct ifreq {
        #     char ifrname[IFNAMSIZ];  // 16 bytes, null-padded
        #     short ifr_flags;          // 2 bytes
        # }
        import struct
        name_bytes = self.name.encode("utf-8")[:15]  # max 15 chars + null
        name_bytes = name_bytes.ljust(16, b'\x00')  # pad to 16 bytes
        ifreq = struct.pack(
            "16sH",
            name_bytes,
            IFF_TUN | IFF_NO_PI
        )

        try:
            fcntl.ioctl(fd, TUNSETIFF, ifreq)
        except OSError as e:
            os.close(fd)
            raise TunDeviceError(f"Cannot set TUN device name to '{self.name}': {e}")

        self._fd = fd
        self._opened = True
        logger.info(f"LinuxTunDevice '{self.name}' opened (FD={fd}, MTU={self.mtu})")

    def close(self) -> None:
        """Close the TUN device file descriptor."""
        if not self._opened:
            return

        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError as e:
                logger.warning(f"Error closing TUN fd: {e}")
            self._fd = None

        self._opened = False
        logger.info(f"LinuxTunDevice '{self.name}' closed")

    def read_packet(self) -> Optional[bytes]:
        """Read an IP packet from the TUN device.

        Reads raw IP packet from the TUN file descriptor.
        Non-blocking if O_NONBLOCK was set on fd.

        Returns:
            Raw IP packet bytes, or None if no data available (EAGAIN).

        Raises:
            TunDeviceError: If read fails.
        """
        if not self._opened or self._fd is None:
            raise TunDeviceError("Device not opened")

        try:
            packet = os.read(self._fd, self.mtu)
            logger.debug(f"LinuxTunDevice read {len(packet)} bytes")
            return packet
        except OSError as e:
            if e.errno == 11:  # EAGAIN
                return None
            raise TunDeviceError(f"Read error: {e}")

    def write_packet(self, packet: bytes) -> None:
        """Write an IP packet to the TUN device.

        Args:
            packet: Raw IP packet bytes.

        Raises:
            TunDeviceError: If write fails.
        """
        if not self._opened or self._fd is None:
            raise TunDeviceError("Device not opened")

        if len(packet) > self.mtu:
            raise TunDeviceError(f"Packet too large: {len(packet)} > MTU={self.mtu}")

        try:
            os.write(self._fd, packet)
            logger.debug(f"LinuxTunDevice wrote {len(packet)} bytes")
        except OSError as e:
            raise TunDeviceError(f"Write error: {e}")

    def set_nonblocking(self) -> None:
        """Set the TUN fd to non-blocking mode.

        After calling this, read_packet() will return None
        immediately if no data available.

        Raises:
            TunDeviceError: If fcntl fails.
        """
        if self._fd is None:
            raise TunDeviceError("Device not opened")

        try:
            flags = fcntl.fcntl(self._fd, fcntl.F_GETFL)
            fcntl.fcntl(self._fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            logger.debug(f"LinuxTunDevice '{self.name}' set to non-blocking")
        except OSError as e:
            raise TunDeviceError(f"Cannot set non-blocking: {e}")


def create_tun_device(
    name: str = "tun0",
    mtu: int = 1400,
    use_mock: bool = True,
) -> TunDevice:
    """Factory function to create appropriate TUN device.

    Args:
        name: Device name.
        mtu: Maximum transmission unit size.
        use_mock: If True, create MockTunDevice; if False, create LinuxTunDevice.

    Returns:
        TunDevice instance.
    """
    if use_mock:
        logger.info(f"Creating MockTunDevice (name={name}, mtu={mtu})")
        return MockTunDevice(name=name, mtu=mtu)
    else:
        logger.info(f"Creating LinuxTunDevice (name={name}, mtu={mtu})")
        return LinuxTunDevice(name=name, mtu=mtu)
