"""NAT (Network Address Translation) Module.

Provides NAT functionality for forwarding tunnel traffic to external networks.
Implements basic source NAT for outbound traffic.
"""

import logging
import socket
import struct
from typing import Optional

from ..common.errors import ForwardingError
from ..common.logger import setup_logger


logger = setup_logger(__name__)


class NATForwarder:
    """NAT forwarder for tunnel traffic.

    Performs basic NAT operations:
    - Translates source IP addresses for outbound traffic
    - Maintains translation table for return traffic
    - Forwards traffic to external gateway

    Note: This is a simplified implementation for tunnel scenarios.
    Full NAT implementation would require connection tracking and
    proper port management.
    """

    def __init__(
        self,
        tunnel_ip: str = "10.0.0.2",
        external_ip: str = "10.0.0.1",
        gateway: str = "192.168.1.1",
        mtu: int = 1400,
    ):
        """Initialize NAT forwarder.

        Args:
            tunnel_ip: IP address assigned to tunnel interface.
            external_ip: External IP address visible to remote network.
            gateway: Upstream gateway for forwarded traffic.
            mtu: Maximum transmission unit.
        """
        self.tunnel_ip = tunnel_ip
        self.external_ip = external_ip
        self.gateway = gateway
        self.mtu = mtu

        self._enabled = False
        logger.info(
            f"NAT forwarder initialized: tunnel={tunnel_ip}, "
            f"external={external_ip}, gateway={gateway}"
        )

    def enable(self) -> None:
        """Enable NAT forwarding."""
        self._enabled = True
        logger.info("NAT forwarding enabled")

    def disable(self) -> None:
        """Disable NAT forwarding."""
        self._enabled = False
        logger.info("NAT forwarding disabled")

    def forward_packet(self, packet: bytes) -> Optional[bytes]:
        """Forward a packet with NAT translation.

        Args:
            packet: Raw IP packet bytes.

        Returns:
            NAT-translated packet bytes, or None if dropped.
        """
        if not self._enabled:
            return packet

        if len(packet) < 20:
            logger.warning("Packet too short for IP header")
            return None

        # Parse IP header
        version = (packet[0] >> 4) & 0xF
        if version != 4:
            logger.warning(f"Only IPv4 supported, got version {version}")
            return packet

        ihl = (packet[0] & 0xF) * 4
        total_length = struct.unpack(">H", packet[2:4])[0]
        protocol = packet[9]
        src_ip = packet[12:16]
        dst_ip = packet[16:20]

        # For now, just log and pass through
        # A full implementation would:
        # 1. Translate source IP from tunnel_ip to external_ip
        # 2. Create mapping entry in connection table
        # 3. Update IP checksum
        # 4. Forward to gateway

        logger.debug(
            f"NAT: forwarding packet {socket.inet_ntoa(src_ip)} -> "
            f"{socket.inet_ntoa(dst_ip)} (proto={protocol})"
        )

        return packet

    def translate_packet(self, packet: bytes) -> bytes:
        """Translate source IP in outbound packet.

        Args:
            packet: Raw IP packet.

        Returns:
            Packet with translated source IP.
        """
        if len(packet) < 20:
            raise ForwardingError("Packet too short for IP header")

        version = (packet[0] >> 4) & 0xF
        if version != 4:
            return packet

        # Parse header
        ihl = (packet[0] & 0xF) * 4
        header = bytearray(packet[:ihl])
        payload = packet[ihl:]

        # Convert IPs to integers
        tunnel_ip_bytes = socket.inet_aton(self.tunnel_ip)
        tunnel_ip_int = struct.unpack(">I", tunnel_ip_bytes)[0]
        external_ip_bytes = socket.inet_aton(self.external_ip)
        external_ip_int = struct.unpack(">I", external_ip_bytes)[0]

        src_ip_int = struct.unpack(">I", bytes(header[12:16]))[0]

        # Only translate if source matches tunnel_ip
        if src_ip_int == tunnel_ip_int:
            # Replace source IP
            header[12:16] = external_ip_bytes

            # Recalculate IP checksum
            # IP checksum is at bytes 10-11 of header
            header[10:12] = b"\x00\x00"
            checksum = self._ip_checksum(bytes(header))
            header[10:12] = struct.pack(">H", checksum)

            logger.debug(f"NAT: translated {self.tunnel_ip} -> {self.external_ip}")

        return bytes(header) + payload

    def _ip_checksum(self, header: bytes) -> int:
        """Calculate IP header checksum.

        Args:
            header: IP header bytes.

        Returns:
            Checksum value.
        """
        if len(header) % 2 != 0:
            header += b"\x00"

        checksum = 0
        for i in range(0, len(header), 2):
            word = (header[i] << 8) + header[i + 1]
            checksum += word

        while checksum >> 16:
            checksum = (checksum & 0xFFFF) + (checksum >> 16)

        return ~checksum & 0xFFFF

    def __repr__(self) -> str:
        return (
            f"NATForwarder(tunnel={self.tunnel_ip}, "
            f"external={self.external_ip}, gateway={self.gateway})"
        )
