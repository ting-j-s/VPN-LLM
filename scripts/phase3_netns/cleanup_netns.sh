#!/bin/bash
# cleanup_netns.sh - Remove network namespaces and veth pairs used for Phase 3 testing
# Usage: sudo ./cleanup_netns.sh
# This script is idempotent - safe to run even if namespaces don't exist

set -euo pipefail

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    echo "Error: This script must be run as root (sudo)"
    exit 1
fi

echo "=== Cleaning up network namespaces ==="

# Delete namespaces (may fail if processes still running inside - that's OK for cleanup)
ip netns delete vpn_srv 2>/dev/null && echo "Deleted vpn_srv namespace" || echo "vpn_srv namespace not found (OK)"
ip netns delete vpn_cli 2>/dev/null && echo "Deleted vpn_cli namespace" || echo "vpn_cli namespace not found (OK)"

echo ""
echo "=== Cleanup complete ==="
echo "Note: If namespaces still exist with running processes, stop server/client first with Ctrl+C"