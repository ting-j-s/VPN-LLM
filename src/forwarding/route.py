"""Routing Module.

Provides routing table and forwarding logic for tunnel traffic.
"""

import logging
import socket
import struct
from typing import Optional

from ..common.logger import setup_logger


logger = setup_logger(__name__)


class RoutingTable:
    """Simple routing table for tunnel traffic.

    Provides basic destination-based forwarding decisions.
    """

    def __init__(self, tunnel_subnet: str = "10.0.0.0/24"):
        """Initialize routing table.

        Args:
            tunnel_subnet: Subnet managed by the tunnel (CIDR notation).
        """
        self.tunnel_subnet = tunnel_subnet
        self._routes: dict[str, str] = {}  # destination -> next_hop

        # Default route through tunnel
        self._routes["0.0.0.0/0"] = "tunnel"

        logger.info(f"Routing table initialized for {tunnel_subnet}")

    def add_route(self, destination: str, next_hop: str) -> None:
        """Add a route to the routing table.

        Args:
            destination: Destination network (CIDR notation).
            next_hop: Next hop address ('tunnel' for local, or IP).
        """
        self._routes[destination] = next_hop
        logger.info(f"Added route: {destination} -> {next_hop}")

    def remove_route(self, destination: str) -> None:
        """Remove a route from the table.

        Args:
            destination: Destination network to remove.
        """
        if destination in self._routes:
            del self._routes[destination]
            logger.info(f"Removed route: {destination}")

    def lookup(self, destination_ip: str) -> Optional[str]:
        """Look up route for a destination IP.

        Args:
            destination_ip: Destination IP address.

        Returns:
            Next hop address, or 'tunnel' if should be forwarded
            through tunnel, or None if no route found.
        """
        # Simple longest prefix match
        dest_bytes = socket.inet_aton(destination_ip)

        best_match = None
        best_prefix_len = -1

        for cidr, next_hop in self._routes.items():
            if cidr == "0.0.0.0/0":
                # Default route
                if best_prefix_len < 0:
                    best_match = next_hop
                    best_prefix_len = 0
                continue

            # Parse CIDR
            network, prefix_len = cidr.split("/")
            prefix_len = int(prefix_len)
            network_bytes = socket.inet_aton(network)

            # Check if destination matches
            dest_int = struct.unpack(">I", dest_bytes)[0]
            network_int = struct.unpack(">I", network_bytes)[0]
            mask = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF

            if (dest_int & mask) == (network_int & mask):
                if prefix_len > best_prefix_len:
                    best_match = next_hop
                    best_prefix_len = prefix_len

        logger.debug(f"Route lookup for {destination_ip}: {best_match}")
        return best_match

    def should_forward_to_tunnel(self, destination_ip: str) -> bool:
        """Check if packet should be forwarded through the tunnel.

        Args:
            destination_ip: Destination IP address.

        Returns:
            True if packet should go through tunnel.
        """
        next_hop = self.lookup(destination_ip)
        return next_hop == "tunnel"

    def __repr__(self) -> str:
        return f"RoutingTable(subnet={self.tunnel_subnet}, routes={len(self._routes)})"
