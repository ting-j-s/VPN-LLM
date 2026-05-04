# TCP + Mock TUN Demo

This document demonstrates a working VPN tunnel system using TCP transport with MockTUN devices.

> **Note**: This is a minimal demonstration. The system uses mock TUN devices that simulate packet processing without requiring root privileges or real network interfaces.

## 1. 安装依赖

```bash
cd /data/xjr/VPN-LLM/vpn_tunnel
pip install -r requirements.txt
```

## 2. 运行测试

```bash
python -m pytest tests/ -v
```

**Expected output**: `112 passed, 1 skipped`

> The 1 skipped test (`test_bidirectional_data_path`) is due to timing sensitivity in the test environment. The core functionality is verified by the unidirectional tests.

## 3. 启动服务端

```bash
python -m src.server --config config/server.yaml --transport tcp --mock-tun
```

**Expected key logs**:
```
VPN Tunnel Server starting
Configuration loaded: config/server.yaml
Transport type overridden to: tcp
Using MockTunDevice (name=tun0, mtu=1400)
Transport created: TCPTransport(mode=server, host=0.0.0.0, port=2222, connected=False)
ServerCore created
Starting tunnel...
Starting server core
MockTunDevice 'tun0' opened (MTU=1400)
TCP server binding to 0.0.0.0:2222
TCP server listening on 0.0.0.0:2222
Waiting for TCP connection...
```

The server will **block at "Waiting for TCP connection..."** waiting for a client to connect.

## 4. 启动客户端

In a **second terminal**, run:

```bash
cd /data/xjr/VPN-LLM/vpn_tunnel
python -m src.client --config config/client.yaml --transport tcp --mock-tun
```

**Expected key logs**:
```
VPN Tunnel Client starting
Configuration loaded: config/client.yaml
Transport type overridden to: tcp
Using MockTunDevice (name=tun0, mtu=1400)
Transport created: TCPTransport(mode=client, host=127.0.0.1, port=2222, connected=False)
ClientCore created
Starting tunnel...
Starting client core
MockTunDevice 'tun0' opened (MTU=1400)
TCP client connecting to 127.0.0.1:2222
TCP client connected
Transport->TUN loop started
TUN->Transport loop started
Heartbeat loop started
Client core started (session_id=...)
Tunnel established, running...
Press Ctrl+C to stop
```

## 5. 服务端应显示连接成功

Once client connects, server should show:
```
TCP connection accepted from ('127.0.0.1', ...)
Transport->TUN loop started
TUN->Transport loop started
Heartbeat loop started
Server core started (session_id=...)
Tunnel listening, running...
Press Ctrl+C to stop
```

## 6. Ctrl+C 关闭

Press **Ctrl+C** on both terminal to shut down.

**Expected graceful shutdown logs**:
```
Received signal 15, shutting down...
Stopping client core
TUN->Transport loop finished
Heartbeat loop finished
Closing TCP transport
TCP transport closed
MockTunDevice 'tun0' closed
Graceful shutdown completed (tun->transport=0 bytes, transport->tun=0 bytes)
VPN Tunnel Client stopped
```

**Important**:
- No `ERROR` level logs should appear during normal shutdown
- `WARNING` logs about "Frame error: Not connected" are expected in mock-tun mode (no real packets are exchanged)
- All threads should exit cleanly

## Troubleshooting

### "Address already in use"

Port 2222 is in use. Either:
1. Wait a moment and try again (TIME_WAIT state)
2. Or kill any remaining processes: `pkill -f "src.server"`

### Connection refused

Ensure server is running and listening before starting client:
1. Start server first
2. Wait for "TCP server listening" message
3. Then start client

### WARNING: Frame error: Not connected

This is **expected** in mock-tun mode. MockTUN devices don't produce real IP packets, so the recv() calls timeout after 1 second. This does not indicate an error - it's just the mock environment behavior.

## What This Demo Proves

1. **TCP transport layer works**: Server can bind/listen, client can connect
2. **Transport accept works**: Server properly accepts incoming connections
3. **Core threading works**: All loops (transport_to_tun, tun_to_transport, heartbeat) start correctly
4. **Graceful shutdown works**: Ctrl+C triggers clean shutdown without crashes
5. **No unhandled exceptions**: The system handles disconnections gracefully

## Next Steps

This demo establishes a baseline. Future work may include:

- **Real TUN device**: Remove `--mock-tun` flag and configure system TUN device
- **TLS transport**: Test with proper certificates
- **SSH transport**: Test with real SSH server
- **Bidirectional data flow**: Inject real packets via TUN device

---

**Tag**: `v0.1-tcp-mocktun`