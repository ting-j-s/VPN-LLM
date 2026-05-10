# Phase 10.3: netns + TUN Validation for Transport/Core Replacement

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
| `-h`, `--help` | — | Show help |

## What the script validates

The script performs the following checks in order:

1. **Pre-flight**: Linux OS, `ip`, `python3`, `/dev/net/tun`, root/CAP_NET_ADMIN — skip if missing
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

## Integration with LLM Agent workflow

The netns validation is a manual step in the LLM Agent replacement pipeline:

```
LLM generates patch
  → git apply --check
  → human confirms --apply-patch
  → post-apply validation
  → --run-replacement-smoke (Gate 1: automated, CI-safe)
  → phase10_netns_tun_validation.sh (Gate 2: manual, requires root)
  → commit advice
```

The script is intentionally NOT invoked automatically by the LLM Agent — it requires
root privileges and kernel TUN support, which are not available in CI or typical
developer environments without explicit setup.

## Files

| File | Purpose |
|---|---|
| `scripts/phase10_netns_tun_validation.sh` | Main validation script |
| `docs/phase10_netns_tun_validation.md` | This document |
| `config/server_netns.yaml` | Server config (used by script) |
| `config/client_netns.yaml` | Client config (used by script) |
| `docs/phase3_netns_validation.md` | Detailed manual troubleshooting guide |
