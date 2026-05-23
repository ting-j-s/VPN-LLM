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
   `connect()` raises `TransportError`.  Must be installed system-wide
   (via `sudo pip install --break-system-packages h2`) for netns/sudo
   execution; user-level `pip install --user` is insufficient.
2. **No browser emulation**: Uses bare `h2` library connection — no
   browser-like SETTINGS, WINDOW_UPDATE, or PRIORITY frame patterns.
3. **Single stream**: Currently uses only stream ID 1.  Full multiplexing
   is not implemented.
4. **No HPACK tuning**: Uses default `h2` HPACK settings.
5. **Shaping mismatch (partially resolved in Phase 10D)**: Generic
   pipeline (padding/aggregation/jitter) was designed for TCP/TLS/WebSocket.
   Phase 10D added HTTP/2-aware chunking and WINDOW_UPDATE batching, which
   reversed the Phase 10C regression.  Further tuning (multi-stream,
   SETTINGS) remains for Phase 10E.
6. **No WINDOW_UPDATE strategy (resolved in Phase 10D)**: Batched
   WINDOW_UPDATE via `http2_window_update_threshold` eliminates the
   predictable per-packet update pattern.  Tuning remaining.

## Phase 10B: HTTP/2 Matrix Integration (completed 2026-05-22)

Phase 10B added HTTP/2 to the Phase 9 trace matrix infrastructure:

1. `http2` added to `_SUPPORTED_TRANSPORTS` and `run_env_check()`
2. Env-check reports `http2_dependency` with per-module status
   (`h2_available`, `hpack_available`, `hyperframe_available`, `http2_runnable`)
3. Plan subcommand accepts `--transports http2`
4. When h2 is missing: `trace_type=dependency_missing`, `status=skipped`
5. Four netns config files created: `config/{server,client}_netns_http2{,_shaping}.yaml`
6. 44 new tests covering plan, env-check, skip semantics, config parse, patch prompts

## Phase 10C: Real Trace Execution (completed 2026-05-22)

Phase 10C installed h2 dependencies and ran the full Phase 9 trace matrix
with HTTP/2 transport:

1. Installed `h2`/`hpack`/`hyperframe` (v4.3.0) system-wide for sudo/netns
2. Fixed two blocking issues:
   - `server.py` and `client.py` argparse `--transport choices` missing `http2`
   - Data path bug: stream 1 was never opened via HEADERS, causing
     `NoSuchStreamError` on `send_data()`.  Fixed by adding HEADERS-based
     stream open, internal `asyncio.Queue` for decoupling read loop from
     `recv()`, and flow-control window management.
3. Ran idle/ping/bulk before/after real traces (all `data_quality=ok`)

### Real trace results

| Scenario | Before Pkts | After Pkts | Before Risk | After Risk | Delta | Verdict |
|---|---|---|---|---|---|---|
| idle | 14 | 24 | 0.536 | 0.610 | +0.074 | regressed |
| ping (n=3) | 580.7 | 39.3 | 0.560 | 0.636 | +0.076 | regressed |
| bulk (n=3) | 93.0 | 52.0 | 0.637 | 0.619 | -0.018 | unchanged |

### Cross-transport comparison

| Transport | ping Δrisk | ping Verdict | bulk Δrisk | bulk Verdict |
|---|---|---|---|---|
| **http2** | **+0.076** | **regressed** | **-0.018** | **unchanged** |
| tcp | insufficient | insufficient | -0.068 | improved |
| tls | insufficient | insufficient | — | — |
| websocket | -0.062 | improved | -0.056 | unchanged |

HTTP/2 ping has the highest before packet count (581 vs 249-328 for others),
indicating significant HTTP/2 frame overhead on small packets.  Shaping
reduces packet counts but does not improve fingerprint risk — the current
shaping pipeline was not designed for HTTP/2 frame patterns.

### Running HTTP/2 with Phase 9 Runner

```bash
# Install deps (must be system-wide for sudo/netns)
sudo python3 -m pip install --break-system-packages h2

# Check environment
python3 scripts/run_phase9_real_trace_matrix.py env-check

# Real execution
python3 scripts/run_phase9_real_trace_matrix.py run \
  --output-dir outputs/phase10c_http2_real \
  --transports http2 \
  --scenarios idle,ping,bulk \
  --repeat-count 3 --capture-duration 45 \
  --execute
```

## Phase 10D: HTTP/2-aware Shaping Countermeasures (completed 2026-05-23)

Phase 10D implemented two transport-internal countermeasures to address the
HTTP/2 fingerprint regression identified in Phase 10C:

1. **DATA frame size chunking**: Randomizes DATA frame payload sizes by
   splitting large sends into variable-sized chunks. Configured via
   `http2_chunk_min_size` and `http2_chunk_max_size` (default 0 = disabled).
   Uses a seeded RNG for deterministic testability.

2. **WINDOW_UPDATE batching**: Accumulates received bytes and sends
   WINDOW_UPDATE only when a threshold is reached, instead of per-packet.
   Configured via `http2_window_update_threshold` (default 0 = disabled).
   Eliminates the predictable 66/92-byte alternation pattern.

Configuration fields (in `TransportConfig`):
- `http2_chunk_min_size: int = 0`
- `http2_chunk_max_size: int = 0`
- `http2_window_update_threshold: int = 0`
- `http2_chunk_rng_seed: int | None = None`

Default 0 means disabled, preserving existing behavior. Config files:
- `config/server_netns_http2_shaping_aware.yaml`
- `config/client_netns_http2_shaping_aware.yaml`

### Phase 10D repeated trace results (min_packet_count=20)

| Scenario | Before Pkts | After Pkts | Before Risk | After Risk | Delta | Verdict |
|---|---|---|---|---|---|---|
| idle (n=3) | 27.0 | 16.0 | 0.569 | 0.558 | -0.011 | insufficient |
| ping (n=3) | 579.7 | 23.0 | 0.560 | 0.522 | **-0.038** | **unchanged** |
| bulk (n=3) | 93.0 | 28.3 | 0.629 | 0.479 | **-0.150** | **improved** |

### Phase 10C vs 10D comparison

| Scenario | Phase 10C Delta | Phase 10C Verdict | Phase 10D Delta | Phase 10D Verdict |
|---|---|---|---|---|
| idle | +0.074 | regressed | -0.011 | insufficient |
| ping | +0.076 | regressed | **-0.038** | unchanged |
| bulk | -0.018 | unchanged | **-0.150** | **improved** |

Key achievement: all three scenarios are now below the 0.60 risk threshold.
The generic shaping regression (ping +0.076, idle +0.074) was fully reversed.
WINDOW_UPDATE batching eliminates the predictable 66/92-byte control frame
alternation that dominated Phase 10C after-traces.

### Remaining for Phase 10E

1. **Multi-stream strategy**: Distribute tunnel data across multiple streams
2. **SETTINGS randomization**: Tune initial SETTINGS to avoid predictable fingerprint
3. **HPACK table manipulation**: Dynamic table churn for header compression patterns
4. **WINDOW_UPDATE strategy tuning**: Further tune batching thresholds

## Implementation notes

- Follows the same background event-loop pattern as `WebSocketTransport`
  (dedicated `asyncio` loop per instance, sync methods submit coroutines
  via `run_coroutine_threadsafe`).
- Threading model: daemon thread per transport instance.
- Wire format: HTTP/2 DATA frames on stream 1.
- `close()` is idempotent.
- Marked `experimental: true` in config.
