#!/usr/bin/env bash
# Phase 10.3/10.4: Linux netns + TUN validation for Transport/Core replacement.
#
# Sets up isolated network namespaces with veth pairs and real TUN devices,
# then starts server/client to verify the replacement is minimally runnable
# with real IP packets. This is the SECOND validation gate after the local
# smoke matrix - requiring root/CAP_NET_ADMIN and real kernel TUN support.
#
# Default mode (Phase 10.3): environment, TUN creation, process health checks.
# --e2e-ping mode (Phase 10.4): real IP packet forwarding verification via ping.
#
# Usage:
#   sudo ./scripts/phase10_netns_tun_validation.sh
#   sudo ./scripts/phase10_netns_tun_validation.sh --transport websocket
#   sudo ./scripts/phase10_netns_tun_validation.sh --keep --verbose --e2e-ping
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
E2E_PING=false
PING_COUNT=2
PING_TIMEOUT=5
TCPDUMP=false

_usage() {
    cat <<'EOF'
Usage: phase10_netns_tun_validation.sh [OPTIONS]

Options:
  --transport TYPE    Transport to validate: tcp (default) or websocket
  --timeout SECONDS   Max wait for server startup (default: 15)
  --keep              Keep namespaces and TUN devices after validation
  --verbose           Print detailed status and logs
  --e2e-ping          Enable end-to-end TUN ping verification (Phase 10.4)
  --ping-count N      Number of ping packets (default: 2, only with --e2e-ping)
  --ping-timeout SEC  Ping timeout in seconds (default: 5, only with --e2e-ping)
  --tcpdump           Enable packet capture for diagnostics (only with --e2e-ping)
  -h, --help          Show this help

Default mode: environment + TUN + process health check (Phase 10.3)
--e2e-ping mode: also runs real IP packet ping through the TUN tunnel (Phase 10.4)

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
        --e2e-ping)
            E2E_PING=true; shift ;;
        --ping-count)
            PING_COUNT="$2"; shift 2 ;;
        --ping-timeout)
            PING_TIMEOUT="$2"; shift 2 ;;
        --tcpdump)
            TCPDUMP=true; shift ;;
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

# Validate ping-count is a positive integer
if ! [[ "$PING_COUNT" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: --ping-count must be a positive integer, got '$PING_COUNT'" >&2
    exit 1
fi

# Validate ping-timeout is a positive integer
if ! [[ "$PING_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: --ping-timeout must be a positive integer, got '$PING_TIMEOUT'" >&2
    exit 1
fi

# --tcpdump requires --e2e-ping
if $TCPDUMP && ! $E2E_PING; then
    echo "ERROR: --tcpdump requires --e2e-ping" >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER_PID=""
CLIENT_PID=""
SERVER_LOG=""
CLIENT_LOG=""
TCPDUMP_SRV_TUN_PID=""
TCPDUMP_CLI_TUN_PID=""
TCPDUMP_SRV_VETH_PID=""
PCAP_DIR=""
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

    # e2e-ping extra checks
    if $E2E_PING; then
        command -v ping >/dev/null 2>&1 || missing+=("ping")
        if $TCPDUMP; then
            command -v tcpdump >/dev/null 2>&1 || missing+=("tcpdump")
        fi
        if [[ ${#missing[@]} -gt 0 ]]; then
            echo "SKIP: e2e-ping requires: ${missing[*]}"
            exit 0
        fi
    fi

    _info "pre-flight checks passed: Linux, ip, python3, /dev/net/tun, privileges"
}

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
_cleanup() {
    # Kill tcpdump processes first
    if [[ -n "${TCPDUMP_SRV_TUN_PID:-}" ]] && kill -0 "$TCPDUMP_SRV_TUN_PID" 2>/dev/null; then
        kill "$TCPDUMP_SRV_TUN_PID" 2>/dev/null || true
        wait "$TCPDUMP_SRV_TUN_PID" 2>/dev/null || true
    fi
    if [[ -n "${TCPDUMP_CLI_TUN_PID:-}" ]] && kill -0 "$TCPDUMP_CLI_TUN_PID" 2>/dev/null; then
        kill "$TCPDUMP_CLI_TUN_PID" 2>/dev/null || true
        wait "$TCPDUMP_CLI_TUN_PID" 2>/dev/null || true
    fi
    if [[ -n "${TCPDUMP_SRV_VETH_PID:-}" ]] && kill -0 "$TCPDUMP_SRV_VETH_PID" 2>/dev/null; then
        kill "$TCPDUMP_SRV_VETH_PID" 2>/dev/null || true
        wait "$TCPDUMP_SRV_VETH_PID" 2>/dev/null || true
    fi

    if $KEEP; then
        _info "--keep: preserving namespaces, TUN devices, logs, and pcaps"
        if [[ -n "${PCAP_DIR:-}" && -d "$PCAP_DIR" ]]; then
            _info "pcap files: $PCAP_DIR/"
        fi
        if [[ -n "${SERVER_LOG:-}" && -f "$SERVER_LOG" ]]; then
            _info "server log: $SERVER_LOG"
        fi
        if [[ -n "${CLIENT_LOG:-}" && -f "$CLIENT_LOG" ]]; then
            _info "client log: $CLIENT_LOG"
        fi
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

    # Clean up temp logs
    rm -f /tmp/vpn_server_validation.*.log /tmp/vpn_client_validation.*.log

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

    SERVER_LOG="$(mktemp /tmp/vpn_server_validation.XXXXXX.log)"

    # Run server in background within namespace.
    ip netns exec "$NS_SRV" \
        env VPN_LLM_LOG_LEVEL="${VPN_LLM_LOG_LEVEL:-INFO}" \
        python3 -m src.server \
            --config config/server_netns.yaml \
            --transport "$TRANSPORT" \
        >"$SERVER_LOG" 2>&1 &
    SERVER_PID=$!

    _verbose "server PID=$SERVER_PID, log=$SERVER_LOG"

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
            cat "$SERVER_LOG"
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
            if grep -qE "(listening|running|Tunnel)" "$SERVER_LOG" 2>/dev/null; then
                ready=true
                break
            fi
        fi
    done

    if ! $ready; then
        _fail "server did not become ready within ${TIMEOUT}s"
        _info "--- server log ---"
        cat "$SERVER_LOG"
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

    CLIENT_LOG="$(mktemp /tmp/vpn_client_validation.XXXXXX.log)"

    ip netns exec "$NS_CLI" \
        env VPN_LLM_LOG_LEVEL="${VPN_LLM_LOG_LEVEL:-INFO}" \
        python3 -m src.client \
            --config config/client_netns.yaml \
            --transport "$TRANSPORT" \
        >"$CLIENT_LOG" 2>&1 &
    CLIENT_PID=$!

    _verbose "client PID=$CLIENT_PID, log=$CLIENT_LOG"

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
            cat "$CLIENT_LOG"
            return 1
        fi

        # Check log for tunnel established
        if grep -qE "(Tunnel established|tunnel established|running)" "$CLIENT_LOG" 2>/dev/null; then
            connected=true
            break
        fi
    done

    if ! $connected; then
        _fail "client did not connect within ${TIMEOUT}s"
        _info "--- client log ---"
        cat "$CLIENT_LOG"
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

    # Use point-to-point configuration for better routing semantics.
    # peer /32 avoids the kernel creating a subnet route that can cause
    # local delivery of packets instead of routing through the TUN fd.
    ip netns exec "$NS_SRV" ip addr add 10.8.0.1 peer 10.8.0.2/32 dev tun0 2>/dev/null || true
    ip netns exec "$NS_SRV" ip link set tun0 up 2>/dev/null || true

    ip netns exec "$NS_CLI" ip addr add 10.8.0.2 peer 10.8.0.1/32 dev tun1 2>/dev/null || true
    ip netns exec "$NS_CLI" ip link set tun1 up 2>/dev/null || true

    _pass "TUN IPs configured: tun0=10.8.0.1 peer 10.8.0.2, tun1=10.8.0.2 peer 10.8.0.1"

    if $E2E_PING; then
        # Add explicit routes to ensure packets go through TUN devices
        ip netns exec "$NS_SRV" ip route add 10.8.0.2 dev tun0 2>/dev/null || true
        ip netns exec "$NS_CLI" ip route add 10.8.0.1 dev tun1 2>/dev/null || true
        _verbose "explicit TUN routes added"
    fi

    if $VERBOSE; then
        _verbose "server routes:"
        ip netns exec "$NS_SRV" ip route show 2>/dev/null || true
        _verbose "client routes:"
        ip netns exec "$NS_CLI" ip route show 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# Start packet capture for diagnostics (--tcpdump)
# ---------------------------------------------------------------------------
_start_tcpdump() {
    if ! $TCPDUMP; then
        return 0
    fi

    PCAP_DIR="$(mktemp -d /tmp/vpn_validation_pcap.XXXXXX)"
    _info "starting tcpdump captures in $PCAP_DIR..."

    # Capture on server TUN (tun0) — ICMP packets
    ip netns exec "$NS_SRV" \
        tcpdump -i tun0 -n -w "$PCAP_DIR/server_tun0.pcap" \
        >/dev/null 2>&1 &
    TCPDUMP_SRV_TUN_PID=$!
    _verbose "tcpdump server tun0 PID=$TCPDUMP_SRV_TUN_PID"

    # Capture on client TUN (tun1) — ICMP packets
    ip netns exec "$NS_CLI" \
        tcpdump -i tun1 -n -w "$PCAP_DIR/client_tun1.pcap" \
        >/dev/null 2>&1 &
    TCPDUMP_CLI_TUN_PID=$!
    _verbose "tcpdump client tun1 PID=$TCPDUMP_CLI_TUN_PID"

    # Capture on server veth (underlay tunnel traffic)
    ip netns exec "$NS_SRV" \
        tcpdump -i veth_srv -n -w "$PCAP_DIR/server_veth_srv.pcap" \
        >/dev/null 2>&1 &
    TCPDUMP_SRV_VETH_PID=$!
    _verbose "tcpdump server veth_srv PID=$TCPDUMP_SRV_VETH_PID"

    # Give tcpdump processes time to start
    sleep 0.5
    _pass "tcpdump captures started"
    return 0
}

# ---------------------------------------------------------------------------
# Diagnostics output on e2e ping failure
# ---------------------------------------------------------------------------
_diagnostics() {
    local reason="${1:-unknown}"

    echo ""
    echo "============================================================"
    echo "  E2E Ping Diagnostics"
    echo "============================================================"
    echo "Failure reason: $reason"
    echo ""

    echo "--- Network Namespaces ---"
    ip netns list 2>/dev/null || echo "(none)"

    echo ""
    echo "--- Server Namespace ($NS_SRV): ip addr ---"
    ip netns exec "$NS_SRV" ip addr show 2>/dev/null || echo "(namespace not found)"

    echo ""
    echo "--- Client Namespace ($NS_CLI): ip addr ---"
    ip netns exec "$NS_CLI" ip addr show 2>/dev/null || echo "(namespace not found)"

    echo ""
    echo "--- Server Namespace ($NS_SRV): ip route ---"
    ip netns exec "$NS_SRV" ip route show 2>/dev/null || echo "(namespace not found)"

    echo ""
    echo "--- Client Namespace ($NS_CLI): ip route ---"
    ip netns exec "$NS_CLI" ip route show 2>/dev/null || echo "(namespace not found)"

    echo ""
    echo "--- Process Status ---"
    if [[ -n "${SERVER_PID:-}" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "Server PID $SERVER_PID: running"
    else
        echo "Server PID ${SERVER_PID:-N/A}: not running"
    fi
    if [[ -n "${CLIENT_PID:-}" ]] && kill -0 "$CLIENT_PID" 2>/dev/null; then
        echo "Client PID $CLIENT_PID: running"
    else
        echo "Client PID ${CLIENT_PID:-N/A}: not running"
    fi

    echo ""
    echo "--- Server Log (last 20 lines) ---"
    if [[ -n "${SERVER_LOG:-}" && -f "$SERVER_LOG" ]]; then
        tail -20 "$SERVER_LOG" 2>/dev/null || echo "(empty)"
    else
        echo "(no server log)"
    fi

    echo ""
    echo "--- Client Log (last 20 lines) ---"
    if [[ -n "${CLIENT_LOG:-}" && -f "$CLIENT_LOG" ]]; then
        tail -20 "$CLIENT_LOG" 2>/dev/null || echo "(empty)"
    else
        echo "(no client log)"
    fi

    if $TCPDUMP && [[ -n "${PCAP_DIR:-}" && -d "$PCAP_DIR" ]]; then
        echo ""
        echo "--- tcpdump captures ---"
        echo "  Server TUN (tun0):       $PCAP_DIR/server_tun0.pcap"
        echo "  Client TUN (tun1):       $PCAP_DIR/client_tun1.pcap"
        echo "  Server veth (underlay):  $PCAP_DIR/server_veth_srv.pcap"
        echo ""
        echo "  Inspect with:"
        echo "    tcpdump -r $PCAP_DIR/server_tun0.pcap -n"
        echo "    tcpdump -r $PCAP_DIR/server_veth_srv.pcap -n"
        echo "    tcpdump -r $PCAP_DIR/client_tun1.pcap -n"
    fi

    echo ""
    echo "--- Common Causes for E2E Ping Failure ---"
    echo ""
    echo "1. Session ID mismatch (KNOWN LIMITATION):"
    echo "   src/server.py and src/client.py each auto-generate independent"
    echo "   session_id values. Both ServerCore and ClientCore drop frames"
    echo "   with mismatched session IDs. The test suite works because it"
    echo "   passes a shared session_id to both cores."
    echo ""
    echo "   To verify: grep for 'Dropping frame' in server/client logs."
    echo ""
    echo "2. TUN routing / local delivery:"
    echo "   The kernel may route TUN-subnet packets locally rather than"
    echo "   through the TUN fd. Use 'ip route get <dst>' in the namespace"
    echo "   to check where the packet goes."
    echo ""
    echo "3. Transport-level issue:"
    echo "   If the TCP/WebSocket tunnel is not established, underlay ping"
    echo "   would have failed earlier. Check veth connectivity first."
    echo ""
    echo "4. TUN fd not reading/writing:"
    echo "   Enable DEBUG logging (VPN_LLM_LOG_LEVEL=DEBUG) and look for"
    echo "   'TUN->Transport READ' and 'Transport->TUN WROTE' messages."
    echo ""
    echo "Recommended next steps:"
    echo "  - Re-run with --keep --verbose and inspect logs manually"
    echo "  - Re-run with --tcpdump to capture packet traces"
    echo "  - See docs/phase10_netns_tun_validation.md for details"
    echo "  - See docs/phase3_netns_validation.md for deep troubleshooting"
    echo "============================================================"
}

# ---------------------------------------------------------------------------
# End-to-end TUN ping validation (Phase 10.4)
# ---------------------------------------------------------------------------
_e2e_ping_validation() {
    echo ""
    echo "--- Phase 10.4: E2E TUN Ping Validation ---"
    echo "Ping count:   $PING_COUNT"
    echo "Ping timeout: ${PING_TIMEOUT}s"
    echo ""

    # Start tcpdump if requested
    _start_tcpdump || true

    local ping_ok=true
    local failures=""

    # --- Client -> Server ping ---
    _info "ping: client TUN (10.8.0.2) -> server TUN (10.8.0.1) ..."
    if ip netns exec "$NS_CLI" ping -c "$PING_COUNT" -W "$PING_TIMEOUT" -I tun1 10.8.0.1; then
        _pass "client -> server ping OK"
    else
        ping_ok=false
        _fail "client -> server ping FAILED"
        failures="$failures  - client -> server: no reply\n"
    fi

    # --- Server -> Client ping ---
    _info "ping: server TUN (10.8.0.1) -> client TUN (10.8.0.2) ..."
    if ip netns exec "$NS_SRV" ping -c "$PING_COUNT" -W "$PING_TIMEOUT" -I tun0 10.8.0.2; then
        _pass "server -> client ping OK"
    else
        ping_ok=false
        _fail "server -> client ping FAILED"
        failures="$failures  - server -> client: no reply\n"
    fi

    if $ping_ok; then
        echo ""
        _pass "E2E TUN ping validation: ALL PASSED"
        echo ""
        echo "Both directions of IP packet forwarding through the TUN tunnel work."
        return 0
    else
        echo ""
        _fail "E2E TUN ping validation: FAILED"
        echo ""
        echo "Failure summary:"
        echo -n "$failures"
        echo ""
        echo "Note: E2E ping failure does NOT necessarily mean the Transport/Core"
        echo "replacement is broken. Common causes include TUN routing, session ID"
        echo "mismatch (known limitation), or kernel local delivery. See diagnostics below."
        echo ""

        # Check for session ID mismatch in logs
        if [[ -n "${SERVER_LOG:-}" && -f "$SERVER_LOG" ]]; then
            if grep -q "Dropping frame" "$SERVER_LOG" 2>/dev/null; then
                _warn "Detected 'Dropping frame' in server log — this indicates"
                _warn "session ID mismatch: server and client generate independent session IDs."
                _warn "This is a known limitation of the current src/server.py and src/client.py entry points."
            fi
        fi

        _diagnostics "ping did not receive replies"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

echo "=== Phase 10.3/10.4: netns + TUN Validation ==="
echo "Transport:   $TRANSPORT"
echo "Timeout:     ${TIMEOUT}s"
echo "Keep:        $KEEP"
echo "E2E Ping:    $E2E_PING"
if $E2E_PING; then
    echo "Ping count:  $PING_COUNT"
    echo "Ping timeout: ${PING_TIMEOUT}s"
    echo "Tcpdump:     $TCPDUMP"
fi
echo ""

_preflight

trap _cleanup EXIT

_setup_netns || exit 1
_verify_underlay || exit 1

_start_server || exit 1
_start_client || exit 1

_verify_tun_devices || exit 1
_configure_tun_ips || exit 1

if $E2E_PING; then
    _e2e_ping_validation || exit 1
fi

echo ""
echo "=== netns + TUN validation complete ==="
echo ""
echo "Namespaces: $NS_SRV, $NS_CLI"
echo "TUN devices: tun0=$NS_SRV, tun1=$NS_CLI"
echo ""
if $E2E_PING; then
    echo "E2E ping validation: completed"
else
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
    echo "  Or use --e2e-ping for automated verification:"
    echo "  sudo bash scripts/phase10_netns_tun_validation.sh --transport $TRANSPORT --e2e-ping"
fi
echo ""
echo "For detailed troubleshooting, see docs/phase10_netns_tun_validation.md"
echo "and docs/phase3_netns_validation.md"
echo ""
echo "Run with --keep to preserve the environment for manual testing."
