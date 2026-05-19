# Phase 5: Traffic Shaping Layer

## 1. Purpose

Phase 5 introduces a configurable traffic shaping layer (`src/shaping/`) that
implements countermeasures against fingerprint detection metrics identified by
the Phase 4 detection-adversarial patch loop.

The shaper is designed to be **driven by LLM-generated patches** — when the
detection gate fails, the countermeasure policy maps failed metrics to shaping
strategy recommendations, which the LLM can implement by creating or modifying
shaper configuration and code.

## 2. Relationship to Detection Papers

| Paper | Metrics Targeted | Shaping Strategy |
|---|---|---|
| 1OpenVPN Fingerprint | `repeated_length_ratio`, `small_packet_ratio` | Padding, Aggregation |
| 2Encapsulated TLS | `dominant_ngram_ratio`, `ngram_entropy`, `max_burst_size` | Fragmentation (skeleton), Padding |
| 3CalcuLatency | `rtt_diff_ms` | Jitter (skeleton, metadata only) |
| 4Cross-layer RTT | `avg_inter_arrival_ms`, `rtt_diff_ms` | Jitter (skeleton) |

## 3. Metrics That Shaping Can Mitigate

### Direct mitigation

- **repeated_length_ratio** — Random padding varies frame sizes, breaking uniform-size patterns.
- **small_packet_ratio** — Aggregation combines small frames into larger transport writes.
- **dominant_ngram_ratio** — Padding and fragmentation disrupt stable ngram distributions.

### Indirect / future mitigation

- **ngram_entropy** — Fragmentation + padding increase payload entropy.
- **max_burst_size** — Fragmentation splits large bursts into smaller chunks.
- **inter-arrival regularity** — Jitter metadata provides delay hints for future scheduler.

## 4. Current Implementation

### 4.1 NoopTrafficShaper (`base.py`)

Default shaper. One frame in → one chunk out, no modification.

```python
from src.shaping import NoopTrafficShaper
shaper = NoopTrafficShaper()
chunks = shaper.encode_frame(frame_bytes)
# chunks == [ShapedChunk(data=frame_bytes)]
```

### 4.2 Padding (`padding.py`)

Wraps frames in a reversible envelope with random padding bytes.

Envelope: `MAGIC(4) + original_len(4) + padding_len(2) + payload + random_bytes`

- Encode: adds VPAD header + random padding
- Decode: strips envelope, returns original frame
- Magic: `VPAD` (avoids collision with VTUN frame magic)
- Fully reversible for any frame size

### 4.3 Aggregation (`aggregation.py`)

Buffers multiple small frames and emits a single combined chunk on flush.

Envelope: `MAGIC(4) + frame_count(2) + [len_i(4)]*N + frame_1 + ... + frame_N`

- Encode: buffers frames; auto-flush when `aggregation_max_bytes` reached
- Decode: splits envelope back into individual frames
- Magic: `VAGG`
- Single frames emitted without envelope after flush

### 4.4 Fragmentation (`fragmentation.py`) — SKELETON

Splits large frames into variable-size fragment envelope chunks.

Envelope: `MAGIC(4) + frag_id(2) + frag_count(2) + original_len(4) + fragment_data`

- `split_frame()` / `reassemble_fragments()` are standalone pure functions
- `fragments_to_chunks()` / `chunks_to_fragments()` convert to/from ShapedChunk
- NOT in the default pipeline — cross-chunk reassembly needs scheduling support
- Magic: `VFRG`

### 4.5 Jitter (`jitter.py`) — SKELETON

Annotates ShapedChunk with random delay metadata. No real sleep.

- `delay_ms` set randomly in `[jitter_min_ms, jitter_max_ms]`
- Requires a future scheduler to execute actual delays
- Deterministic with seeded RNG

### 4.6 Factory (`factory.py`)

Assembles strategies from `ShapingConfig` into a `PipelineTrafficShaper`.

Pipeline order: `aggregation → padding → jitter`

```python
from src.shaping import ShapingConfig, create_traffic_shaper

config = ShapingConfig(
    enabled=True,
    padding_enabled=True,
    min_padding_bytes=8,
    max_padding_bytes=64,
)
shaper = create_traffic_shaper(config, seed=42)
```

## 5. Default: Disabled

All strategies are **disabled by default**. `ShapingConfig.enabled` defaults to `False`,
and `create_traffic_shaper()` returns `NoopTrafficShaper` unless `enabled=True` and
at least one strategy is configured with a positive parameter.

## 6. Configuration Example

```python
from src.shaping import ShapingConfig, create_traffic_shaper

config = ShapingConfig(
    enabled=True,
    # Reduce repeated_length_ratio
    padding_enabled=True,
    min_padding_bytes=8,
    max_padding_bytes=128,
    # Reduce small_packet_ratio
    aggregation_enabled=True,
    aggregation_max_delay_ms=50.0,
    aggregation_max_bytes=4096,
    # Skeleton only — metadata, no sleep
    jitter_enabled=True,
    jitter_min_ms=5.0,
    jitter_max_ms=50.0,
)
shaper = create_traffic_shaper(config, seed=42)

# Use shaper:
for frame in outgoing_frames:
    for chunk in shaper.encode_frame(frame):
        transport.send(chunk.data)

# Flush buffered frames:
for chunk in shaper.flush():
    transport.send(chunk.data)
```

## 7. Testing Encode/Decode Reversibility

All shaping strategies are tested for full reversibility:

```bash
python3 -m pytest \
  tests/test_traffic_shaper_padding.py \
  tests/test_traffic_shaper_aggregation.py \
  tests/test_traffic_shaper_fragmentation.py \
  tests/test_traffic_shaper_factory.py \
  -v
```

Key invariants tested:
- Padding: `decode(encode(frame)) == frame` (30 random frames)
- Aggregation: multiple frames buffered, flushed, decoded → original frames
- Fragmentation: `reassemble(split(frame)) == frame` for varied sizes
- Pipeline: `aggregation → padding` roundtrip preserves all frames

## 8. Before/After Comparison

To measure shaping effectiveness, use Phase 3.5 trace capture tools:

```bash
# Before (baseline):
python3 scripts/trace_capture.py --transport tcp --scenario idle \
  --duration 30 --output traces/tcp/idle

# Apply shaping config, then:
python3 scripts/trace_capture.py --transport tcp --scenario idle \
  --duration 30 --output traces/tcp/idle_shaped

# Compare:
python3 -m src.llm.detection.patch_loop \
  --report traces/tcp/idle.report.json \
  --report traces/tcp/idle_shaped.report.json \
  ...
```

## 9. Current Limitations

1. **Not integrated into core send/recv** — Shaper is a standalone module. ClientCore and
   ServerCore do not call it. Integration is deferred to Phase 5B to keep this commit
   focused on the shaping logic itself.

2. **No real sleep for jitter** — JitterShaper only sets `ShapedChunk.delay_ms` metadata.
   A future scheduler (Phase 5B+) must execute the actual delays.

3. **Fragmentation not in default pipeline** — `split_frame()` and `reassemble_fragments()`
   are pure functions; the cross-chunk decode requires scheduler coordination.

4. **No dummy traffic** — `dummy_enabled` field is reserved in config but not implemented.

5. **No real-time RTT-aware pacing** — Jitter is purely random, not based on measured RTT.

6. **Requires actual TUN/netns traffic capture** to validate against real DPI detectors.

7. **Does NOT claim real undetectability** — This is a local controlled-experiment tool.

## 10. Phase 5B: Core Integration (COMPLETE)

### 10.1 Core insertion points

ClientCore and ServerCore accept an optional `traffic_shaper` parameter:

```python
from src.shaping import PaddingShaper

shaper = PaddingShaper(min_padding_bytes=8, max_padding_bytes=64)
client = ClientCore(tun=tun, transport=transport, traffic_shaper=shaper)
```

Default: `None` → `NoopTrafficShaper()` — zero behavioral change.

### 10.2 Send pipeline

`_send_shaped(encoded_frame)` wraps all transport sends:

```
encoded_frame → shaper.encode_frame() → [ShapedChunk, ...]
  → for each chunk: transport.send(chunk.data)
```

Applied to: AUTH, DATA, HEARTBEAT (all frame types).

If `encode_frame()` returns empty (frame buffered for aggregation),
`flush()` is called immediately to avoid silent drops.

### 10.3 Recv pipeline

`_transport_to_tun_loop` decodes through the shaper:

```
raw_bytes → shaper.decode_chunk() → [encoded_frame, ...]
  → for each: decode_frame(encoded) → _handle_frame(frame)
```

Supports multi-frame decode (decode_chunk returning N frames). If
decode fails, falls back to raw data to ensure loop resilience.

### 10.4 What is NOT connected in Phase 5B

| Component | Status | Reason |
|---|---|---|
| NoopTrafficShaper | Connected | Default, zero impact |
| PaddingShaper | Connected | Fully reversible, no scheduler needed |
| AggregationShaper | NOT connected to core | Requires scheduler with flush policy (Phase 5C) |
| Fragmentation | NOT connected to core | Requires cross-chunk reassembly (Phase 5C+) |
| JitterShaper delay_ms | Metadata only | No real sleep; scheduler needed (Phase 5C) |
| YAML config | NOT connected | Constructor injection only (Phase 5C) |
| Dummy traffic | NOT implemented | Reserved config field |

### 10.5 Heartbeat / control frame safety

All frame types (AUTH, DATA, HEARTBEAT, CLOSE) go through the same
`_send_shaped()` helper. With Noop or Padding, each frame is sent
individually — no buffering, no caching. `_send_shaped` includes a
flush safeguard: if encode_frame returns empty (aggregation case),
flush() is called immediately to prevent silent drops.

### 10.6 Tests

New test file: `tests/test_core_traffic_shaping.py` (15 tests):
- Noop default behavior (ClientCore, ServerCore, old constructor)
- Padding integration (send sees VPAD, recv roundtrip, ServerCore)
- Multi-frame decode handling
- Empty-encode flush safeguard
- Jitter metadata no-sleep verification
- Heartbeat through Noop/Padding/Aggregation paths
- Shaper decode error resilience (fallback to raw data)
- Factory-created shaper with core

### 10.7 Current limitations (Phase 5B)

1. No YAML configuration — shaper must be injected via constructor.
2. Jitter delay_ms is logged but not slept.
3. Aggregation not in core path (flush policy needs scheduler).
4. Fragmentation not in core path (cross-chunk reassembly).
5. No dummy traffic.
6. No real-time RTT-aware pacing.

## 11. Phase 5C: Scheduler, Aggregation, YAML Config (COMPLETE)

### 11.1 SendScheduler (`src/shaping/scheduler.py`)

Lightweight coordinator that applies frame-type-aware flush policy:

- **DATA frames** may be buffered by aggregation-enabled shapers.
- **Control frames** (HEARTBEAT, AUTH, CLOSE) trigger a pre-flush of buffered
  DATA before themselves, and are themselves flushed immediately — control
  messages are never delayed by aggregation.
- No real sleep for jitter (metadata only).
- Stateless — buffer state lives in the underlying shaper.

### 11.2 Frame-type-aware `_send_shaped`

`ClientCore._send_shaped` and `ServerCore._send_shaped` now accept
`frame_type: FrameType` (default `FrameType.DATA`):

```
_send_shaped(encoded_frame, frame_type=FrameType.HEARTBEAT)
```

Control-frame path:
1. `traffic_shaper.flush()` — emit buffered DATA
2. `traffic_shaper.encode_frame(control)` — encode control frame
3. If empty (buffered by aggregation), `flush()` to emit immediately

DATA path:
1. `traffic_shaper.encode_frame(data)` — may buffer (aggregation)

### 11.3 Aggregation flush policy

| Trigger | Behavior |
|---|---|
| `aggregation_max_bytes` reached | Auto-flush in `AggregationShaper.encode_frame()` |
| Control frame (HEARTBEAT/AUTH/CLOSE) | Pre-flush before encode + post-flush if buffered |
| `core.stop()` | Flush shaper buffers before closing transport |
| `shaper.close()` | Flush (passthrough to `_do_flush`) |

### 11.4 YAML config integration

New optional `shaping:` section in client/server YAML:

```yaml
shaping:
  enabled: false          # default: all shaping disabled
  padding_enabled: false
  min_padding_bytes: 0
  max_padding_bytes: 0
  aggregation_enabled: false
  aggregation_max_delay_ms: 0
  aggregation_max_bytes: 0
  fragmentation_enabled: false
  fragmentation_min_size: 0
  fragmentation_max_chunk_size: 0
  jitter_enabled: false
  jitter_min_ms: 0
  jitter_max_ms: 0
  dummy_enabled: false
```

- Old configs missing `shaping:` load with defaults (all disabled).
- `ShapingConfig` added to `ClientConfig` and `ServerConfig` dataclasses.
- `create_traffic_shaper(config.shaping)` called in `client.py` / `server.py`.
- Example configs in `config/examples/`.

### 11.5 Shaper stop-time flush

`ClientCore.stop()` and `ServerCore.stop()` now flush the traffic shaper
before closing the transport. This ensures buffered aggregation data is
emitted before the transport is torn down.

### 11.6 Synthetic before/after trace comparison

Real trace capture requires root/netns. A synthetic comparison using the
real shaper code shows:

| Metric | Noop (before) | Padding (8-32B) | Padding + Aggregation |
|---|---|---|---|
| `packet_count` | 100 | 100 | **7** (-93%) |
| `repeated_length_ratio` | 0.04 | 0.04 | 0.14 |
| `small_packet_ratio` | 0.60 | **0.48** (-20%) | **0.00** (-100%) |
| `ngram_entropy` | 2.74 | 3.02 (+10%) | — |
| `fingerprint_risk_score` | 0.39 | 0.32 (-0.07) | 0.35 (-0.04) |

Generated by `scripts/synthetic_shaping_comparison.py`. **Marked SYNTHETIC**
— not from real network capture.

### 11.7 Tests added

- `tests/test_traffic_shaper_scheduler.py` — 21 tests (SendScheduler + aggregation flush policy)
- `tests/test_config_shaping.py` — 13 tests (YAML config loading, old-config compatibility, validation)
- `tests/test_core_traffic_shaping.py` — extended with 6 aggregation integration tests
- Total: 1095 passed, 6 skipped (+41 vs Phase 5B baseline of 1054)

## 12. Future Plans (Phase 5D+)

1. **Phase 5D: Dummy traffic + real jitter scheduler**
   - Configurable interval dummy frames
   - Optional send scheduler thread that sleeps jitter delay_ms
   - Must not slow unit tests (default disabled)

2. **Phase 6: Active probe resistance**
   - Unified timeout, silent drop, constant close policy for probe response variance

3. **RTT gate integration** — Feed RTT measurements back to jitter parameters.

4. **Fragmentation core integration** — Cross-chunk reassembly with scheduler coordination.
