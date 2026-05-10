"""Routing Module.

Provides utilities for managing routing table entries for the VPN tunnel.
All functions return commands to be executed manually by the user.
"""

import subprocess
from typing import Optional

from ..common.logger import get_logger


logger = get_logger(__name__)


def build_add_route_command(
    destination_cidr: str,
    tun_name: str,
    gateway: Optional[str] = None,
) -> str:
    """Build command to add a route through the TUN device.

    Args:
        destination_cidr: Destination network in CIDR notation (e.g., "10.0.0.0/24").
        tun_name: TUN device name (e.g., "tun0").
        gateway: Optional next hop gateway. If None, uses the TUN interface directly.

    Returns:
        Shell command string to add the route.
    """
    if gateway:
        cmd = f"ip route add {destination_cidr} via {gateway} dev {tun_name}"
    else:
        cmd = f"ip route add {destination_cidr} dev {tun_name}"

    logger.info(f"Route add command: {cmd}")
    return cmd


def build_delete_route_command(
    destination_cidr: str,
    tun_name: str,
    gateway: Optional[str] = None,
) -> str:
    """Build command to delete a route through the TUN device.

    Args:
        destination_cidr: Destination network in CIDR notation (e.g., "10.0.0.0/24").
        tun_name: TUN device name (e.g., "tun0").
        gateway: Optional next hop gateway.

    Returns:
        Shell command string to delete the route.
    """
    if gateway:
        cmd = f"ip route delete {destination_cidr} via {gateway} dev {tun_name}"
    else:
        cmd = f"ip route delete {destination_cidr} dev {tun_name}"

    logger.info(f"Route delete command: {cmd}")
    return cmd


def build_default_route_command(
    tun_name: str,
    gateway: str,
) -> str:
    """Build command to set default route through the TUN device.

    Args:
        tun_name: TUN device name.
        gateway: Gateway IP address.

    Returns:
        Shell command string to set default route.
    """
    cmd = f"ip route add default via {gateway} dev {tun_name}"
    logger.info(f"Default route command: {cmd}")
    return cmd


def build_show_route_command(destination: Optional[str] = None) -> str:
    """Build command to show routing table or specific route.

    Args:
        destination: Optional destination to look up.

    Returns:
        Shell command string to show routes.
    """
    if destination:
        cmd = f"ip route show {destination}"
    else:
        cmd = "ip route show"
    return cmd


def execute_command(cmd: str) -> tuple[int, str, str]:
    """Execute a shell command.

    Args:
        cmd: Command string to execute.

    Returns:
        Tuple of (return_code, stdout, stderr).
    """
    logger.info(f"Executing: {cmd}")
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def add_route(
    destination_cidr: str,
    tun_name: str,
    gateway: Optional[str] = None,
    execute: bool = False,
) -> Optional[str]:
    """Add route through TUN device.

    Args:
        destination_cidr: Destination network in CIDR notation.
        tun_name: TUN device name.
        gateway: Optional next hop gateway.
        execute: If False (default), only returns command. If True, executes it.

    Returns:
        Command string if execute=False, None if execute=True.

    Warning:
        Only set execute=True if you understand the system implications.
        Always verify routes before applying.
    """
    cmd = build_add_route_command(destination_cidr, tun_name, gateway)

    if not execute:
        logger.warning(
            f"Route NOT applied. To apply manually, run:\n  {cmd}\n"
            "Review the command carefully before executing with sudo."
        )
        return cmd

    logger.warning(f"Executing route add command: {cmd}")
    returncode, stdout, stderr = execute_command(cmd)
    if returncode == 0:
        logger.info(f"Route added successfully: {destination_cidr} via {gateway or 'direct'}")
    else:
        logger.error(f"Failed to add route: {stderr}")
    return None


def delete_route(
    destination_cidr: str,
    tun_name: str,
    gateway: Optional[str] = None,
    execute: bool = False,
) -> Optional[str]:
    """Delete route through TUN device.

    Args:
        destination_cidr: Destination network in CIDR notation.
        tun_name: TUN device name.
        gateway: Optional next hop gateway.
        execute: If False (default), only returns command. If True, executes it.

    Returns:
        Command string if execute=False, None if execute=True.
    """
    cmd = build_delete_route_command(destination_cidr, tun_name, gateway)

    if not execute:
        logger.warning(
            f"Route NOT deleted. To delete manually, run:\n  {cmd}\n"
            "Review the command carefully before executing with sudo."
        )
        return cmd

    logger.warning(f"Executing route delete command: {cmd}")
    returncode, stdout, stderr = execute_command(cmd)
    if returncode == 0:
        logger.info(f"Route deleted successfully: {destination_cidr}")
    else:
        logger.error(f"Failed to delete route: {stderr}")
    return None
