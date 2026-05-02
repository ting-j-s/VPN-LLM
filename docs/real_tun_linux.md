# Real Linux TUN Device Guide

> **Status**: Experimental - requires root or CAP_NET_ADMIN

This guide explains how to use real Linux TUN devices instead of mock TUN devices. It covers setup, IP configuration, routing, verification with ping/tcpdump, and cleanup.

## Environment Requirements

- Linux kernel with `/dev/net/tun` support
- Root privileges OR `CAP_NET_ADMIN` capability
- Python 3.8+

## Check /dev/net/tun

```bash
# Check if /dev/net/tun exists
ls -la /dev/net/tun

# Check your capabilities (if you have capsh installed)
capsh --print | grep net_admin

# Or check with getpcaps (replace $$ with your pid)
getpcaps $$
```

## Start Server and Client

You need two different TUN device names (e.g., `tun0` for server, `tun1` for client).

**Terminal 1 - Server:**
```bash
sudo python -m src.server --config config/server.yaml --transport tcp
```

**Terminal 2 - Client:**
```bash
sudo python -m src.client --config config/client.yaml --transport tcp
```

The server uses `tun0`, the client uses `tun1` (configured in `config/server.yaml` and `config/client.yaml`).

If you see `Device or resource busy`, the TUN device name is already in use. Choose a different name or delete the existing device.

## Configure IP Addresses

The VPN tunnel uses these IP addresses by default:
- Server (tun0): 10.8.0.1
- Client (tun1): 10.8.0.2

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

**Note:** The `ip addr add` command is persistent. To remove later:
```bash
sudo ip addr del 10.8.0.1/24 dev tun0
```

## Add Routes

For the tunnel to work, you need routes so traffic can reach the other side.

**On Server (tun0):**
```bash
# Allow forwarding between tun0 and the network
# This is a basic route - adjust based on your network topology
sudo ip route add 10.8.0.0/24 dev tun0
```

**On Client (tun1):**
```bash
# Route to server network through tun1
sudo ip route add 10.8.0.0/24 dev tun1
```

## Enable IP Forwarding (Server)

For routing between networks, enable IP forwarding:

```bash
# Temporarily enable
sudo sysctl -w net.ipv4.ip_forward=1

# Make permanent (add to /etc/sysctl.conf):
# net.ipv4.ip_forward = 1
```

## Verify with ping

**From Client to Server:**
```bash
ping -I tun1 10.8.0.1
```

**From Server to Client:**
```bash
ping -I tun0 10.8.0.2
```

## Capture Packets with tcpdump

**Capture on tun0 (Server side):**
```bash
sudo tcpdump -i tun0 -n icmp
```

**Capture on tun1 (Client side):**
```bash
sudo tcpdump -i tun1 -n icmp
```

**Capture all tunnel traffic:**
```bash
sudo tcpdump -i tun0 -n not port 2222
```
This excludes the control port to see only tunnel data.

**Capture with details:**
```bash
sudo tcpdump -i tun0 -nn -vv -x -X
```

## Graceful Shutdown

Press `Ctrl+C` on both terminals. The program handles cleanup:
- Closes TUN device
- Stops all threads
- Logs statistics

## Clean Up TUN Devices

If the program crashed or you need to reset:

```bash
# Delete TUN devices
sudo ip link delete tun0 2>/dev/null
sudo ip link delete tun1 2>/dev/null

# Or delete all TUN devices
for dev in tun0 tun1 tun2 tun99; do
    sudo ip link delete $dev 2>/dev/null
done
```

## Common Errors

### "Cannot open /dev/net/tun: Permission denied"

**Cause:** Not running as root, no CAP_NET_ADMIN capability.

**Fix:**
```bash
sudo python -m src.server --config config/server.yaml --transport tcp
```

Or set capability on the Python binary (requires root):
```bash
sudo setcap 'cap_net_admin=eip' /usr/bin/python3
```

### "Device or resource busy"

**Cause:** TUN device name already in use.

**Fix:** Delete the existing device or use a different name:
```bash
sudo ip link delete tun0
```
Then restart the server.

### "Cannot bind to 0.0.0.0:2222: Address already in use"

**Cause:** Port 2222 is already occupied.

**Fix:** Kill the existing process:
```bash
sudo fuser -k 2222/tcp
# Or find and kill
ps aux | grep src.server | grep -v grep
sudo kill <pid>
```

### "Cannot set TUN device name to 'tun0': Invalid argument"

**Cause:** Incorrect ifreq structure (older code) or name too long.

**Fix:** Ensure device name is <= 15 characters.

### "TUN device already opened"

**Cause:** Trying to open an already-open TUN device.

**Fix:** Close the existing device first or use a different name.

### "No route to host"

**Cause:** Missing route, IP not configured, or device not up.

**Fix:**
```bash
# Check device status
ip link show tun0

# Check IP address
ip addr show tun0

# Add route if needed
ip route add 10.8.0.0/24 dev tun0

# Enable forwarding
sysctl -w net.ipv4.ip_forward=1
```

## Full Workflow Example

```bash
# 1. Clean any existing TUN devices
sudo ip link delete tun0 2>/dev/null
sudo ip link delete tun1 2>/dev/null

# 2. Start server (Terminal 1)
sudo python -m src.server --config config/server.yaml --transport tcp

# 3. Start client (Terminal 2)
sudo python -m src.client --config config/client.yaml --transport tcp

# 4. Configure IPs (new terminals or separate commands)
sudo ip addr add 10.8.0.1/24 dev tun0
sudo ip link set tun0 up
sudo ip addr add 10.8.0.2/24 dev tun1
sudo ip link set tun1 up

# 5. Enable IP forwarding (server side)
sudo sysctl -w net.ipv4.ip_forward=1

# 6. Verify with ping
ping -I tun1 10.8.0.1

# 7. Capture packets (optional)
sudo tcpdump -i tun0 -n icmp

# 8. Clean up when done
sudo ip link delete tun0
sudo ip link delete tun1
```

## Program Output Example

**Server:**
```
INFO - LinuxTunDevice 'tun0' opened (FD=3, MTU=1400)
INFO - TCP server listening on 0.0.0.0:2222
INFO - Waiting for TCP connection...
INFO - TCP connection accepted from ('127.0.0.1', ...)
INFO - Server core started
INFO - Tunnel listening, running...
```

**Client:**
```
INFO - LinuxTunDevice 'tun1' opened (FD=3, MTU=1400)
INFO - TCP client connected
INFO - Client core started
INFO - Tunnel established, running...
```

---

**Related:**
- [Project Status](status.md) - Current development phase
- [TCP + Mock TUN Demo](demo_tcp_mocktun.md) - Using mock TUN devices for testing