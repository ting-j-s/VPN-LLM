# Phase 10.3/10.4: netns + TUN Validation for Transport/Core Replacement

## Goal

This phase establishes a **Linux netns + real TUN validation gate** for Transport/Core
replacement. It is the **second validation gate** in the LLM Agent replacement pipeline:

| Gate | What | Environment | Automation |
|---|---|---|---|
| **Gate 1** | Smoke matrix (`smoke_replacement_matrix.py`) | CI, developer laptop | Fully automated |
| **Gate 2** | netns + TUN validation (`phase10_netns_tun_validation.sh`) | Developer workstation (root) | Semi-automated |
| **Gate 3** | Full multi-machine integration | Lab environment | Manual |

Gate 1 answers: "Does the replacement survive basic send/recv?"  
Gate 2 answers: "Does the replacement work with real kernel TUN devices and IP packets?"  
Gate 3 answers: "Does the replacement work under real network conditions?"

Gate 2 has two modes:

| Mode | Flag | What it validates |
|---|---|---|
| **Default** (Phase 10.3) | _(none)_ | Environment, TUN creation, underlay connectivity, process health |
| **E2E Ping** (Phase 10.4) | `--e2e-ping` | Real IP packet forwarding through the TUN tunnel via ping |

## When to run Gate 2

Run this validation after:

1. The local smoke matrix (Gate 1) passes
2. The LLM Agent has applied a Transport or Core replacement patch
3. `git apply --check` and post-apply validation passed

Do **not** run in CI — this requires root privileges and kernel TUN support.

## Topology

```
┌─────────────────────────────────────────────────────────────────┐
│                         Host Machine                             │
│                                                                  │
│   ┌─────────────────────────┐   ┌─────────────────────────┐    │
│   │  vpn_srv_validation     │   │  vpn_cli_validation     │    │
│   │  (namespace)            │   │  (namespace)            │    │
│   │                         │   │                         │    │
│   │  tun0                   │   │  tun1                   │    │
│   │  10.8.0.1/24            │   │  10.8.0.2/24            │    │
│   │                         │   │                         │    │
│   │  veth_srv               │   │  veth_cli               │    │
│   │  192.168.200.1/24       │◄──┼── veth pair ───────────►│    │
│   │                         │   │  192.168.200.2/24       │    │
│   └─────────────────────────┘   └─────────────────────────┘    │
│            │                              │                     │
│            │  Transport tunnel            │                     │
│            │  (tcp:2222 or ws:port)       │                     │
│            └──────────────────────────────┘                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Address assignment

| Interface | Namespace | IP | Purpose |
|---|---|---|---|
| tun0 | vpn_srv_validation | 10.8.0.1/24 | Server TUN endpoint |
| tun1 | vpn_cli_validation | 10.8.0.2/24 | Client TUN endpoint |
| veth_srv | vpn_srv_validation | 192.168.200.1/24 | Underlay connectivity |
| veth_cli | vpn_cli_validation | 192.168.200.2/24 | Underlay connectivity |

> **Note on subnet choice**: This script uses `192.168.200.0/24` for veth (Phase 10.3)
> while Phase 3 used `192.168.100.0/24`. The different subnet avoids conflicts
> when both Phase 3 and Phase 10.3 namespaces exist simultaneously.

## Prerequisites

- **OS**: Linux (required — uses network namespaces and `/dev/net/tun`)
- **Permissions**: root or `CAP_NET_ADMIN`
- **Kernel**: `/dev/net/tun`, network namespace support, veth pairs
- **Tools**: `iproute2` (`ip`), `python3`
- **Project**: dependencies installed (`pip install -r requirements.txt`)

The script skips gracefully (exit 0) if prerequisites are not met.

## Usage

### Basic

```bash
cd /data/xjr/VPN-LLM
sudo ./scripts/phase10_netns_tun_validation.sh
```

### Specific transport

```bash
sudo ./scripts/phase10_netns_tun_validation.sh --transport websocket
```

### Keep environment for manual testing

```bash
sudo ./scripts/phase10_netns_tun_validation.sh --transport tcp --keep --verbose
```

After `--keep`, namespaces and TUN devices are preserved. Manually clean up with:

```bash
sudo ip netns delete vpn_cli_validation
sudo ip netns delete vpn_srv_validation
```

### CLI options

| Option | Default | Description |
|---|---|---|
| `--transport TYPE` | `tcp` | Transport to validate: `tcp` or `websocket` |
| `--timeout SECONDS` | `15` | Max wait for server/client startup |
| `--keep` | false | Preserve namespaces after validation |
| `--verbose` | false | Print detailed status and route tables |
| `--preflight-only` | false | Run only pre-flight checks, then exit. No namespaces, TUN devices, or processes. Safe for CI. |
| `--e2e-ping` | false | Enable end-to-end TUN ping verification (Phase 10.4) |
| `--ping-count N` | `2` | Number of ping packets (only with `--e2e-ping`) |
| `--ping-timeout SEC` | `5` | Ping timeout in seconds (only with `--e2e-ping`) |
| `--tcpdump` | false | Enable packet capture for diagnostics (only with `--e2e-ping`) |
| `-h`, `--help` | — | Show help |

### CI and lightweight checks

For CI pipelines or quick environment checks, use `--preflight-only`:

```bash
# Safe for CI — exits 0, no namespaces created, no root required
bash scripts/phase10_netns_tun_validation.sh --preflight-only
```

This mode:
- Runs all pre-flight checks including the real netns capability probe
- Exits 0 with SKIP if the environment cannot create network namespaces
- Exits 0 with PASS if all prerequisites are met
- Never creates namespaces, TUN devices, or server/client processes
- Never requires root (but handles root correctly if present)

## What the script validates

The script performs the following checks in order:

1. **Pre-flight**: Linux OS, `ip`, `python3`, `/dev/net/tun`, root/CAP_NET_ADMIN, real netns capability probe — skip if missing
2. **Setup**: Create `vpn_srv_validation` and `vpn_cli_validation` namespaces, veth pair, assign IPs
3. **Underlay connectivity**: `ping -c 3` from client namespace to server namespace over veth
4. **Server startup**: Start `src/server.py` with real TUN (`--mock-tun` is NOT used)
5. **Client startup**: Start `src/client.py` with real TUN, verify tunnel established
6. **TUN device check**: Verify `tun0` exists in server namespace, `tun1` exists in client namespace
7. **TUN IP configuration**: Assign `10.8.0.1/24` to `tun0`, `10.8.0.2/24` to `tun1`
8. **Cleanup** (unless `--keep`): Kill processes, delete namespaces

## What the script does NOT validate

- **TUN-to-TUN ping**: The script does not run `ping -I tun1 10.8.0.1` because this depends
  on correct routing within each namespace. Kernel may local-deliver packets instead of
  sending them through the TUN fd. See Phase 3 docs for manual ping verification steps.
- **Long-running stability**: The script checks startup only. For soak testing, use `--keep`
  and run manual tests.
- **Data integrity**: The script checks that tunnel processes start and TUN devices appear.
  It does not verify that IP packets pass correctly through the tunnel.

## Manual real-IP-packet verification

After successful script execution with `--keep`, verify real IP packet flow:

### Terminal 1: tcpdump on server TUN

```bash
sudo ip netns exec vpn_srv_validation tcpdump -i tun0 -n icmp
```

### Terminal 2: tcpdump on client TUN

```bash
sudo ip netns exec vpn_cli_validation tcpdump -i tun1 -n icmp
```

### Terminal 3: tcpdump on veth (tunnel traffic)

```bash
sudo ip netns exec vpn_srv_validation tcpdump -i veth_srv -n
```

### Terminal 4: ping through the tunnel

```bash
sudo ip netns exec vpn_cli_validation ping -I tun1 10.8.0.1
```

### Expected results

1. **Client tun1 tcpdump**: ICMP echo request out
2. **Client veth_cli** (if monitored): TCP/WebSocket frames carrying encapsulated data
3. **Server veth_srv** (if monitored): TCP/WebSocket frames arriving
4. **Server tun0 tcpdump**: ICMP echo request in, ICMP echo reply out
5. **Client tun1 tcpdump**: ICMP echo reply in
6. **ping output**: replies with RTT

If ping does not receive replies, refer to the troubleshooting guide in
[docs/phase3_netns_validation.md](phase3_netns_validation.md) (Section 8).

### Common issues

| Symptom | Likely cause | Check |
|---|---|---|
| ping with no output | Kernel local-delivers to TUN IP | `ip route get 10.8.0.1` in client ns |
| ping: Network is unreachable | No route to TUN subnet | `ip route` in client ns |
| Server died during startup | Port already in use | `ss -tlnp` in server ns |
| No TUN device | Process died before opening TUN | Check server/client logs |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All checks passed, or prerequisites not met (skip) |
| 1 | Validation failed (infrastructure or process error) |

## Relationship to Phase 3

Phase 3 (`docs/phase3_netns_validation.md`) provides a manual step-by-step guide for
network namespace validation with detailed troubleshooting. Phase 10.3 builds on that
knowledge and adds:

- **Automated setup/teardown**: Single script instead of multiple terminals
- **Transport parameterization**: Support for both TCP and WebSocket
- **Graceful skipping**: Exit 0 when prerequisites are missing (CI-safe)
- **Replacement validation context**: Designed to be the second gate after smoke matrix

For deep troubleshooting of TUN/ping issues, always refer to Phase 3 documentation.

## E2E Ping Mode (Phase 10.4)

### Enabling e2e ping

```bash
# Basic e2e ping with TCP
sudo ./scripts/phase10_netns_tun_validation.sh --transport tcp --e2e-ping

# With verbose output and packet capture
sudo ./scripts/phase10_netns_tun_validation.sh --transport websocket --e2e-ping --verbose --tcpdump

# Custom ping parameters
sudo ./scripts/phase10_netns_tun_validation.sh --transport tcp --e2e-ping --ping-count 5 --ping-timeout 3
```

### What e2e ping validates

The `--e2e-ping` mode adds the following checks after the default mode:

1. **Shared session ID**: The script passes a fixed test `--session-id` to both server
   and client, ensuring they share the same session identifier and accept each other's frames.
2. **Explicit TUN routing**: Configures point-to-point TUN IPs (`peer /32` notation) and
   adds explicit routes (`ip route add 10.8.0.x dev tunX`).
3. **Client → Server ping**: `ping -I tun1 -c $PING_COUNT -W $PING_TIMEOUT 10.8.0.1`
4. **Server → Client ping**: `ping -I tun0 -c $PING_COUNT -W $PING_TIMEOUT 10.8.0.2`

### What ping success means

If ping succeeds:
- The Transport tunnel is carrying real IP packets
- ClientCore correctly reads ICMP from tun1 and sends DATA frames
- ServerCore correctly receives DATA frames and writes ICMP to tun0
- The kernel routes the ICMP reply back through tun0 → ServerCore → Transport → ClientCore → tun1
- **This confirms end-to-end IP packet forwarding through the replacement Transport/Core**

### What ping failure means

Ping failure does **NOT** necessarily mean the Transport/Core replacement is broken.
Common causes:

| Cause | Diagnostic signal |
|---|---|
| **Session ID mismatch** | `grep "Dropping frame" server.log` shows drops. The script passes shared `--session-id`, so this typically means manual invocation without matching IDs. |
| **Kernel local delivery** | `ip route get 10.8.0.1` in client ns shows `local` |
| **TUN routing missing** | `ip route get 10.8.0.1` shows no route or wrong device |
| **Transport tunnel broken** | Underlay veth ping would have failed earlier |
| **TUN fd not reading** | No `TUN->Transport READ` in DEBUG server/client logs |

### Session ID sharing

`ServerCore` and `ClientCore` each validate that incoming frames carry their own
`session_id`. When the server and client generate independent session IDs (the default),
every frame is dropped by the receiving end — including TUN ping packets.

**Shared session ID**: The `--session-id HEX` CLI flag and `session_id` config field
allow both endpoints to use the same 16-byte session identifier:

```bash
# Via CLI (priority: CLI > config > random)
python3 -m src.server --config config/server_netns.yaml --session-id 00112233445566778899aabbccddeeff
python3 -m src.client --config config/client_netns.yaml --session-id 00112233445566778899aabbccddeeff

# Via config
session_id: "00112233445566778899aabbccddeeff"
```

**Priority**: CLI `--session-id` > config `session_id` > auto-generated (random).

The netns validation script (`phase10_netns_tun_validation.sh`) automatically passes
a fixed test `--session-id` to both server and client, so e2e ping works with shared
session isolation.

> **Note**: `session_id` is a session isolation identifier, not an authentication
> secret or cryptographic key. In production, the session ID should be negotiated
> or distributed by the control plane.

**`Dropping frame` as a diagnostic signal**: If `"Dropping frame"` appears in logs
during e2e ping, it means the endpoints are using different session IDs — check that
`--session-id` matches on both sides.

### Key differences: default vs e2e-ping mode

| Aspect | Default mode | `--e2e-ping` mode |
|---|---|---|
| Environment setup | Yes | Yes |
| Underlay veth ping | Yes | Yes |
| TUN creation check | Yes | Yes |
| Shared session ID | No | Yes (auto-passed via `--session-id`) |
| TUN IP configuration | `/24` subnet | `peer /32` point-to-point |
| Process health check | Yes | Yes |
| Explicit TUN routes | No | Yes |
| Real IP ping through tunnel | No | Yes (bidirectional) |
| tcpdump capture | No | Optional (`--tcpdump`) |
| Failure diagnostics | Basic | Full (ip addr, ip route, logs, pcaps) |
| Session ID mismatch detection | No | Yes (auto-detected from logs) |

### tcpdump auxiliary diagnosis

When `--tcpdump` is enabled, three packet captures are created:

```
/tmp/vpn_validation_pcap.XXXXXX/
├── server_tun0.pcap      # ICMP on server TUN
├── client_tun1.pcap      # ICMP on client TUN
└── server_veth_srv.pcap  # Tunnel traffic on underlay
```

Inspect with:
```bash
tcpdump -r /tmp/vpn_validation_pcap.XXXXXX/server_tun0.pcap -n
tcpdump -r /tmp/vpn_validation_pcap.XXXXXX/server_veth_srv.pcap -n
tcpdump -r /tmp/vpn_validation_pcap.XXXXXX/client_tun1.pcap -n
```

**Expected for success**:
- `server_veth_srv.pcap`: TCP/WebSocket frames carrying encapsulated DATA
- `server_tun0.pcap`: ICMP echo request (in) and echo reply (out)
- `client_tun1.pcap`: ICMP echo request (out) and echo reply (in)

**Expected when session ID mismatch**:
- `server_veth_srv.pcap`: TCP/WebSocket frames visible (transport works)
- `server_tun0.pcap`: Empty (server drops frames, never writes to tun0)
- `client_tun1.pcap`: ICMP echo request (out) only, no reply (in)

### Common diagnostic steps

1. **Check session ID**:
   ```bash
   grep -E "(session_id=|Dropping frame|Session ID:)" /tmp/vpn_server_validation.*.log
   grep -E "(session_id=|Dropping frame|Session ID:)" /tmp/vpn_client_validation.*.log
   ```

2. **Check TUN routing in each namespace**:
   ```bash
   sudo ip netns exec vpn_srv_validation ip route show
   sudo ip netns exec vpn_cli_validation ip route get 10.8.0.1
   ```

3. **Check process health**:
   ```bash
   sudo ip netns exec vpn_srv_validation ss -tlnp | grep 2222
   sudo ip netns exec vpn_cli_validation ss -tnp | grep 2222
   ```

4. **Enable DEBUG logging for TUN fd visibility**:
   ```bash
   sudo VPN_LLM_LOG_LEVEL=DEBUG bash scripts/phase10_netns_tun_validation.sh --transport tcp --keep --verbose
   # Then inspect logs for TUN->Transport READ / Transport->TUN WROTE
   ```

## Integration with LLM Agent workflow

The netns validation is a manual step in the LLM Agent replacement pipeline:

```
LLM generates patch
  → git apply --check
  → human confirms --apply-patch
  → post-apply validation
  → --run-replacement-smoke (Gate 1: automated, CI-safe)
  → phase10_netns_tun_validation.sh (Gate 2a: default mode, requires root)
  → phase10_netns_tun_validation.sh --e2e-ping (Gate 2b: e2e ping, requires root)
  → commit advice
```

Gate 2a (default mode) verifies the infrastructure: namespaces, TUN devices, underlay
connectivity, and process health. Gate 2b (e2e-ping) adds real IP packet forwarding
verification. Both require explicit human invocation — the script is intentionally
NOT invoked automatically by the LLM Agent because root privileges and kernel TUN
support are not available in CI or typical developer environments without explicit setup.

## Files

| File | Purpose |
|---|---|
| `scripts/phase10_netns_tun_validation.sh` | Main validation script |
| `docs/phase10_netns_tun_validation.md` | This document |
| `config/server_netns.yaml` | Server config (used by script) |
| `config/client_netns.yaml` | Client config (used by script) |
| `docs/phase3_netns_validation.md` | Detailed manual troubleshooting guide |
