# Real Linux TUN Device Guide

> **Status**: Experimental - requires root or CAP_NET_ADMIN
>
> **Current Version**: v0.1-tcp-realtun
>
> **Document Scope**: This guide covers Phase 3 verification: manually configuring IP, routing, and using ping/tcpdump to verify real TUN packet flow. Auto-configuration scripts are out of scope for this phase.

---

## 1. Environment Requirements

- **OS**: Linux (required)
- **Kernel**: Linux with `/dev/net/tun` support
- **Permissions**: Root OR `CAP_NET_ADMIN` capability
- **Python**: 3.8+
- **Python dependencies**: `pip install -r requirements.txt`

### Check Python dependencies

```bash
cd /data/xjr/VPN-LLM/vpn_tunnel
pip install -r requirements.txt
```

---

## 2. Check TUN Environment

Before starting, verify your system supports TUN devices:

```bash
# Check /dev/net/tun exists
ls -l /dev/net/tun

# Expected output (crw-rw-rw-): character device, read/write for all
# If missing: "No such file or directory"

# Check kernel info
uname -a

# Check network interfaces
ip link show
```

### If /dev/net/tun does not exist

Your kernel may not have the TUN module loaded. Try:

```bash
sudo modprobe tun
```

If that fails, your kernel may not support TUN devices (common in containers or restricted environments).

### Check capabilities

```bash
# If you have capsh installed
capsh --print | grep net_admin

# Check process capabilities (replace $$ with your pid)
getpcaps $$
```

---

## 3. Start Server and Client

> **Important**: In real TUN mode, do NOT use `--mock-tun`. The program will automatically use LinuxTunDevice.

**Terminal 1 - Server (uses tun0):**

```bash
sudo python -m src.server --config config/server.yaml --transport tcp
```

**Terminal 2 - Client (uses tun1):**

```bash
sudo python -m src.client --config config/client.yaml --transport tcp
```

### Device name configuration

The server and client must use **different TUN device names** to avoid conflicts:

- Server: `tun0` (configured in `config/server.yaml`)
- Client: `tun1` (configured in `config/client.yaml`)

If you see `Device or resource busy`, stop the existing process and delete the stale device:

```bash
sudo ip link delete tun0
sudo ip link delete tun1
```

---

## 4. Configure TUN IP

The VPN tunnel uses these default IP addresses:
- Server (tun0): 10.8.0.1/24
- Client (tun1): 10.8.0.2/24

**Terminal 1 - Configure server TUN:**

```bash
sudo ip addr add 10.8.0.1/24 dev tun0
sudo ip link set tun0 up
```

**Terminal 2 - Configure client TUN:**

```bash
sudo ip addr add 10.8.0.2/24 dev tun1
sudo ip link set tun1 up
```

### Remove IP if needed

```bash
sudo ip addr del 10.8.0.1/24 dev tun0
sudo ip addr del 10.8.0.2/24 dev tun1
```

### Important warning for same-machine testing

> **⚠️ Same-machine routing conflicts**:
>
> If server and client run on the **same machine in the same network namespace**, both tun0 and tun1 using the **same /24 network (10.8.0.0/24)** will cause routing conflicts. The kernel may route packets directly without going through the TCP tunnel.
>
> **Recommended topologies for proper verification**:
> 1. **Two Linux hosts** (best): One runs server on tun0, the other runs client on tun0/tun1
> 2. **Two network namespaces**: Create separate namespaces to isolate routing
> 3. **Two VMs**: Each VM has its own routing table
>
> Same-machine testing can work but requires careful routing table management and may not prove end-to-end tunnel functionality.

---

## 5. Route Configuration

> **Note**: This project does NOT automatically modify system routes. Phase 3 requires manual route configuration.

### View current routes

```bash
ip route
```

### Add routes

**On Server (tun0) side:**

```bash
# Route to client's network through tun0
sudo ip route add 10.8.0.0/24 dev tun0
```

**On Client (tun1) side:**

```bash
# Route to server's network through tun1
sudo ip route add 10.8.0.0/24 dev tun1
```

### Enable IP forwarding (for routing between networks)

On the server or gateway:

```bash
# Temporarily enable
sudo sysctl -w net.ipv4.ip_forward=1

# Make permanent (add to /etc/sysctl.conf):
# net.ipv4.ip_forward = 1
```

---

## 6. tcpdump Verification

tcpdump allows you to observe whether packets are entering/leaving the TUN devices.

### Capture on tun0 (server side)

```bash
sudo tcpdump -i tun0 -n
```

### Capture on tun1 (client side)

```bash
sudo tcpdump -i tun1 -n
```

### Capture only ICMP (ping) packets

```bash
sudo tcpdump -i tun0 -n icmp
sudo tcpdump -i tun1 -n icmp
```

### Capture tunnel data (excluding control port 2222)

```bash
sudo tcpdump -i tun0 -n not port 2222
```

### Verbose output with hex dump

```bash
sudo tcpdump -i tun0 -nn -vv -x -X
```

### What to observe

- **ping request** (ICMP echo request) leaving tun1 toward 10.8.0.1
- **ping reply** (ICMP echo reply) entering tun0 from 10.8.0.1

If you see packets on tun1 but nothing arrives on tun0, the issue is likely:
1. No TCP connection between server and client
2. Routes not configured correctly
3. Server-side Core not reading from tun0

---

## 7. ping Verification

### Basic ping test

**From Client to Server:**

```bash
ping -I tun1 10.8.0.1
```

**From Server to Client:**

```bash
ping -I tun0 10.8.0.2
```

### Important limitations for same-machine testing

> **⚠️ Same-machine ping limitations**:
>
> `ping -I tun1 10.8.0.1` on a single machine does **NOT** conclusively prove the TCP tunnel is working. The kernel's routing table may handle this directly:
> - Packet originates from tun1's IP (10.8.0.2)
> - Kernel routes to 10.8.0.1
> - If tun0 is also on 10.8.0.0/24, kernel may deliver to tun0 directly
> - The TCP tunnel (server:2222 → client) is never used
>
> **Reliable verification requires**:
> 1. Two separate hosts/namespaces/VMs
> 2. tcpdump showing packets entering tun1 and leaving tun0 (or vice versa)
> 3. Confirming the TCP tunnel (port 2222) carries the data, not direct kernel routing
>
> Use `tcpdump -i tun0 not port 2222` to see tunnel data without control traffic.

---

## 8. Cleanup Commands

### Stop server/client

Press `Ctrl+C` to trigger graceful shutdown. The program will:
- Close TUN device
- Stop all threads
- Log statistics

### Delete TUN devices

```bash
sudo ip link delete tun0
sudo ip link delete tun1
```

### Delete all TUN devices (cleanup script)

```bash
for dev in tun0 tun1 tun2 tun99; do
    sudo ip link delete $dev 2>/dev/null
done
```

### If deletion fails

If `ip link delete` fails with "Device or resource busy":
- The server/client process still holds the TUN file descriptor
- Stop the process with `Ctrl+C` or `kill <pid>`
- Wait a moment, then retry

---

## 9. Common Errors and Troubleshooting

### Error: "Cannot open /dev/net/tun: Permission denied"

**Cause**: Not running as root, no CAP_NET_ADMIN capability.

**Solution**:
```bash
sudo python -m src.server --config config/server.yaml --transport tcp
```

Or set capability on Python binary (requires root):
```bash
sudo setcap 'cap_net_admin=eip' /usr/bin/python3
```

### Error: "No such file or directory: /dev/net/tun"

**Cause**: TUN kernel module not loaded or not available in container/VM.

**Solution**:
```bash
# Try loading the module
sudo modprobe tun

# If that fails, TUN is not available in this environment
# Check kernel config: CONFIG_TUN=y
```

### Error: "Device or resource busy" / "File exists"

**Cause**: TUN device already exists or process still holds fd.

**Solution**:
```bash
# Stop any running server/client
sudo pkill -f "src.server"
sudo pkill -f "src.client"

# Delete existing devices
sudo ip link delete tun0
sudo ip link delete tun1

# Or use a different device name
```

### Error: "Address already in use" for port 2222

**Cause**: Another process using port 2222.

**Solution**:
```bash
# Kill process using port
sudo fuser -k 2222/tcp

# Or find and kill manually
ps aux | grep src.server | grep -v grep
sudo kill <pid>
```

### Error: "Cannot set TUN device name to 'tun0': Invalid argument"

**Cause**: Device name too long (>15 chars) or ioctl failure.

**Solution**: Use shorter device names (tun0, tun1, vpn0, etc.)

### Error: ping fails - "Destination Net Unreachable"

**Possible causes**:
1. Route not configured - `ip route` shows no route to 10.8.0.0/24
2. TUN device not up - `ip link show tun0` shows state DOWN
3. IP not assigned - `ip addr show tun0` shows no 10.8.0.x address

**Debug steps**:
```bash
# Check device is up
ip link show tun0

# Check IP is assigned
ip addr show tun0

# Check routes
ip route

# Add route if missing
sudo ip route add 10.8.0.0/24 dev tun0
```

### Error: ping succeeds but tunnel not working

**Possible causes** (same-machine testing):
1. Kernel routes packets directly (bypasses TCP tunnel)
2. Both tun0 and tun1 on same /24 causes direct delivery
3. No actual tunnel data transfer

**Verification**:
```bash
# Watch tunnel data flow (exclude control port)
sudo tcpdump -i tun0 not port 2222

# If tcpdump shows no activity but ping works, kernel is routing directly
# This does NOT prove the VPN tunnel works
```

### Error: tcpdump shows packets but Core not receiving

**Possible causes**:
1. Server/client TCP connection not established
2. Core loops not running (check logs)
3. Firewall dropping packets

**Debug steps**:
```bash
# Check server logs for "TCP connection accepted"
# Check client logs for "TCP client connected"
# Check both show "Transport->TUN loop started"
```

---

## 10. Current Phase Status

### Phase 2 (v0.1-tcp-realtun) - Completed

**Verified** (2026-05-02):
- ✅ LinuxTunDevice can create real TUN devices
- ✅ TCP server/client can start and establish connection
- ✅ Graceful shutdown works (Ctrl+C)
- ✅ Server uses tun0, client uses tun1 (different devices)
- ✅ No root/CAP_NET_ADMIN errors during startup

### Phase 3 - In Progress

**Goal**: Manual IP configuration, routing, and ping/tcpdump verification of real TUN packet flow.

**Manual steps required**:
1. Configure IP addresses on tun0/tun1
2. Set routes
3. Enable IP forwarding if needed
4. Verify with ping and tcpdump

**Out of scope for Phase 3**:
- ❌ Automatic route configuration scripts
- ❌ NAT configuration
- ❌ Cross-host automated testing
- ❌ WebSocket/TLS/SSH real TUN integration

### Phase 3 Completion Criteria

1. tun0 shows IP 10.8.0.1, tun1 shows IP 10.8.0.2
2. `ip route` shows routes via tun0 and tun1
3. `ping -I tun1 10.8.0.1` shows packets traversing the tunnel
4. `tcpdump -i tun0 not port 2222` shows ICMP data
5. Cross-host test (two machines or namespaces) confirms end-to-end tunnel

---

## Quick Reference

```bash
# 1. Clean up
sudo ip link delete tun0 2>/dev/null
sudo ip link delete tun1 2>/dev/null

# 2. Start server (Terminal 1)
sudo python -m src.server --config config/server.yaml --transport tcp

# 3. Start client (Terminal 2)
sudo python -m src.client --config config/client.yaml --transport tcp

# 4. Configure IPs (separate terminals or commands)
sudo ip addr add 10.8.0.1/24 dev tun0 && sudo ip link set tun0 up
sudo ip addr add 10.8.0.2/24 dev tun1 && sudo ip link set tun1 up

# 5. Add routes
sudo ip route add 10.8.0.0/24 dev tun0
sudo ip route add 10.8.0.0/24 dev tun1

# 6. Enable forwarding (if routing between networks)
sudo sysctl -w net.ipv4.ip_forward=1

# 7. Verify with ping (best with two hosts/namespaces)
ping -I tun1 10.8.0.1

# 8. Capture with tcpdump
sudo tcpdump -i tun0 -n icmp

# 9. Clean up when done
sudo ip link delete tun0
sudo ip link delete tun1
```

---

## Related Documents

- [Project Status](status.md) - Current development phase and roadmap
- [TCP + Mock TUN Demo](demo_tcp_mocktun.md) - Using mock TUN devices for testing without root