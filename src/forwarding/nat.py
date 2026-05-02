"""NAT (Network Address Translation) Module.

Provides utilities for enabling/disabling NAT on the VPN tunnel.
All functions return commands to be executed manually by the user.

Warning:
    NAT and routing changes require root privileges.
    Always review commands before executing.
"""

import subprocess
from typing import Optional

from ..common.logger import get_logger


logger = get_logger(__name__)


def build_enable_nat_command(
    tun_name: str,
    outbound_if: str = "eth0",
) -> str:
    """Build command to enable NAT for the TUN interface.

    Enables IP forwarding and masquerading for packets from the tunnel.

    Args:
        tun_name: TUN device name (e.g., "tun0").
        outbound_if: Outbound network interface (e.g., "eth0", "wlan0").

    Returns:
        Shell command string to enable NAT.
    """
    # Enable IP forwarding and configure iptables NAT
    # Using -C to check and -A to add, ignoring errors if already exists
    cmd = (
        f"echo 1 > /proc/sys/net/ipv4/ip_forward && "
        f"iptables -t nat -C POSTROUTING -s {tun_name} -o {outbound_if} -j MASQUERADE 2>/dev/null || "
        f"iptables -t nat -A POSTROUTING -s {tun_name} -o {outbound_if} -j MASQUERADE"
    )
    logger.info(f"NAT enable command: {cmd}")
    return cmd


def build_disable_nat_command(
    tun_name: str,
    outbound_if: str = "eth0",
) -> str:
    """Build command to disable NAT for the TUN interface.

    Removes the masquerading rule from iptables.

    Args:
        tun_name: TUN device name (e.g., "tun0").
        outbound_if: Outbound network interface.

    Returns:
        Shell command string to disable NAT.
    """
    cmd = f"iptables -t nat -D POSTROUTING -s {tun_name} -o {outbound_if} -j MASQUERADE"
    logger.info(f"NAT disable command: {cmd}")
    return cmd


def build_iptables_forward_rule(
    tun_name: str,
    action: str = "ACCEPT",
) -> str:
    """Build command to add iptables FORWARD rule.

    Args:
        tun_name: TUN device name.
        action: FORWARD action (ACCEPT, DROP, etc.).

    Returns:
        Shell command string for iptables rule.
    """
    cmd = f"iptables -A FORWARD -i {tun_name} -j {action}"
    logger.info(f"iptables FORWARD rule: {cmd}")
    return cmd


def build_flush_forward_rules(tun_name: str) -> str:
    """Build command to flush all FORWARD rules for the TUN device.

    Args:
        tun_name: TUN device name.

    Returns:
        Shell command string to flush rules.
    """
    cmd = f"iptables -F FORWARD -i {tun_name}"
    logger.info(f"Flush FORWARD rules: {cmd}")
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


def enable_nat(
    tun_name: str,
    outbound_if: str = "eth0",
    execute: bool = False,
) -> Optional[str]:
    """Enable NAT for the TUN interface.

    Args:
        tun_name: TUN device name.
        outbound_if: Outbound network interface.
        execute: If False (default), only returns command. If True, executes it.

    Returns:
        Command string if execute=False, None if execute=True.

    Warning:
        Only set execute=True if you understand the system implications.
        NAT changes affect the entire system.
    """
    cmd = build_enable_nat_command(tun_name, outbound_if)

    if not execute:
        logger.warning(
            f"NAT NOT enabled. To enable manually, run:\n  {cmd}\n"
            "Review the command carefully before executing with sudo.\n"
            "This will enable IP forwarding and NAT for all tunnel traffic."
        )
        return cmd

    logger.warning(f"Executing NAT enable command: {cmd}")
    returncode, stdout, stderr = execute_command(cmd)
    if returncode == 0:
        logger.info(f"NAT enabled for {tun_name} -> {outbound_if}")
    else:
        logger.error(f"Failed to enable NAT: {stderr}")
    return None


def disable_nat(
    tun_name: str,
    outbound_if: str = "eth0",
    execute: bool = False,
) -> Optional[str]:
    """Disable NAT for the TUN interface.

    Args:
        tun_name: TUN device name.
        outbound_if: Outbound network interface.
        execute: If False (default), only returns command. If True, executes it.

    Returns:
        Command string if execute=False, None if execute=True.
    """
    cmd = build_disable_nat_command(tun_name, outbound_if)

    if not execute:
        logger.warning(
            f"NAT NOT disabled. To disable manually, run:\n  {cmd}\n"
            "Review the command carefully before executing with sudo."
        )
        return cmd

    logger.warning(f"Executing NAT disable command: {cmd}")
    returncode, stdout, stderr = execute_command(cmd)
    if returncode == 0:
        logger.info(f"NAT disabled for {tun_name}")
    else:
        logger.error(f"Failed to disable NAT: {stderr}")
    return None


def show_nat_status(tun_name: str = "tun0") -> None:
    """Show current NAT rules for the TUN device.

    Args:
        tun_name: TUN device name to filter rules.
    """
    cmd = f"iptables -t nat -L POSTROUTING -v -n | grep {tun_name}"
    logger.info(f"Checking NAT rules for {tun_name}")
    returncode, stdout, stderr = execute_command(cmd)
    if stdout:
        logger.info(f"NAT rules:\n{stdout}")
    else:
        logger.info(f"No NAT rules found for {tun_name}")
