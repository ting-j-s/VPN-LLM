#!/bin/bash
# show_state.sh - Show current network namespace and TUN device state
# Usage: sudo ./show_state.sh

set -euo pipefail

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    echo "Error: This script must be run as root (sudo)"
    exit 1
fi

echo "=== Network Namespaces ==="
ip netns list 2>/dev/null || echo "No namespaces found"

echo ""
echo "=== TUN Devices (global) ==="
ip link show type tun 2>/dev/null || echo "No TUN devices found"

echo ""
echo "=== veth Pairs ==="
ip link show type veth 2>/dev/null || echo "No veth pairs found"

echo ""
echo "=== Namespace vpn_srv state ==="
if ip netns list | grep -q "vpn_srv"; then
    echo "--- Interfaces in vpn_srv ---"
    ip netns exec vpn_srv ip link show 2>/dev/null || echo "Cannot query vpn_srv"
    echo "--- Routes in vpn_srv ---"
    ip netns exec vpn_srv ip route 2>/dev/null || echo "Cannot query vpn_srv routes"
    echo "--- Addresses in vpn_srv ---"
    ip netns exec vpn_srv ip addr show 2>/dev/null || echo "Cannot query vpn_srv addresses"
else
    echo "vpn_srv namespace not found"
fi

echo ""
echo "=== Namespace vpn_cli state ==="
if ip netns list | grep -q "vpn_cli"; then
    echo "--- Interfaces in vpn_cli ---"
    ip netns exec vpn_cli ip link show 2>/dev/null || echo "Cannot query vpn_cli"
    echo "--- Routes in vpn_cli ---"
    ip netns exec vpn_cli ip route 2>/dev/null || echo "Cannot query vpn_cli routes"
    echo "--- Addresses in vpn_cli ---"
    ip netns exec vpn_cli ip addr show 2>/dev/null || echo "Cannot query vpn_cli addresses"
else
    echo "vpn_cli namespace not found"
fi

echo ""
echo "=== Quick connectivity check ==="
if ip netns list | grep -q "vpn_cli" && ip netns list | grep -q "vpn_srv"; then
    echo "Testing vpn_cli -> vpn_srv (192.168.100.1)..."
    ip netns exec vpn_cli ping -c 2 -W 2 192.168.100.1 2>/dev/null && echo "Connectivity OK" || echo "No connectivity"
else
    echo "Namespaces not ready for connectivity test"
fi