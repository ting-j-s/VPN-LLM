#!/bin/bash
# setup_netns.sh - Create network namespaces and veth pairs for Phase 3 testing
# Usage: sudo ./setup_netns.sh

set -euo pipefail

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    echo "Error: This script must be run as root (sudo)"
    exit 1
fi

echo "=== Creating network namespaces ==="
ip netns add vpn_srv 2>/dev/null || echo "vpn_srv namespace already exists"
ip netns add vpn_cli 2>/dev/null || echo "vpn_cli namespace already exists"

echo "=== Creating veth pair ==="
ip link add veth_srv type veth peer name veth_cli 2>/dev/null || echo "veth_srv/veth_cli already exists"

echo "=== Moving veth to namespaces ==="
ip link set veth_srv netns vpn_srv
ip link set veth_cli netns vpn_cli

echo "=== Configuring server namespace (vpn_srv) ==="
ip netns exec vpn_srv ip addr add 192.168.100.1/24 dev veth_srv
ip netns exec vpn_srv ip link set lo up
ip netns exec vpn_srv ip link set veth_srv up

echo "=== Configuring client namespace (vpn_cli) ==="
ip netns exec vpn_cli ip addr add 192.168.100.2/24 dev veth_cli
ip netns exec vpn_cli ip link set lo up
ip netns exec vpn_cli ip link set veth_cli up

echo ""
echo "=== Network namespace setup complete ==="
echo ""
echo "Verify connectivity:"
echo "  sudo ip netns exec vpn_cli ping -c 3 192.168.100.1"
echo ""
echo "To start server:"
echo "  sudo ip netns exec vpn_srv python -m src.server --config config/server_netns.yaml --transport tcp"
echo ""
echo "To start client:"
echo "  sudo ip netns exec vpn_cli python -m src.client --config config/client_netns.yaml --transport tcp"
echo ""
echo "Then configure TUN IPs and verify with ping/tcpdump (see docs/phase3_netns_validation.md)"