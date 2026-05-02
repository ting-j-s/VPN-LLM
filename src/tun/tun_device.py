"""TUN Virtual Network Interface Abstraction.

Provides a unified interface for TUN device operations.
Supports both real TUN devices (Linux) and mock devices for testing.
"""

import queue
from abc import ABC, abstractmethod
from typing import Optional

from ..common.errors import TunDeviceError
from ..common.logger import get_logger


logger = get_logger(__name__)


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
        Blocks if no packet available (queue behavior).

        Returns:
            Raw IP packet bytes, or None if queue is empty (non-blocking check).
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
    - Will raise TunDeviceError if permissions insufficient

    Note:
        This is a placeholder implementation.
        Full implementation requires:
        - Opening /dev/net/tun
        - ioctl TUNSETIFF to configure interface
        - Setting up interface IP and routes
        - Configuring proper MTU
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

        Raises:
            TunDeviceError: If device cannot be opened or configured.
            NotImplementedError: Placeholder - not yet implemented.
        """
        raise NotImplementedError(
            "LinuxTunDevice is not yet implemented. "
            "Use MockTunDevice for testing."
        )

    def read_packet(self) -> Optional[bytes]:
        """Read an IP packet from the TUN device.

        Returns:
            Raw IP packet bytes, or None if no data available.

        Raises:
            NotImplementedError: Placeholder.
        """
        raise NotImplementedError(
            "LinuxTunDevice is not yet implemented. "
            "Use MockTunDevice for testing."
        )

    def write_packet(self, packet: bytes) -> None:
        """Write an IP packet to the TUN device.

        Args:
            packet: Raw IP packet bytes.

        Raises:
            NotImplementedError: Placeholder.
        """
        raise NotImplementedError(
            "LinuxTunDevice is not yet implemented. "
            "Use MockTunDevice for testing."
        )

    def close(self) -> None:
        """Close the TUN device.

        Raises:
            NotImplementedError: Placeholder.
        """
        raise NotImplementedError(
            "LinuxTunDevice is not yet implemented. "
            "Use MockTunDevice for testing."
        )


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
