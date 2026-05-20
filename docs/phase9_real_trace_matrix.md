# Phase 9: Real TUN/netns Before/After Trace Matrix

## 1. Purpose

Phase 9 captures **real VPN-LLM tunnel traffic** before and after enabling traffic
shaping, using Linux network namespaces and TUN devices. This produces the first
experimental evidence of whether the shaping modules actually reduce
fingerprintability in a controlled local setting.

Phase 8 produced documentation and integration. Phase 9 produces data.

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
- **After**: shaping enabled, using `config/examples/shaping_padding_aggregation.yaml`
  (padding + aggregation + jitter enabled).

## 4. Environment Requirements

| Requirement | Purpose |
|---|---|
| Linux with `/dev/net/tun` | TUN device for VPN tunnel |
| root or `CAP_NET_ADMIN` | Create netns, TUN devices, veth pairs |
| `tcpdump` | Capture tunnel traffic on veth |
| `tshark` | Convert pcap to CSV |
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
  --scenarios idle,ping,curl,bulk,reconnect
```

Output: `outputs/phase9_real_matrix/manifest.json`

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
python3 scripts/run_phase9_real_trace_matrix.py run \
  --output-dir outputs/phase9_real_matrix \
  --transports tcp \
  --scenarios idle \
  --execute
```

`--execute` is **required** for real execution. Without it, the script stays in
dry-run mode even if you omit `--dry-run`.

## 6. Output Directory Structure

```
outputs/phase9_real_matrix/
  manifest.json
  environment.json
  results.json
  before/
    tcp/
      idle.pcap          (NOT committed)
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
  next_patch_prompt.txt       (if regressions found)
  patch_loop_result.json      (if regressions found)
  pcap/                       (NOT committed — .gitignore)
```

**.pcap files must NOT be committed** — they are binary capture artifacts.
`.gitignore` already excludes `*.pcap`.

## 7. Comparison Fields

| Field | Description |
|---|---|
| `packet_count` | Total packets captured |
| `fingerprint_risk_score` | Composite risk score (0-1) |
| `risk_level` | low / medium / high |
| `small_packet_ratio` | Fraction of packets < 100 bytes |
| `repeated_length_ratio` | Fraction of packets with repeated lengths |
| `ngram_entropy` | 3-gram entropy of packet length sequence |
| `dominant_ngram_ratio` | Fraction belonging to most common 3-gram |
| `burst_count` | Number of directional bursts |
| `max_burst_size` | Largest burst size |
| `avg_inter_arrival_ms` | Average inter-arrival time |

### Verdict rules

- `risk_score_delta < -0.05`: **improved** (shaping reduced risk)
- `risk_score_delta > 0.05`: **regressed** (shaping increased risk — needs investigation)
- otherwise: **unchanged**
- missing before or after report: **insufficient**
- both missing: **skipped**

## 8. LLM Patch Loop Integration

If the comparison finds regressed entries, the script generates:

```
outputs/phase9_real_matrix/next_patch_prompt.txt
outputs/phase9_real_matrix/patch_loop_result.json
```

These can be fed to the LLM patch loop:

```bash
python3 -m src.llm.detection.patch_loop \
  --user-request "Improve VPN-LLM traffic shaping based on Phase 9 real trace regressions" \
  --functional-test-summary "Phase 9 real trace matrix completed" \
  --report outputs/phase9_real_matrix/summaries/before_after_comparison.json \
  --output-prompt outputs/phase9_real_matrix/next_patch_prompt.txt
```

The LLM is never called automatically. Only the prompt is generated.

## 9. Safety Boundaries

1. All traffic is local to the machine (netns isolation).
2. No third-party servers are contacted.
3. No third-party traffic is captured.
4. No claims of real undetectability.
5. `.pcap` files are never committed.
6. `--execute` must be explicit — no accidental execution.
7. Failed entries do not crash the batch — they are marked and the next entry runs.

## 10. Current Limitations

- SSH transport is skipped (requires paramiko SSH server).
- TLS transport may fail if TLS config is incomplete.
- curl/bulk scenarios require an HTTP/TCP server inside the VPN tunnel (not yet automated).
- WebSocket RTT is not measured during Phase 9 (separate Phase 7C runner exists).
- Single capture per scenario (no statistical repetition).
- No passive RTT estimation integrated.

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

- Execute the full matrix and collect real before/after data
- Integrate probe gate and RTT gate into the same before/after pass
- Add statistical repetition (3+ runs per scenario)
- HTTP/2 transport evaluation
- Passive RTT estimation integration
- Multi-iteration LLM patch loop with Phase 9 as the fitness function
