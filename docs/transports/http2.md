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

## Phase 10B: HTTP/2 Matrix Integration (completed 2026-05-22)

Phase 10B added HTTP/2 to the Phase 9 trace matrix infrastructure:

1. `http2` added to `_SUPPORTED_TRANSPORTS` and `run_env_check()`
2. Env-check reports `http2_dependency` with per-module status
   (`h2_available`, `hpack_available`, `hyperframe_available`, `http2_runnable`)
3. Plan subcommand accepts `--transports http2`
4. When h2 is missing: `trace_type=dependency_missing`, `status=skipped`
5. Four netns config files created: `config/{server,client}_netns_http2{,_shaping}.yaml`
6. 44 new tests covering plan, env-check, skip semantics, config parse, patch prompts

## Phase 10C: Real Trace Execution (planned)

1. Install `h2` (`pip install h2`)
2. Run before/after traces for idle, ping, and bulk scenarios
3. Compare HTTP/2 fingerprint characteristics against tcp, tls, and
   websocket baselines

### Running HTTP/2 with Phase 9 Runner

When h2 is installed:

```bash
# Check environment
python3 scripts/run_phase9_real_trace_matrix.py env-check

# Plan matrix
python3 scripts/run_phase9_real_trace_matrix.py plan \
  --output-dir outputs/phase10c_http2_real \
  --transports http2 \
  --scenarios idle,ping,bulk

# Real execution
python3 scripts/run_phase9_real_trace_matrix.py run \
  --output-dir outputs/phase10c_http2_real \
  --transports http2 \
  --scenarios idle,ping,bulk \
  --repeat-count 3 --capture-duration 45 \
  --execute
```

When h2 is missing, the runner produces `trace_type=dependency_missing`
without attempting netns setup or TCP connections. All other transports
continue to work normally.

## Implementation notes

- Follows the same background event-loop pattern as `WebSocketTransport`
  (dedicated `asyncio` loop per instance, sync methods submit coroutines
  via `run_coroutine_threadsafe`).
- Threading model: daemon thread per transport instance.
- Wire format: HTTP/2 DATA frames on stream 1.
- `close()` is idempotent.
- Marked `experimental: true` in config.
