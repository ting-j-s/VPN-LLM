# HTTP/2 Transport (experimental)

## Status

**Experimental skeleton.**  The `h2` Python library is an optional dependency.
When `h2` is not installed, the transport class is importable and
factory-constructable, but `connect()` raises a clear `TransportError`.

## What it is

HTTP2Transport is a **carrier-layer transport** for the VPN tunnel.  It
replaces the TCP/TLS/WebSocket framing with HTTP/2 streams over a single
TCP connection.

It is **not** an undetectability claim.  HTTP/2 has its own wire-level
signatures (SETTINGS frames, stream multiplexing patterns, HPACK header
compression) that can be fingerprinted.  Using HTTP/2 changes the
observable fingerprint — it does not erase it.

## Relationship to traffic shaping

HTTP/2 transport sits below the shaping layer:

```
    VPN tunnel data
         |
    Shaping (aggregation / padding / jitter)   ← optional
         |
    HTTP/2 framing (h2 connection + streams)   ← this transport
         |
    TCP socket
```

When shaping is enabled, the tunnel bytes are shaped **before** HTTP/2
framing.  The shaping layer sees the same raw tunnel bytes regardless
of transport type.

## Fingerprint considerations

| Detection surface | HTTP/2 impact |
|---|---|
| Encapsulated TLS | Not applicable (no inner TLS) |
| 3-gram / n-gram | New patterns from HTTP/2 frame types |
| Burst profile | Multiplexing may smooth bursts |
| Direction ratio | Similar to TCP (bidirectional stream) |
| Timing | HPACK dynamic table may add variable latency |

## Configuration

### Client (config/examples/http2_client.yaml)

```yaml
client:
  tun_name: tun0
  tun_ip: 10.8.0.2
  tun_peer: 10.8.0.1
  mtu: 1400

server:
  host: 127.0.0.1
  port: 2225

transport:
  type: http2
  path: /
  server_hostname: 127.0.0.1
  experimental: true

session:
  heartbeat_interval: 10
  reconnect: true
  reconnect_interval: 3
```

### Server (config/examples/http2_server.yaml)

```yaml
server:
  tun_name: tun0
  tun_ip: 10.8.0.1
  tun_peer: 10.8.0.2
  mtu: 1400
  listen_port: 2225

forwarding:
  enable_nat: false
  enable_route: true

transport:
  type: http2
  path: /
  experimental: true

session:
  heartbeat_timeout: 30
```

## Current limitations

1. **Optional dependency**: Requires `pip install h2`.  Without it,
   `connect()` raises `TransportError`.
2. **No browser emulation**: Uses bare `h2` library connection — no
   browser-like SETTINGS, WINDOW_UPDATE, or PRIORITY frame patterns.
3. **Single stream**: Currently uses only stream ID 1.  Full multiplexing
   is not implemented.
4. **No HPACK tuning**: Uses default `h2` HPACK settings.
5. **Server accept not integration-tested**: The server accept path
   compiles and passes unit tests, but has not been verified with
   real TUN/netns traffic.
6. **No Phase 9 trace data**: Not yet included in the before/after
   real trace matrix.

## Phase 10B: Real Trace Evaluation

Phase 10B will add HTTP/2 to the Phase 9 trace matrix:

1. Create example configs for HTTP/2 client and server
2. Add http2 to the supported transports list in
   `run_phase9_real_trace_matrix.py`
3. Run before/after traces for idle, ping, and bulk scenarios
4. Compare HTTP/2 fingerprint characteristics against tcp, tls, and
   websocket baselines

## Implementation notes

- Follows the same background event-loop pattern as `WebSocketTransport`
  (dedicated `asyncio` loop per instance, sync methods submit coroutines
  via `run_coroutine_threadsafe`).
- Threading model: daemon thread per transport instance.
- Wire format: HTTP/2 DATA frames on stream 1.
- `close()` is idempotent.
- Marked `experimental: true` in config.
