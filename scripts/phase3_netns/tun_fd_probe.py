#!/usr/bin/env python3
"""TUN FD Probe - Minimal test to verify kernel delivers packets to TUN fd.

This script creates a TUN device and continuously reads packets from it.
Used to verify that the kernel properly delivers IP packets to the TUN file descriptor.

Usage:
    # Terminal 1 - start probe
    sudo ip netns exec vpn_cli python scripts/phase3_netns/tun_fd_probe.py --name tun_probe

    # Terminal 2 - configure IP and send ping
    sudo ip netns exec vpn_cli ip addr add 10.9.0.2/24 dev tun_probe
    sudo ip netns exec vpn_cli ip link set tun_probe up
    sudo ip netns exec vpn_cli ping -I tun_probe 10.9.0.1

    # Terminal 3 - also start a listener on 10.9.0.1
    sudo ip netns exec vpn_srv ip addr add 10.9.0.1/24 dev tun0
    sudo ip netns exec vpn_srv ip link set tun0 up
"""

import argparse
import os
import sys
import time
import fcntl
import struct

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.common.logger import get_logger
from src.common.errors import TunDeviceError

logger = get_logger(__name__)

# TUN ioctl
TUNSETIFF = 0x400454CA
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000


def create_tun(name: str) -> int:
    """Create and open a TUN device.

    Args:
        name: TUN device name.

    Returns:
        File descriptor for the TUN device.
    """
    fd = os.open("/dev/net/tun", os.O_RDWR)

    # Build ifreq structure
    name_bytes = name.encode("utf-8")[:15].ljust(16, b'\x00')
    ifreq = struct.pack("16sH", name_bytes, IFF_TUN | IFF_NO_PI)

    try:
        fcntl.ioctl(fd, TUNSETIFF, ifreq)
    except OSError as e:
        os.close(fd)
        raise RuntimeError(f"Cannot set TUN device name to '{name}': {e}")

    # Set non-blocking
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    return fd


def parse_ip_packet(packet: bytes) -> dict:
    """Parse basic IP info from packet.

    Args:
        packet: Raw IP packet bytes.

    Returns:
        Dict with src, dst, protocol, version.
    """
    info = {"length": len(packet), "version": "?", "src": "?", "dst": "?", "protocol": "?"}
    try:
        if len(packet) >= 1:
            version = packet[0] >> 4
            info["version"] = version
            if version == 4 and len(packet) >= 20:
                info["src"] = ".".join(str(b) for b in packet[12:16])
                info["dst"] = ".".join(str(b) for b in packet[16:20])
                info["protocol"] = packet[9]
                proto_names = {1: "ICMP", 6: "TCP", 17: "UDP", 47: "GRE"}
                info["protocol_name"] = proto_names.get(info["protocol"], str(info["protocol"]))
            elif version == 6 and len(packet) >= 40:
                info["src"] = ":".join(f"{packet[i]:02x}{packet[i+1]:02x}" for i in range(24, 40, 2))
                info["dst"] = ":".join(f"{packet[i]:02x}{packet[i+1]:02x}" for i in range(40, 56, 2))
                info["protocol"] = packet[6]
    except Exception as e:
        info["error"] = str(e)
    return info


def main():
    parser = argparse.ArgumentParser(description="TUN FD Probe - Verify kernel delivers packets to TUN fd")
    parser.add_argument(
        "--name",
        default="tun_probe",
        help="TUN device name (default: tun_probe)"
    )
    parser.add_argument(
        "--mtu",
        type=int,
        default=1500,
        help="MTU size (default: 1500)"
    )
    args = parser.parse_args()

    print(f"=== TUN FD Probe ===")
    print(f"Creating TUN device: {args.name}")
    print(f"Press Ctrl+C to stop")
    print()

    try:
        fd = create_tun(args.name)
        print(f"SUCCESS: Opened {args.name} (FD={fd})")
        print(f"Non-blocking mode enabled")
        print()
        print("Waiting for packets...")
        print("-" * 70)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    try:
        packet_count = 0
        while True:
            try:
                packet = os.read(fd, args.mtu)
                if packet:
                    packet_count += 1
                    info = parse_ip_packet(packet)
                    print(
                        f"[{packet_count:04d}] READ: "
                        f"len={info['length']} "
                        f"v={info['version']} "
                        f"src={info['src']} -> dst={info['dst']} "
                        f"proto={info.get('protocol_name', info['protocol'])}"
                    )
            except OSError as e:
                if e.errno in (11, 35):  # EAGAIN, EWOULDBLOCK
                    time.sleep(0.1)
                    continue
                raise RuntimeError(f"Read error: {e}")

    except KeyboardInterrupt:
        print()
        print("-" * 70)
        print(f"Stopped. Total packets read: {packet_count}")
    finally:
        os.close(fd)
        print(f"Closed {args.name} (FD={fd})")


if __name__ == "__main__":
    main()