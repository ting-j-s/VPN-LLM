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

40 total entries (4 transports x 5 scenarios x 2 phases).

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
