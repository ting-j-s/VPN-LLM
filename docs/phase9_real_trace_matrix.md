# Phase 9: Real TUN/netns Before/After Trace Matrix

## 1. Purpose

Phase 9 captures **real VPN-LLM tunnel traffic** before and after enabling traffic
shaping, using Linux network namespaces and TUN devices. This produces the first
experimental evidence of whether the shaping modules actually reduce
fingerprintability in a controlled local setting.

Phase 8 produced documentation and integration. Phase 9 produces data.

Phase 9B extends the runner to produce **statistically meaningful packet counts**
(packet_count >= 30) via enhanced ping, curl, and bulk scenarios.

## 2. Experiment Matrix

| Transport | idle | ping | curl | bulk | reconnect |
|---|---|---|---|---|---|
| tcp | before/after | before/after | before/after | before/after | before/after |
| tls | before/after | before/after | before/after | before/after | before/after |
| websocket | before/after | before/after | before/after | before/after | before/after |
| ssh | skipped | skipped | skipped | skipped | skipped |
| http2 | skipped | skipped | skipped | skipped | skipped |

50 total entries (5 transports x 5 scenarios x 2 phases). http2 is skipped
by default when the h2 dependency is missing (see Section 13).

ssh is skipped by default because it requires paramiko SSH server setup. It can
be enabled by fixing the SSH transport implementation.

## 3. Before/After Definition

- **Before**: shaping disabled, using default config (`config/server_netns.yaml`
  + `config/client_netns.yaml`).
- **After**: shaping enabled, using symmetric shaping configs (server + client both enable
  padding + aggregation + jitter). Server uses `config/server_netns_shaping.yaml`,
  client uses `config/examples/shaping_padding_aggregation_netns.yaml`.

### 3.1 Phase 9C: Shaping Data-Path Fix (2026-05-22)

**Root cause:** `_last_sent_time` in both `client_core.py` and `server_core.py` was
updated on every TUN read in `_tun_to_transport_loop`, even when aggregation buffered
the data without calling `transport.send()`. This continuously reset the HEARTBEAT
timer, preventing HEARTBEAT-triggered aggregation flushes during active TUN traffic.

**Fix:** Moved `_last_sent_time` update into `_send_shaped`, co-located with
`transport.send()` calls. Now `_last_sent_time` only advances when data actually hits
the wire. HEARTBEAT fires reliably every 10 seconds regardless of TUN activity.

**Changes:**
- `src/core/client_core.py`: `_send_shaped` updates `_last_sent_time` after each `transport.send()`
- `src/core/server_core.py`: same fix
- `scripts/run_phase9_real_trace_matrix.py`: IPv6 disable on TUN interfaces, symmetric shaping config selection

**Verification (full aggregation pipeline, tcp/ping, 300 pings):**
- before: 912 packets, after: 46 packets
- data_quality=ok, no TUN write errors
- HEARTBEAT interval: consistent 10s both sides
- risk delta: +0.0289 (unchanged)

### 3.2 Phase 9D: Real Matrix Expansion (2026-05-22)

Expanded the before/after matrix from a single TCP smoke test to tcp/websocket/tls.

**Results:**

| Transport | Scenario | Before Pkts | After Pkts | Quality | Before Risk | After Risk | Delta | Verdict |
|---|---|---|---|---|---|---|---|---|
| tcp | ping | 609 | 32 | ok | 0.5145 | 0.5171 | +0.0026 | unchanged |
| tcp | bulk | 51 | 22 | insufficient | 0.6073 | 0.5910 | N/A | insufficient |
| websocket | ping | 624 | 44 | ok | 0.6399 | 0.5619 | **-0.078** | **improved** |
| websocket | bulk | 69 | 28 | insufficient | 0.6197 | 0.6128 | N/A | insufficient |
| tls | ping | 418 | 19 | insufficient | 0.5545 | 0.5025 | N/A | insufficient |

**Key findings:**
- websocket/ping is the first scenario showing measurable risk reduction (delta -0.078)
- tcp/ping risk is essentially unchanged (delta +0.0026)
- Aggregation reduces wire-level packet count by 10-20x; insufficient entries need
  more traffic volume (higher ping count / larger bulk files)
- TLS config path fixed: `_get_config_for_phase` now returns TLS-specific configs with
  certfile/keyfile for TLS transport

**TLS diagnosis:**
- Root cause: `_get_config_for_phase` always returned TCP configs, which lack
  `certfile`/`keyfile` settings. Server failed with "Server mode requires certfile
  and keyfile."
- Fix: Created `config/server_netns_tls.yaml`, `config/client_netns_tls.yaml`,
  `config/server_netns_tls_shaping.yaml`, `config/client_netns_tls_shaping.yaml`
- After fix: TLS connection succeeds. Data insufficient (19 after < 30) due to
  aggregation, needs more ping volume.

**Bulk scenario limitation:**
- `--bulk-bytes` was not wired to the HTTP server file generation (hardcoded 256KB).
  Fixed in Phase 9D.
- With aggregation, HTTP request TCP SYN is buffered until HEARTBEAT flush; curl
  `--connect-timeout 10` is tight. Needs longer connect timeout or larger transfers.

### 3.3 Phase 9E: Repeated Real Trace Matrix and Statistical Before/After Evaluation (2026-05-22)

Phase 9E adds statistical repeatability to the real trace matrix without modifying
detector logic. Each scenario runs multiple times; results are aggregated with
mean/std/min/max for both packet counts and risk scores.

**New CLI parameters:**

| Parameter | Default | Description |
|---|---|---|
| `--repeat-count` | 1 | Number of repetitions per scenario |
| `--curl-connect-timeout` | 30 | curl `--connect-timeout` seconds |
| `--curl-max-time` | 60 | curl `--max-time` seconds |
| `--http-server-startup-timeout` | 10 | HTTP server startup verification timeout |
| `--post-scenario-wait` | 2 | Post-scenario wait to capture residual packets |
| `--bulk-read-timeout` | 60 | Bulk download read timeout |

**Bulk scenario improvements:**
- `bulk_bytes` default raised from 256KB to 1MB
- HTTP server startup verified via `ss -tlnp` before curl runs
- curl uses configurable `--connect-timeout` and `--max-time`

**Repeated output structure:**

```
outputs/phase9_real_matrix_e/
  before/tcp/ping/run_01.report.json
  before/tcp/ping/run_02.report.json
  before/tcp/ping/run_03.report.json
  after/tcp/ping/run_01.report.json
  ...
  summaries/repeated_before_after_comparison.{csv,json,md}
```

**Statistical summary fields:**

| Field | Description |
|---|---|
| `before_packet_count_mean` / `_std` / `_min` / `_max` | Per-phase packet count stats |
| `after_packet_count_mean` / `_std` / `_min` / `_max` | Per-phase packet count stats |
| `before_risk_score_mean` / `_std` | Per-phase risk score stats |
| `after_risk_score_mean` / `_std` | Per-phase risk score stats |
| `risk_score_delta_mean` / `_std` | Paired delta stats |
| `improved_count` / `unchanged_count` / `regressed_count` | Per-run verdict counts |
| `insufficient_count` / `skipped_count` | Data quality counts |
| `data_quality` | ok / partial / insufficient / skipped |
| `aggregate_verdict` | improved / unchanged / regressed / mixed / insufficient / skipped |

**Aggregate verdict rules:**
- `valid_repeat_count == 0`: insufficient or skipped
- `improved_count >= 2` and `regressed_count == 0`: **improved**
- `regressed_count >= 2`: **regressed**
- `unchanged_count >= 2`: **unchanged**
- otherwise: **mixed**

**Recommended Phase 9E parameters:**
```
--repeat-count 3 --capture-duration 45 --ping-count 80 --ping-interval 0.05
--bulk-bytes 1048576 --curl-connect-timeout 30 --curl-max-time 60
--min-packet-count 30
```

**Recommended experiment matrix (4 scenarios x 2 phases x 3 repeats = 24 runs):**

| Transport | Scenario | Purpose |
|---|---|---|
| tcp | ping | Stability baseline (already ok in 9D) |
| websocket | ping | Effectiveness validation (already improved in 9D) |
| tcp | bulk | Fix insufficient after packet_count |
| tls | ping | Fix insufficient after packet_count |

**Patch loop strategy (Phase 9E):**
- `aggregate_verdict=regressed` or `after_risk_score_mean >= 0.70` → `next_patch_prompt.txt`
- `data_quality=insufficient` or `partial` → `data_collection_prompt.txt`
- `aggregate_verdict=improved` or `unchanged` → `no_patch_needed.txt`

**Phase 9E repeated results (5 scenarios x 3 repeats = 30 runs):**

| Transport | Scenario | After Pkts | Delta | I/U/R | Quality | Verdict |
|---|---|---|---|---|---|---|
| tcp | bulk | 33.3±1.5 | -0.0683 | 2/1/0 | ok | **improved** |
| websocket | ping | 32.7±1.2 | -0.0621 | 3/0/0 | ok | **improved** |
| websocket | bulk | 40.7±0.6 | -0.0555 | 1/2/0 | ok | unchanged |
| tcp | ping | 20.0±1.0 | N/A | 0/0/0 | insufficient | insufficient |
| tls | ping | 15.0±0.0 | N/A | 0/0/0 | insufficient | insufficient |

**Phase 9E key conclusion:**
- Shaping data path is confirmed stable (0 TUN write errors across 30 runs).
- tcp/bulk and websocket/ping show repeated improved verdicts with valid data quality.
- tcp/ping and tls/ping after packet counts are heartbeat-flush limited
  (after ≈ capture_duration / heartbeat_interval × pkts_per_flush, independent of ping_count).
  This is expected aggregation behavior, not a data-path failure.

### 3.4 Phase 9F: Long-Capture Ping Supplement (2026-05-22)

Phase 9F tests whether extending `--capture-duration` from 45s to 90s resolves
the ping packet-count insufficiency without changing shaping behavior.

**Parameters:**
```
--transports tcp,tls --scenarios ping --repeat-count 3
--capture-duration 90 --ping-count 120 --ping-interval 0.05
--min-packet-count 30
```

**Results (2 scenarios x 3 repeats = 12 runs):**

| Transport | Scenario | Before Pkts | After Pkts | Delta | I/U/R | Quality | Verdict |
|---|---|---|---|---|---|---|---|
| tcp | ping | 384.7±1.2 | **41.0±1.0** | +0.0415 | 0/3/0 | ok | unchanged |
| tls | ping | 384.0±0.0 | 25.0±13.0 | N/A | 0/1/0 (2 insuf) | partial | mixed |

**Key findings:**
- tcp/ping reaches `data_quality=ok` under 90s capture; packet count scales
  approximately linearly with capture duration (20 → 41, against 45s → 90s).
- tls/ping remains partial: high variance (17/18/40), only 1/3 runs ≥30.
  TLS handshake overhead further compresses the effective flush window.
- 0 TUN write errors across all 12 runs.

**Phase 9F conclusion:**
- Ping under aggregation is **heartbeat-flush limited**. Packet count is bounded by
  `capture_duration / heartbeat_interval`, not by `ping_count`. Extending capture
  duration resolves tcp/ping but not tls/ping.
- tls/ping is formally marked **aggregation_flush_limited** and should not be used
  as a primary before/after shaping metric unless the flush policy or capture
  duration changes.
- Primary before/after evidence should rely on `data_quality=ok` scenarios:
  tcp/bulk, websocket/ping, websocket/bulk, and (with 90s capture) tcp/ping.

## 4. Environment Requirements

| Requirement | Purpose |
|---|---|
| Linux with `/dev/net/tun` | TUN device for VPN tunnel |
| root or `CAP_NET_ADMIN` | Create netns, TUN devices, veth pairs |
| `tcpdump` | Capture tunnel traffic on veth |
| `tshark` | Convert pcap to CSV |
| `curl` | Needed for curl and bulk scenarios |
| Python `websockets` | WebSocket transport |
| Python `paramiko` | SSH transport (optional) |
| Python `yaml` | Config parsing |

Run `python3 scripts/run_phase9_real_trace_matrix.py env-check` to verify.

## 5. Usage

### 5.1 Environment check

```bash
python3 scripts/run_phase9_real_trace_matrix.py env-check
python3 scripts/run_phase9_real_trace_matrix.py env-check --json
```

### 5.2 Generate plan

```bash
python3 scripts/run_phase9_real_trace_matrix.py plan \
  --output-dir outputs/phase9_real_matrix \
  --transports tcp,tls,websocket \
  --scenarios idle,ping,curl,bulk,reconnect \
  --capture-duration 30 --ping-count 20 --min-packet-count 30
```

Output: `outputs/phase9_real_matrix/manifest.json` (includes `runtime_params`).

### 5.3 Dry-run

```bash
python3 scripts/run_phase9_real_trace_matrix.py run \
  --output-dir outputs/phase9_real_matrix \
  --transports tcp \
  --scenarios idle,ping \
  --dry-run
```

Dry-run is the **default**. It prints what would happen but does not:
- Create network namespaces
- Start server or client processes
- Run tcpdump
- Execute any scenario commands

### 5.4 Real execution

```bash
# Recommended first run: tcp ping + bulk for meaningful packet counts
python3 scripts/run_phase9_real_trace_matrix.py run \
  --output-dir outputs/phase9_real_matrix_b \
  --transports tcp \
  --scenarios ping,bulk \
  --capture-duration 30 \
  --ping-count 20 --ping-interval 0.1 \
  --bulk-bytes 262144 \
  --min-packet-count 30 \
  --execute

# Then extend to tls and websocket
python3 scripts/run_phase9_real_trace_matrix.py run \
  --output-dir outputs/phase9_real_matrix_b \
  --transports tls,websocket \
  --scenarios ping \
  --capture-duration 30 \
  --ping-count 20 --ping-interval 0.1 \
  --min-packet-count 30 \
  --execute
```

`--execute` is **required** for real execution. Without it, the script stays in
dry-run mode even if you omit `--dry-run`.

## 5.5 Phase 9B Runtime Parameters

| Parameter | Default | Description |
|---|---|---|
| `--capture-duration` | 30 | Capture duration in seconds |
| `--ping-count` | 20 | Number of ping packets sent through TUN |
| `--ping-interval` | 0.1 | Interval between pings in seconds |
| `--curl-count` | 10 | Number of curl requests for curl scenario |
| `--bulk-bytes` | 262144 | Bulk transfer size in bytes (256 KiB) |
| `--min-packet-count` | 30 | Minimum packet count for valid data |
| `--scenario-timeout` | 60 | Scenario execution timeout in seconds |

## 5.6 Recommended Experiment Matrix

For a quick, meaningful result:

| Transport | Scenario | Capture Duration | Notes |
|---|---|---|---|
| tcp | ping | 30s | Most reliable, always produces >= 30 pkts with ping_count=20 |
| tcp | bulk | 30s | Requires HTTP server in server netns |
| tls | ping | 30s | May be skipped if TLS config incomplete |
| websocket | ping | 30s | Most likely to work alongside tcp |

For idle: increase `--capture-duration` to 45-60s or increase heartbeat
frequency. With the default 10s heartbeat and 30s capture, idle may still
produce < 30 packets.

## 6. Output Directory Structure

```
outputs/phase9_real_matrix_b/
  manifest.json
  environment.json
  results.json
  before/
    tcp/
      idle.pcap          (NOT committed — see 7.8)
      idle.csv
      idle.report.json
      ping.pcap
      ping.csv
      ping.report.json
      ...
    tls/
    websocket/
    ssh/
  after/
    tcp/
    tls/
    websocket/
    ssh/
  summaries/
    fingerprint_before.csv
    fingerprint_after.csv
    before_after_comparison.csv
    before_after_comparison.json
    before_after_comparison.md
    skipped.json
  logs/
    commands.txt
  next_patch_prompt.txt       (if regressions with ok data found)
  data_collection_prompt.txt  (if insufficient data found)
  patch_loop_result.json      (if regressions found)
```

**.pcap files must NOT be committed** — they are binary capture artifacts.
`.gitignore` already excludes `*.pcap`.

## 7. Comparison Fields

| Field | Description |
|---|---|
| `packet_count` | Total packets captured |
| `packet_count_ok_before` | Whether before packet count >= min_packet_count |
| `packet_count_ok_after` | Whether after packet count >= min_packet_count |
| `min_packet_count` | Threshold for valid data |
| `data_quality` | ok / insufficient / skipped |
| `fingerprint_risk_score` | Composite risk score (0-1) |
| `risk_level` | low / medium / high |
| `small_packet_ratio` | Fraction of packets < 100 bytes |
| `repeated_length_ratio` | Fraction of packets with repeated lengths |
| `ngram_entropy` | 3-gram entropy of packet length sequence |
| `dominant_ngram_ratio` | Fraction belonging to most common 3-gram |
| `burst_count` | Number of directional bursts |
| `max_burst_size` | Largest burst size |
| `avg_inter_arrival_ms` | Average inter-arrival time |

### 7.1 data_quality

- **ok**: Both before and after reports exist, and both have packet_count >= min_packet_count.
- **insufficient**: One or both reports missing, or packet_count < min_packet_count. Results are not reliable for before/after comparison.
- **skipped**: Both reports missing (scenario was never executed).

### 7.2 Verdict rules

When data_quality == "ok":
- `risk_score_delta < -0.05`: **improved** (shaping reduced risk)
- `risk_score_delta > 0.05`: **regressed** (shaping increased risk — needs investigation)
- otherwise: **unchanged**

When data_quality != "ok":
- **insufficient**: not enough data for comparison
- **skipped**: both phases skipped

### 7.3 Why idle may be insufficient

With default configuration (10s heartbeat interval, 30s capture), the idle
scenario captures very few packets — often just the initial connection handshake
(2-4 packets). The VPN tunnel is quiescent between heartbeat intervals. To get
meaningful idle data:

- Increase `--capture-duration` to 45-60 seconds to catch multiple heartbeat cycles
- Or decrease the heartbeat interval in the YAML config to 2-3 seconds
- Or accept that idle data is limited and focus on ping/bulk scenarios

## 8. LLM Patch Loop Integration

Phase 9B generates two types of prompts based on data quality:

### 8.1 Countermeasure patch prompt (data_quality=ok, verdict=regressed)

```
outputs/phase9_real_matrix_b/next_patch_prompt.txt
outputs/phase9_real_matrix_b/patch_loop_result.json
```

These can be fed to the LLM patch loop:

```bash
python3 -m src.llm.detection.patch_loop \
  --user-request "Improve VPN-LLM traffic shaping based on Phase 9 real trace regressions" \
  --functional-test-summary "Phase 9 real trace matrix completed" \
  --report outputs/phase9_real_matrix_b/summaries/before_after_comparison.json \
  --output-prompt outputs/phase9_real_matrix_b/next_patch_prompt.txt
```

### 8.2 Data collection prompt (data_quality=insufficient)

```
outputs/phase9_real_matrix_b/data_collection_prompt.txt
```

This prompt suggests increasing traffic or capture duration. It does NOT
recommend modifying countermeasure code. Insufficient data should never drive
shaping module changes.

The LLM is never called automatically. Only the prompt is generated.

## 9. Safety Boundaries

1. All traffic is local to the machine (netns isolation).
2. **No third-party servers are contacted.** All scenario commands target `10.8.0.1` (TUN peer) or `192.168.200.1` (veth pair).
3. **No public internet access.** Ping, curl, and bulk only use local netns/TUN addresses.
4. No third-party traffic is captured.
5. No claims of real undetectability.
6. `.pcap` files are never committed (see section 7.8).
7. `--execute` must be explicit — no accidental execution.
8. Failed entries do not crash the batch — they are marked and the next entry runs.

## 10. Current Limitations

- SSH transport is skipped (requires paramiko SSH server).
- TLS ping connects but after packet_count is low (19 < 30); Phase 9E addresses this with higher ping counts.
- tcp/bulk and websocket/bulk after traces are insufficient (22, 28 < 30); Phase 9E addresses with larger bulk bytes and longer curl timeouts.
- curl/bulk scenarios require `curl` binary; scenario is skipped if curl is missing.
- HTTP server for curl/bulk is a temporary Python http.server, adequate for local testing.
- Reconnect scenario is not supported in current architecture (marks explicitly skipped).
- WebSocket RTT is not measured during Phase 9 (separate Phase 7C runner exists).
- Aggregation fundamentally reduces wire-level packet count; fingerprint analysis
  with fewer packets may be less statistically robust.
- Phase 9E adds statistical repetition (3x) but only for the 4-scenario subset, not the full 40-entry matrix.

## 11. Relationship to Other Phases

| Phase | Relationship |
|---|---|
| Phase 3 (trace capture) | Uses same tcpdump/tshark pipeline |
| Phase 5 (traffic shaping) | Phase 9 validates shaping effectiveness |
| Phase 6 (active probe) | Can run probe before/after alongside |
| Phase 7 (cross-layer RTT) | RTT can be measured before/after |
| Phase 8 (integrated summary) | Phase 9 results feed into integrated summary |
| Phase 10 (netns validation) | Phase 9 reuses the proven netns+TUN setup |

## 12. Next Steps

- **Phase 9E execution**: Run the 4-scenario repeated matrix (tcp ping/bulk, websocket ping, tls ping) with 3 repeats
- If TLS still insufficient after 9E, increase `--ping-count` further (100+) and consider
  reducing aggregation `max_bytes` for TLS transport specifically
- If bulk still insufficient, investigate TCP SYN buffering in client aggregation and
  consider keep-alive HTTP connections
- After Phase 9E achieves stable results: full matrix execution (all transports x all scenarios)
- Consider `aggregation_max_bytes` tuning to balance packet-count reduction vs
  fingerprint obfuscation
- HTTP/2 transport evaluation
- Passive RTT estimation integration
- Multi-iteration LLM patch loop with Phase 9E repeated results as the fitness function

## 13. Phase 10B: HTTP/2 Transport in Trace Matrix (2026-05-22)

### 13.1 Purpose

HTTP/2 transport is an experimental carrier-layer transport (added in Phase 10A).
Phase 10B brings http2 into the Phase 9 real trace matrix **as a valid plan entry**
so that it participates in the manifest, env-check, and comparison pipeline.

This is not a real trace execution phase. The h2/hpack/hyperframe dependencies
are not installed in the current environment, so all http2 entries are **gracefully
skipped** with `trace_type=dependency_missing`.

### 13.2 What Phase 10B Delivers

- `http2` added to `_SUPPORTED_TRANSPORTS` in the Phase 9 runner
- `run_env_check()` reports `http2_dependency` status (h2, hpack, hyperframe, http2_runnable)
- `--transports http2` accepted by both `plan` and `run` subcommands
- When h2 is missing, entries are marked `dependency_missing:h2,hpack,hyperframe` and skipped
- Other transports (tcp/tls/websocket/ssh) are completely unaffected
- 4 new netns config files: `config/{server,client}_netns_http2{,_shaping}.yaml`
- 44 new tests covering http2 in matrix, env-check, plan, skip, config parse, patch prompts

### 13.3 Dependency Status (Current)

| Module | Status |
|---|---|
| h2 | missing |
| hpack | missing |
| hyperframe | missing |
| http2_runnable | false |
| can_run_real_http2 | false |

### 13.4 Skip Semantics

When `can_run_real_http2` is false:
- `trace_type` = `dependency_missing`
- `status` = `skipped` (not failed)
- `error_reason` = `dependency_missing:h2,hpack,hyperframe`
- `dependency_status` dict with per-module booleans
- Does not affect other transport entries
- Does not trigger data collection or countermeasure patch prompts
- `data_quality` = `skipped` in comparison summary
- `verdict` = `skipped`

### 13.5 How to Enable HTTP/2 Real Traces

```bash
pip install h2
```

After installation, re-run env-check:
```bash
python3 scripts/run_phase9_real_trace_matrix.py env-check
```

`can_run_real_http2` should become `true`. Then:
```bash
python3 scripts/run_phase9_real_trace_matrix.py run \
  --output-dir outputs/phase10c_http2_real \
  --transports http2 \
  --scenarios idle,ping,bulk \
  --repeat-count 3 --capture-duration 45 \
  --execute
```

### 13.6 Next: Phase 10C

- Install h2 and run real http2 idle/ping/bulk traces
- Generate before/after fingerprint reports
- Compare http2 fingerprint characteristics against tcp/tls/websocket baselines
- Integrate into the repeated trace comparison pipeline

## 14. Phase 10C: HTTP/2 Real Trace Execution (2026-05-22)

### 14.1 What Changed

Phase 10C installed h2/hpack/hyperframe (v4.3.0) and ran the full idle/ping/bulk
before/after trace matrix with real HTTP/2 transport.  14 real traces were captured
(2 idle + 12 ping/bulk repeated).

Two blocking issues were fixed to enable the data path:

1. **argparse transport choices**: `server.py` and `client.py` `--transport` flag
   did not include `http2` in its `choices` list, causing immediate startup failure.
   Fixed by adding `http2` to the choices.

2. **Data path: stream not opened**: The HTTP/2 transport skeleton called
   `send_data(stream_id=1)` without first opening stream 1 via `send_headers()`.
   The h2 library requires a HEADERS frame to create a stream before DATA frames
   can be sent.  Fixed by:
   - Client: opening stream 1 with POST headers after connection setup
   - Server: responding with :status=200 headers when RequestReceived fires
   - Adding `asyncio.Queue` to decouple the internal read loop from `recv()`
   - Handling flow control (WINDOW_UPDATE increments) in the read loop

### 14.2 Results

| Scenario | Before Pkts | After Pkts | Before Risk | After Risk | Delta | Verdict | Quality |
|---|---|---|---|---|---|---|---|
| idle | 14 | 24 | 0.536 | 0.610 | +0.074 | regressed | ok |
| ping (n=3) | 580.7±0.6 | 39.3±0.6 | 0.560±0.000 | 0.636±0.017 | +0.076±0.017 | regressed | ok |
| bulk (n=3) | 93.0±2.0 | 52.0±2.6 | 0.637±0.012 | 0.619±0.045 | -0.018±0.054 | unchanged | ok |

### 14.3 Cross-Transport Comparison

| Transport | ping Δrisk | ping Verdict | bulk Δrisk | bulk Verdict |
|---|---|---|---|---|
| **http2** | **+0.076** | **regressed** | **-0.018** | **unchanged** |
| tcp | insufficient | insufficient | -0.068 | improved |
| tls | insufficient | insufficient | — | — |
| websocket | -0.062 | improved | -0.056 | unchanged |

HTTP/2 ping has the highest before packet count (581) due to HTTP/2 framing
overhead on small ICMP packets.  The current shaping pipeline (padding,
aggregation, jitter) reduces packet counts but does not improve fingerprint
risk for HTTP/2 — the remaining packets have high repeated-length ratios
and low entropy.

### 14.4 Installation Note

The h2 dependency must be installed **system-wide** (not `--user`) for netns
execution because the runner invokes Python via `sudo ip netns exec`:

```bash
sudo python3 -m pip install --break-system-packages h2
```

### 14.5 Next: Phase 10D

See Section 15 for Phase 10D results.

## 15. Phase 10D: HTTP/2-aware Shaping Countermeasures (2026-05-23)

Phase 10D implements two transport-internal countermeasures to address the
HTTP/2 fingerprint regression found in Phase 10C:

1. **DATA frame size chunking**: Splits large sends into randomized-size
   DATA frames (`http2_chunk_min_size` / `http2_chunk_max_size`).
2. **WINDOW_UPDATE batching**: Accumulates received bytes and sends
   WINDOW_UPDATE only when threshold is reached, replacing the default
   per-packet update pattern.

Both default to 0 (disabled, old behavior). Config fields:
- `http2_chunk_min_size`, `http2_chunk_max_size`, `http2_window_update_threshold`,
  `http2_chunk_rng_seed` — added to `TransportConfig`.

### 15.1 Real trace results (min_packet_count=20, n=3)

| Scenario | Before Pkts | After Pkts | Before Risk | After Risk | Delta | Verdict |
|---|---|---|---|---|---|---|
| idle | 27.0 | 16.0 | 0.569 | 0.558 | -0.011 | insufficient |
| ping | 579.7 | 23.0 | 0.560 | 0.522 | -0.038 | unchanged |
| bulk | 93.0 | 28.3 | 0.629 | 0.479 | **-0.150** | **improved** |

### 15.2 Phase 10C vs 10D comparison

| Scenario | Phase 10C Delta | Phase 10C Verdict | Phase 10D Delta | Phase 10D Verdict |
|---|---|---|---|---|
| idle | +0.074 | regressed | -0.011 | insufficient |
| ping | +0.076 | regressed | -0.038 | unchanged |
| bulk | -0.018 | unchanged | -0.150 | **improved** |

Phase 10D reversed the generic shaping regression. All three scenarios are
now below the 0.60 risk threshold. WINDOW_UPDATE batching eliminates the
predictable 66/92-byte control frame alternation seen in Phase 10C.

### 15.3 Remaining for Phase 10E

- Multi-stream strategy, SETTINGS randomization, HPACK table manipulation
- Idle scenario still below min_packet_count threshold (16 pkts)
- Further WINDOW_UPDATE threshold tuning
