# Phase 3 Network Namespace Validation Guide

> **Purpose**: Use Linux network namespaces to simulate two isolated hosts on one machine, enabling reliable verification of TCP + real TUN + routing + ping/tcpdump without needing two physical machines.
>
> **Scope**: This guide covers Phase 3 verification - proving that real IP packets flow through the TCP tunnel between isolated network namespaces.

---

## 1. Topology Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Host Machine                             │
│                                                                  │
│   ┌─────────────────────┐     ┌─────────────────────┐          │
│   │    vpn_srv          │     │    vpn_cli          │          │
│   │   (namespace)       │     │   (namespace)       │          │
│   │                     │     │                     │          │
│   │   tun0              │     │   tun1              │          │
│   │   10.8.0.1/24       │     │   10.8.0.2/24       │          │
│   │                     │     │                     │          │
│   │   veth_srv          │     │   veth_cli          │          │
│   │   192.168.100.1/24  │◄────┼──── veth pair ──────►│          │
│   │                     │     │   192.168.100.2/24  │          │
│   └─────────────────────┘     └─────────────────────┘          │
│            │                              │                     │
│            │    TCP tunnel                │                     │
│            │    (port 2222)               │                     │
│            └──────────────────────────────┘                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Network Summary

| Interface | Namespace | IP Address | Purpose |
|-----------|-----------|------------|---------|
| tun0 | vpn_srv | 10.8.0.1/24 | VPN server TUN device |
| tun1 | vpn_cli | 10.8.0.2/24 | VPN client TUN device |
| veth_srv | vpn_srv | 192.168.100.1/24 | Cross-namespace connectivity |
| veth_cli | vpn_cli | 192.168.100.2/24 | Cross-namespace connectivity |

### Packet Flow (when ping works correctly)

```
vpn_cli: ping -I tun1 10.8.0.1
  │
  ├─> tun1 (10.8.0.2) - ICMP echo request
  │
  ├─> ClientCore reads from tun1 → Frame → TCP tunnel (veth_cli:veth_srv → port 2222)
  │
  ├─> Server receives via TCP → ServerCore → writes to tun0
  │
  ├─> tun0 (10.8.0.1) - ICMP echo request
  │
  ├─> Kernel routes reply to 10.8.0.2 via tun0
  │
  ├─> ServerCore reads from tun0 → Frame → TCP tunnel
  │
  └─> Client receives → tun1 → ICMP echo reply → ping succeeds
```

---

## 2. Prerequisites

### Requirements

- **OS**: Linux (required)
- **Permissions**: Root (sudo)
- **Kernel features**: `/dev/net/tun`, network namespaces, veth pairs
- **Tools**: `iproute2` (provides `ip netns`, `ip link`, `ip addr`, `ip route`)
- **Tools**: `tcpdump`
- **Python**: Version 3.8+
- **Project**: Dependencies installed (`pip install -r requirements.txt`)

### Check Prerequisites

```bash
# Check /dev/net/tun exists
ls -l /dev/net/tun

# Check network namespace support
ip netns list

# Check iproute2 installed
ip -version

# Check tcpdump installed
tcpdump --version

# Check Python and dependencies
python --version
python -m pytest tests/ -v
```

### If ip netns list fails

If `ip netns list` shows error, you may need to create `/var/run/netns` directory:

```bash
sudo mkdir -p /var/run/netns
```

---

## 3. Setup Network Namespaces

### Option A: Use Setup Script (Recommended)

```bash
cd /data/xjr/VPN-LLM/vpn_tunnel
sudo scripts/phase3_netns/setup_netns.sh
```

### Option B: Manual Commands

```bash
# 1. Create namespaces
sudo ip netns add vpn_srv
sudo ip netns add vpn_cli

# 2. Create veth pair
sudo ip link add veth_srv type veth peer name veth_cli

# 3. Move veth endpoints to namespaces
sudo ip link set veth_srv netns vpn_srv
sudo ip link set veth_cli netns vpn_cli

# 4. Configure server namespace
sudo ip netns exec vpn_srv ip addr add 192.168.100.1/24 dev veth_srv
sudo ip netns exec vpn_srv ip link set lo up
sudo ip netns exec vpn_srv ip link set veth_srv up

# 5. Configure client namespace
sudo ip netns exec vpn_cli ip addr add 192.168.100.2/24 dev veth_cli
sudo ip netns exec vpn_cli ip link set lo up
sudo ip netns exec vpn_cli ip link set veth_cli up
```

### Test veth Connectivity

```bash
sudo ip netns exec vpn_cli ping -c 3 192.168.100.1
```

Expected output: `3 packets transmitted, 3 received, 0% packet loss`

---

## 4. Configuration Files

Two configuration files are provided for namespace testing:

### config/server_netns.yaml

```yaml
server:
  tun_name: tun0
  tun_ip: 10.8.0.1
  tun_peer: 10.8.0.2
  mtu: 1400
  listen_port: 2222

forwarding:
  enable_nat: false
  enable_route: true

transport:
  type: tcp

session:
  heartbeat_timeout: 30
```

### config/client_netns.yaml

```yaml
client:
  tun_name: tun1
  tun_ip: 10.8.0.2
  tun_peer: 10.8.0.1
  mtu: 1400

server:
  host: 192.168.100.1
  port: 2222

transport:
  type: tcp

session:
  heartbeat_interval: 10
  reconnect: true
  reconnect_interval: 3
```

---

## 5. Start Server and Client

### Terminal 1 - Start Server

```bash
cd /data/xjr/VPN-LLM/vpn_tunnel
sudo ip netns exec vpn_srv python -m src.server --config config/server_netns.yaml --transport tcp
```

Expected output:
```
INFO - LinuxTunDevice 'tun0' opened (FD=3, MTU=1400)
INFO - TCP server listening on 0.0.0.0:2222
INFO - Waiting for TCP connection...
```

### Terminal 2 - Start Client

```bash
cd /data/xjr/VPN-LLM/vpn_tunnel
sudo ip netns exec vpn_cli python -m src.client --config config/client_netns.yaml --transport tcp
```

Expected output:
```
INFO - LinuxTunDevice 'tun1' opened (FD=3, MTU=1400)
INFO - TCP client connected
INFO - Client core started
INFO - Tunnel established, running...
```

### Verify Connection

On Server terminal, you should see:
```
INFO - TCP connection accepted from ('192.168.100.2', ...)
INFO - Server core started
INFO - Tunnel listening, running...
```

---

## 6. Configure TUN IP Addresses

### Terminal 3 - Configure Server TUN

```bash
sudo ip netns exec vpn_srv ip addr add 10.8.0.1/24 dev tun0
sudo ip netns exec vpn_srv ip link set tun0 up
```

### Terminal 4 - Configure Client TUN

```bash
sudo ip netns exec vpn_cli ip addr add 10.8.0.2/24 dev tun1
sudo ip netns exec vpn_cli ip link set tun1 up
```

### Verify Configuration

```bash
# Check TUN devices exist with correct IPs
sudo ip netns exec vpn_srv ip addr show tun0
sudo ip netns exec vpn_cli ip addr show tun1

# Check routes
sudo ip netns exec vpn_srv ip route
sudo ip netns exec vpn_cli ip route
```

Expected for vpn_srv:
```
tun0: <POINTOPOINT,MULTICAST,NOARP,UP,LOWER_UP> mtu 1400
    link/none
    inet 10.8.0.1/24 scope global tun0
```

Expected routes in vpn_srv:
```
10.8.0.0/24 dev tun0 proto kernel scope link src 10.8.0.1
192.168.100.0/24 dev veth_srv proto kernel scope link src 192.168.100.1
```

---

## 7. tcpdump Verification

tcpdump is essential to confirm packets actually traverse the TCP tunnel, not direct kernel routing.

### Terminal 5 - Capture on Server TUN (tun0)

```bash
sudo ip netns exec vpn_srv tcpdump -i tun0 -n icmp
```

### Terminal 6 - Capture on Client TUN (tun1)

```bash
sudo ip netns exec vpn_cli tcpdump -i tun1 -n icmp
```

### Terminal 7 - Capture TCP Tunnel Traffic (veth)

```bash
# See only tunnel data (exclude control port)
sudo ip netns exec vpn_srv tcpdump -i veth_srv -n tcp port 2222
sudo ip netns exec vpn_cli tcpdump -i veth_cli -n tcp port 2222
```

### What to Observe During ping -I tun1 10.8.0.1

1. **vpn_cli tcpdump tun1**: You should see ICMP echo request leaving tun1 toward 10.8.0.1
2. **vpn_cli tcpdump veth_cli**: You should see TCP packets carrying Frame data (not plain ICMP) going to 192.168.100.1:2222
3. **vpn_srv tcpdump veth_srv**: You should see TCP packets arriving from 192.168.100.2:port toward port 2222
4. **vpn_srv tcpdump tun0**: You should see ICMP echo request arriving on tun0 from 10.8.0.2

If you see ICMP on tun1 but no TCP traffic on veth_cli, the tunnel forwarding is not working.

---

## 8. ping Verification

### From Client to Server

```bash
sudo ip netns exec vpn_cli ping -I tun1 10.8.0.1
```

### What Success Looks Like

- ping shows `64 bytes from 10.8.0.1: icmp_seq=X ttl=64 time=X ms`
- Client tun1 tcpdump shows ICMP echo request
- Server tun0 tcpdump shows ICMP echo request arriving
- Server tun0 tcpdump shows ICMP echo reply leaving
- Client tun1 tcpdump shows ICMP echo reply arriving
- tcpdump on veth_srv/veth_cli shows TCP tunnel carrying Frame data

### If ping fails

**排查顺序 (Troubleshooting order)**:

1. **Verify veth connectivity first**
   ```bash
   sudo ip netns exec vpn_cli ping -c 3 192.168.100.1
   ```
   If this fails, the namespace/veth setup is broken.

2. **Check TCP tunnel established**
   ```bash
   sudo ip netns exec vpn_srv ss -tnp | grep 2222
   sudo ip netns exec vpn_cli ss -tnp | grep 2222
   ```
   Should show ESTAB connection.

3. **Confirm TUN device IPs**
   ```bash
   sudo ip netns exec vpn_srv ip addr show tun0 | grep 10.8.0.1
   sudo ip netns exec vpn_cli ip addr show tun1 | grep 10.8.0.2
   ```

4. **Check routing - where does ping go?**
   ```bash
   # In vpn_cli, where does 10.8.0.1 go?
   sudo ip netns exec vpn_cli ip route get 10.8.0.1
   # Expected: 10.8.0.1 dev tun1 src 10.8.0.2
   # If it says "local", kernel will local-deliver, not send to TUN fd

   # In vpn_srv, where does 10.8.0.2 go?
   sudo ip netns exec vpn_srv ip route get 10.8.0.2
   ```

5. **Check TUN interface statistics (don't just look at RX/TX)**
   ```bash
   # Use -s to see actual byte counts, look at both directions
   sudo ip netns exec vpn_cli ip -s link show tun1
   sudo ip netns exec vpn_srv ip -s link show tun0
   ```
   Note: Kernel delivering TO TUN fd may appear as TX (out of interface).
   Kernel receiving FROM TUN fd may appear as RX.

6. **Use tcpdump to find where packet stops**

   Step-by-step:
   ```bash
   # Terminal A - watch client tun1
   sudo ip netns exec vpn_cli tcpdump -i tun1 -n -vv icmp

   # Terminal B - watch client veth_cli
   sudo ip netns exec vpn_cli tcpdump -i veth_cli -n -vv tcp port 2222

   # Terminal C - watch server veth_srv
   sudo ip netns exec vpn_srv tcpdump -i veth_srv -n -vv tcp port 2222

   # Terminal D - watch server tun0
   sudo ip netns exec vpn_srv tcpdump -i tun0 -n -vv icmp

   # Terminal E - run ping
   sudo ip netns exec vpn_cli ping -I tun1 10.8.0.1
   ```

   Expected pattern for SUCCESS:
   - tun1: ICMP echo request OUT (TX)
   - veth_cli: TCP packets carrying DATA frames
   - veth_srv: TCP packets arriving
   - tun0: ICMP echo request IN (RX)
   - (server processes, generates reply)
   - tun0: ICMP echo reply OUT (TX)
   - veth_srv: TCP packets carrying DATA frames
   - veth_cli: TCP packets arriving
   - tun1: ICMP echo reply IN (RX)
   - ping shows reply

7. **Enable DEBUG logging to see TUN fd read/write**

   ```bash
   # Start server with DEBUG
   sudo ip netns exec vpn_srv env VPN_LLM_LOG_LEVEL=DEBUG python -m src.server --config config/server_netns.yaml --transport tcp

   # Start client with DEBUG (separate terminal)
   sudo ip netns exec vpn_cli env VPN_LLM_LOG_LEVEL=DEBUG python -m src.client --config config/client_netns.yaml --transport tcp
   ```

   Look for these DEBUG messages:
   - `LinuxTunDevice 'tun1' READ: len=84 src=10.8.0.2 dst=10.8.0.1 proto=ICMP` - packet read from TUN fd
   - `TUN->Transport READ packet: len=84 bytes, total sent: ...` - packet sent to tunnel
   - `Transport->TUN RECEIVED frame: type=DATA ...` - frame received from tunnel
   - `Transport->TUN WROTE packet: len=84 bytes` - packet written to TUN fd

   If you see TUN READ but no TUN->Transport sent, the packet was read but not sent to transport.
   If you see Transport->TUN RECEIVED but no WROTE, the frame was received but couldn't write to TUN.

8. **Use tun_fd_probe.py to isolate TUN fd issue**

   ```bash
   # Terminal 1 - start probe (creates tun_probe)
   sudo ip netns exec vpn_cli python scripts/phase3_netns/tun_fd_probe.py --name tun_probe

   # Terminal 2 - configure IP and ping from different subnet
   sudo ip netns exec vpn_cli ip addr add 10.9.0.2/24 dev tun_probe
   sudo ip netns exec vpn_cli ip link set tun_probe up

   # Terminal 3 - configure server side
   sudo ip netns exec vpn_srv ip addr add 10.9.0.1/24 dev tun0
   sudo ip netns exec vpn_srv ip link set tun0 up

   # Terminal 4 - ping (from vpn_srv to test server-side delivery)
   sudo ip netns exec vpn_srv ping -I tun0 10.9.0.1  # should fail (local)

   # Better: use ping from vpn_srv to external IP through tun_probe
   # But this requires routing setup

   # Alternative: ping from vpn_cli to 10.9.0.1
   sudo ip netns exec vpn_cli ping -I tun_probe 10.9.0.1
   ```

   If tun_fd_probe.py shows packets, TUN fd works.
   If tun_fd_probe.py shows nothing, TUN fd or routing has issue.

9. **Check TUN device state**
   ```bash
   # Is TUN UP?
   sudo ip netns exec vpn_cli ip link show tun1 | grep UP

   # Is TUN point-to-point correct?
   sudo ip netns exec vpn_cli ip -o link show tun1
   ```

10. **Common issues and fixes**

    | Symptom | Likely Cause | Fix |
    |---------|-------------|-----|
    | tun1 TX counter increments but no TCP on veth_cli | Routing sends to TUN but kernel local-delivers | Check `ip route get 10.8.0.1` |
    | TCP on veth but no DATA frames in tcpdump | TUN->Transport loop not calling send() | Enable DEBUG, check logs |
    | No RX/TX on tun1 during ping | Kernel doesn't route to TUN fd | Check routing table |
    | Connection refused | Server not listening | Check `ss -tlnp` |
    | Connection already exists | Stale connection | Delete namespace and recreate |

---

## 9. Cleanup

### Option A: Use Cleanup Script (Recommended)

```bash
cd /data/xjr/VPN-LLM/vpn_tunnel
sudo scripts/phase3_netns/cleanup_netns.sh
```

### Option B: Manual Cleanup

```bash
# Ctrl+C to stop server and client first, then:
sudo ip netns delete vpn_srv 2>/dev/null
sudo ip netns delete vpn_cli 2>/dev/null
```

### If namespace deletion fails

If `ip netns delete` fails with "Device or resource busy":
- Server or client process is still running in the namespace
- Press Ctrl+C in the server/client terminals
- Wait a moment, then retry deletion

---

## 10. Full Workflow Script

```bash
# Phase 3 validation workflow

# 1. Setup
cd /data/xjr/VPN-LLM/vpn_tunnel
sudo scripts/phase3_netns/setup_netns.sh

# 2. Verify veth connectivity
sudo scripts/phase3_netns/show_state.sh

# 3. Start server (Terminal 1)
sudo ip netns exec vpn_srv python -m src.server --config config/server_netns.yaml --transport tcp

# 4. Start client (Terminal 2)
sudo ip netns exec vpn_cli python -m src.client --config config/client_netns.yaml --transport tcp

# 5. Configure TUN IPs (Terminals 3 & 4)
sudo ip netns exec vpn_srv ip addr add 10.8.0.1/24 dev tun0 && sudo ip netns exec vpn_srv ip link set tun0 up
sudo ip netns exec vpn_cli ip addr add 10.8.0.2/24 dev tun1 && sudo ip netns exec vpn_cli ip link set tun1 up

# 6. tcpdump on TUNs (Terminals 5 & 6)
sudo ip netns exec vpn_srv tcpdump -i tun0 -n icmp
sudo ip netns exec vpn_cli tcpdump -i tun1 -n icmp

# 7. ping test (new terminal)
sudo ip netns exec vpn_cli ping -I tun1 10.8.0.1

# 8. Cleanup
sudo scripts/phase3_netns/cleanup_netns.sh
```

---

## 11. Phase 3 Completion Criteria

Phase 3 is complete when ALL of the following are verified:

1. ✅ `ip netns list` shows both `vpn_srv` and `vpn_cli`
2. ✅ Server starts with `LinuxTunDevice 'tun0' opened`
3. ✅ Client starts with `LinuxTunDevice 'tun1' opened`
4. ✅ Server logs show `TCP connection accepted from ('192.168.100.2', ...)`
5. ✅ `ip netns exec vpn_srv ip addr show tun0` shows `10.8.0.1/24`
6. ✅ `ip netns exec vpn_cli ip addr show tun1` shows `10.8.0.2/24`
7. ✅ `ping -I tun1 10.8.0.1` succeeds with replies
8. ✅ tcpdump on `veth_srv` or `veth_cli` shows TCP port 2222 traffic during ping
9. ✅ tcpdump on `tun0` shows ICMP echo request and reply during ping
10. ✅ Ctrl+C cleanly shuts down server and client

---

## Related Documents

- [Real Linux TUN Guide](real_tun_linux.md) - General real TUN setup and troubleshooting
- [Project Status](status.md) - Current phase and roadmap