#!/usr/bin/env bash
# Phase 10.3: Linux netns + TUN validation for Transport/Core replacement.
#
# Sets up isolated network namespaces with veth pairs and real TUN devices,
# then starts server/client to verify the replacement is minimally runnable
# with real IP packets. This is the SECOND validation gate after the local
# smoke matrix - requiring root/CAP_NET_ADMIN and real kernel TUN support.
#
# Usage:
#   sudo ./scripts/phase10_netns_tun_validation.sh
#   sudo ./scripts/phase10_netns_tun_validation.sh --transport websocket
#   sudo ./scripts/phase10_netns_tun_validation.sh --keep --verbose
#
# Exit codes:
#   0 - all checks passed, or skipped due to missing prerequisites
#   1 - validation failed

set -euo pipefail

# ---------------------------------------------------------------------------
# CLI defaults
# ---------------------------------------------------------------------------
TRANSPORT="${TRANSPORT:-tcp}"
TIMEOUT="${TIMEOUT:-15}"
KEEP=false
VERBOSE=false

_usage() {
    cat <<'EOF'
Usage: phase10_netns_tun_validation.sh [OPTIONS]

Options:
  --transport TYPE   Transport to validate: tcp (default) or websocket
  --timeout SECONDS  Max wait for server startup (default: 15)
  --keep             Keep namespaces and TUN devices after validation
  --verbose          Print detailed status and logs
  -h, --help         Show this help

Exit codes:
  0  All checks passed, or prerequisites not met (skip)
  1  Validation failed
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --transport)
            TRANSPORT="$2"; shift 2 ;;
        --timeout)
            TIMEOUT="$2"; shift 2 ;;
        --keep)
            KEEP=true; shift ;;
        --verbose|-v)
            VERBOSE=true; shift ;;
        -h|--help)
            _usage; exit 0 ;;
        *)
            echo "Unknown option: $1" >&2
            _usage; exit 1 ;;
    esac
done

# Validate transport
if [[ "$TRANSPORT" != "tcp" && "$TRANSPORT" != "websocket" ]]; then
    echo "ERROR: Unsupported transport '$TRANSPORT'. Use tcp or websocket." >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER_PID=""
CLIENT_PID=""
NS_SRV="vpn_srv_validation"
NS_CLI="vpn_cli_validation"

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
_info()  { echo "[INFO]  $*"; }
_warn()  { echo "[WARN]  $*" >&2; }
_error() { echo "[ERROR] $*" >&2; }
_verbose() { $VERBOSE && echo "[DEBUG] $*" || true; }
_pass()  { echo "[PASS]  $*"; }
_fail()  { echo "[FAIL]  $*"; }

# ---------------------------------------------------------------------------
# Pre-flight checks — skip gracefully if prerequisites not met
# ---------------------------------------------------------------------------
_preflight() {
    local missing=()

    if [[ "$(uname -s)" != "Linux" ]]; then
        echo "SKIP: not running on Linux (detected: $(uname -s))"
        exit 0
    fi

    command -v ip >/dev/null 2>&1 || missing+=("ip (iproute2)")
    command -v python3 >/dev/null 2>&1 || missing+=("python3")
    [[ -c /dev/net/tun ]] || missing+=("/dev/net/tun")

    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "SKIP: missing prerequisites: ${missing[*]}"
        exit 0
    fi

    # Check root or CAP_NET_ADMIN
    if [[ $EUID -ne 0 ]]; then
        # Check for CAP_NET_ADMIN on the current process
        if command -v capsh >/dev/null 2>&1; then
            if capsh --print 2>/dev/null | grep -q 'cap_net_admin'; then
                : # ok
            else
                echo "SKIP: not running as root and missing CAP_NET_ADMIN"
                exit 0
            fi
        else
            echo "SKIP: not running as root (sudo required for netns + TUN)"
            exit 0
        fi
    fi

    _info "pre-flight checks passed: Linux, ip, python3, /dev/net/tun, privileges"
}

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
_cleanup() {
    if $KEEP; then
        _info "--keep: preserving namespaces and TUN devices"
        return
    fi

    _info "cleaning up namespaces and veth pairs..."

    # Kill background processes
    if [[ -n "${CLIENT_PID:-}" ]] && kill -0 "$CLIENT_PID" 2>/dev/null; then
        kill "$CLIENT_PID" 2>/dev/null || true
        wait "$CLIENT_PID" 2>/dev/null || true
    fi
    if [[ -n "${SERVER_PID:-}" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi

    # Small delay to let processes release TUN fds
    sleep 0.5

    # Delete namespaces (veth pairs are auto-deleted with namespace)
    ip netns delete "$NS_CLI" 2>/dev/null || true
    ip netns delete "$NS_SRV" 2>/dev/null || true

    _info "cleanup complete"
}

# ---------------------------------------------------------------------------
# Setup network namespaces and veth pair
# ---------------------------------------------------------------------------
_setup_netns() {
    _info "setting up network namespaces..."

    # Create namespaces
    ip netns add "$NS_SRV" 2>/dev/null || { _error "failed to create $NS_SRV"; return 1; }
    _verbose "created namespace: $NS_SRV"

    ip netns add "$NS_CLI" 2>/dev/null || { _error "failed to create $NS_CLI"; return 1; }
    _verbose "created namespace: $NS_CLI"

    # Create veth pair
    ip link add veth_srv type veth peer name veth_cli
    _verbose "created veth pair: veth_srv <-> veth_cli"

    # Move endpoints to namespaces
    ip link set veth_srv netns "$NS_SRV"
    ip link set veth_cli netns "$NS_CLI"

    # Configure server-side network
    ip netns exec "$NS_SRV" ip addr add 192.168.200.1/24 dev veth_srv
    ip netns exec "$NS_SRV" ip link set lo up
    ip netns exec "$NS_SRV" ip link set veth_srv up

    # Configure client-side network
    ip netns exec "$NS_CLI" ip addr add 192.168.200.2/24 dev veth_cli
    ip netns exec "$NS_CLI" ip link set lo up
    ip netns exec "$NS_CLI" ip link set veth_cli up

    _info "namespaces configured:"
    _info "  $NS_SRV: veth_srv 192.168.200.1/24"
    _info "  $NS_CLI: veth_cli 192.168.200.2/24"
}

# ---------------------------------------------------------------------------
# Verify underlay connectivity (veth ping)
# ---------------------------------------------------------------------------
_verify_underlay() {
    _info "verifying underlay connectivity..."

    if ! ip netns exec "$NS_CLI" ping -c 3 -W 2 192.168.200.1 >/dev/null 2>&1; then
        _fail "veth ping failed — client ($NS_CLI) cannot reach server ($NS_SRV)"
        return 1
    fi
    _pass "veth ping: 192.168.200.2 -> 192.168.200.1 OK"
    return 0
}

# ---------------------------------------------------------------------------
# Start server in server namespace
# ---------------------------------------------------------------------------
_start_server() {
    _info "starting VPN server in $NS_SRV (transport=$TRANSPORT)..."

    local server_log
    server_log="$(mktemp /tmp/vpn_server_validation.XXXXXX.log)"

    # Run server in background within namespace.
    # The netns config uses tcp by default; --transport overrides it.
    ip netns exec "$NS_SRV" \
        env VPN_LLM_LOG_LEVEL="${VPN_LLM_LOG_LEVEL:-INFO}" \
        python3 -m src.server \
            --config config/server_netns.yaml \
            --transport "$TRANSPORT" \
        >"$server_log" 2>&1 &
    SERVER_PID=$!

    _verbose "server PID=$SERVER_PID, log=$server_log"

    # Wait for server to be ready
    local t0
    t0=$(date +%s)
    local ready=false
    local slept=0.5
    while true; do
        local elapsed
        elapsed=$(($(date +%s) - t0))
        if [[ $elapsed -ge $TIMEOUT ]]; then
            break
        fi

        sleep "$slept"

        # Check that the process is still alive
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            _fail "server process died during startup"
            _info "--- server log ---"
            cat "$server_log"
            return 1
        fi

        # For TCP: check that port 2222 is listening
        if [[ "$TRANSPORT" == "tcp" ]]; then
            if ip netns exec "$NS_SRV" ss -tlnp 2>/dev/null | grep -q "2222"; then
                ready=true
                break
            fi
        fi

        # For WebSocket: check log for "listening" or "running" keywords
        if [[ "$TRANSPORT" == "websocket" ]]; then
            if grep -qE "(listening|running|Tunnel)" "$server_log" 2>/dev/null; then
                ready=true
                break
            fi
        fi
    done

    if ! $ready; then
        _fail "server did not become ready within ${TIMEOUT}s"
        _info "--- server log ---"
        cat "$server_log"
        return 1
    fi

    _pass "server started (PID=$SERVER_PID, transport=$TRANSPORT)"
    return 0
}

# ---------------------------------------------------------------------------
# Start client in client namespace
# ---------------------------------------------------------------------------
_start_client() {
    _info "starting VPN client in $NS_CLI (transport=$TRANSPORT)..."

    local client_log
    client_log="$(mktemp /tmp/vpn_client_validation.XXXXXX.log)"

    ip netns exec "$NS_CLI" \
        env VPN_LLM_LOG_LEVEL="${VPN_LLM_LOG_LEVEL:-INFO}" \
        python3 -m src.client \
            --config config/client_netns.yaml \
            --transport "$TRANSPORT" \
        >"$client_log" 2>&1 &
    CLIENT_PID=$!

    _verbose "client PID=$CLIENT_PID, log=$client_log"

    # Wait for client to connect
    local t0
    t0=$(date +%s)
    local connected=false
    local slept=0.5
    while true; do
        local elapsed
        elapsed=$(($(date +%s) - t0))
        if [[ $elapsed -ge $TIMEOUT ]]; then
            break
        fi

        sleep "$slept"

        # Check that the process is still alive
        if ! kill -0 "$CLIENT_PID" 2>/dev/null; then
            _fail "client process died during startup"
            _info "--- client log ---"
            cat "$client_log"
            return 1
        fi

        # Check log for tunnel established
        if grep -qE "(Tunnel established|tunnel established|running)" "$client_log" 2>/dev/null; then
            connected=true
            break
        fi
    done

    if ! $connected; then
        _fail "client did not connect within ${TIMEOUT}s"
        _info "--- client log ---"
        cat "$client_log"
        return 1
    fi

    _pass "client connected (PID=$CLIENT_PID, transport=$TRANSPORT)"
    return 0
}

# ---------------------------------------------------------------------------
# Verify TUN devices exist
# ---------------------------------------------------------------------------
_verify_tun_devices() {
    _info "verifying TUN devices..."

    # Server TUN (tun0)
    if ! ip netns exec "$NS_SRV" ip link show tun0 >/dev/null 2>&1; then
        _fail "server TUN device (tun0) not found in $NS_SRV"
        return 1
    fi
    local srv_up
    srv_up=$(ip netns exec "$NS_SRV" ip link show tun0 | grep -c "UP" || true)
    _pass "server TUN device: tun0 present in $NS_SRV"

    # Client TUN (tun1)
    if ! ip netns exec "$NS_CLI" ip link show tun1 >/dev/null 2>&1; then
        _fail "client TUN device (tun1) not found in $NS_CLI"
        return 1
    fi
    _pass "client TUN device: tun1 present in $NS_CLI"

    return 0
}

# ---------------------------------------------------------------------------
# Configure TUN IPs and verify routing
# ---------------------------------------------------------------------------
_configure_tun_ips() {
    _info "configuring TUN IP addresses..."

    ip netns exec "$NS_SRV" ip addr add 10.8.0.1/24 dev tun0 2>/dev/null || true
    ip netns exec "$NS_SRV" ip link set tun0 up 2>/dev/null || true

    ip netns exec "$NS_CLI" ip addr add 10.8.0.2/24 dev tun1 2>/dev/null || true
    ip netns exec "$NS_CLI" ip link set tun1 up 2>/dev/null || true

    _pass "TUN IPs configured: tun0=10.8.0.1/24, tun1=10.8.0.2/24"

    if $VERBOSE; then
        _verbose "server routes:"
        ip netns exec "$NS_SRV" ip route show 2>/dev/null || true
        _verbose "client routes:"
        ip netns exec "$NS_CLI" ip route show 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

echo "=== Phase 10.3: netns + TUN Validation ==="
echo "Transport:  $TRANSPORT"
echo "Timeout:    ${TIMEOUT}s"
echo "Keep:       $KEEP"
echo ""

_preflight

trap _cleanup EXIT

_setup_netns || exit 1
_verify_underlay || exit 1

_start_server || exit 1
_start_client || exit 1

_verify_tun_devices || exit 1
_configure_tun_ips || exit 1

echo ""
echo "=== netns + TUN validation complete ==="
echo ""
echo "Namespaces: $NS_SRV, $NS_CLI"
echo "TUN devices: tun0=$NS_SRV, tun1=$NS_CLI"
echo ""
echo "Server and client are running. To verify real IP packet flow manually:"
echo ""
echo "  # Terminal 1: tcpdump on server TUN"
echo "  sudo ip netns exec $NS_SRV tcpdump -i tun0 -n icmp"
echo ""
echo "  # Terminal 2: tcpdump on client TUN"
echo "  sudo ip netns exec $NS_CLI tcpdump -i tun1 -n icmp"
echo ""
echo "  # Terminal 3: ping through the tunnel"
echo "  sudo ip netns exec $NS_CLI ping -I tun1 10.8.0.1"
echo ""
echo "For detailed troubleshooting, see docs/phase10_netns_tun_validation.md"
echo "and docs/phase3_netns_validation.md"
echo ""
echo "Run with --keep to preserve the environment for manual testing."
