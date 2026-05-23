# VPN-LLM Final Integrated Report

Generated: 2026-05-23 | Phase 11 Convergence

## 1. Project Positioning

VPN-LLM is an **LLM-assisted modular VPN transport research platform**. It is not a
production VPN and does not claim real undetectability.

The project demonstrates a complete closed loop:

```
Detection papers → local evaluation gate → DetectionReport
→ LLM-generated countermeasure prompt → code patch
→ unit tests → real TUN/netns trace capture
→ before/after risk comparison → next iteration
```

Key design properties:

- **Pluggable transports** — replace the outer framing without touching core logic
- **Local controlled evaluation** — all detection gates run offline, no third-party scanning
- **LLM-driven detection-adversarial patch loop** — structured prompts from DetectionReports
- **Real TUN/netns before/after trace matrix** — experimental evidence, not synthetic-only

## 2. System Architecture

### 2.1 Data Plane

```
TUN device
  → ClientCore / ServerCore (read/write loops, heartbeat, session_id)
    → Frame (VTUN magic + 4-byte length prefix + 16-byte session_id + payload)
      → TrafficShaper (padding, aggregation, jitter, fragmentation, scheduler)
        → Transport (TCP, TLS, WebSocket, SSH, HTTP/2)
          → Wire (localhost / netns, controlled)
```

### 2.2 Detection-Adversarial Control Loop

```
DetectionReport (fingerprint / probe / RTT)
  → DetectionGate (multi-metric threshold evaluation)
    → CountermeasurePolicy (_HINT_MAP: metric → countermeasure direction)
      → PromptBuilder (structured adversarial patch prompt)
        → LLM Patch Loop (planning, file selection, impact expansion, patch generation)
          → SafetyGuard + ValidationRunner
            → Tests (pytest)
              → Real Trace Matrix (TUN/netns before/after)
```

### 2.3 Key Architectural Decisions

| Decision | Rationale |
|---|---|
| LLM is NOT in the runtime data path | LLM operates on detection reports, not live packets |
| Transport abstraction is a single `send/recv` interface | Replace transport without touching core |
| Shaping sits between core and transport | Transport-agnostic countermeasures |
| Evaluation gates are local-only | Reproducible, no network dependency |
| Config-driven transport selection | YAML config, no code change for transport switch |

## 3. LLM Module Role

**LLM is the development/evaluation control loop, not the runtime forwarding path.**

It operates on structured detection reports and produces countermeasure patches:

1. **Task Planning** — classify user request, generate structured TaskPlan
2. **File Selection** — multi-path recall (keyword, symbol, config key, task type rules)
3. **Impact Expansion** — must_edit / must_review / test / doc classification
4. **Context Building** — read selected files, cap at 12KB/file
5. **Patch Generation** — constrained by allowed_edit_files / allowed_create_paths
6. **Safety Guard** — block dangerous paths (.git, .env, *.key), commands (sudo, rm -rf)
7. **Validation** — compileall, pytest, git apply --check
8. **Detection → Countermeasure** — DetectionReport → Gate → Policy → Prompt

### Core Files

| Module | Path | Purpose |
|---|---|---|
| DetectionReport | `src/llm/detection/detector_report.py` | Unified data model for all detection reports |
| DetectionGate | `src/llm/detection/gate.py` | Multi-metric threshold evaluation |
| CountermeasurePolicy | `src/llm/detection/countermeasure_policy.py` | Metric → countermeasure hint mapping |
| PromptBuilder | `src/llm/detection/prompt_builder.py` | Structured adversarial patch prompt |
| PatchLoop | `src/llm/detection/patch_loop.py` | End-to-end CLI |
| LLMClient | `src/llm/llm_client.py` | OpenAI-style API client |
| TaskPlanner | `src/llm/llm_task_planner.py` | LLM-based task decomposition |
| RepoIndexer | `src/llm/repo_indexer.py` | Local AST-based symbol extraction |
| FileRetriever | `src/llm/file_retriever.py` | Multi-path file recall |
| ImpactExpander | `src/llm/impact_expander.py` | FileSelection classification |
| ContextBuilder | `src/llm/context_builder.py` | LLM context assembly |
| PatchGenerator | `src/llm/patch_generator.py` | Constrained patch generation |
| SafetyGuard | `src/llm/safety_guard.py` | Path/command/secret blocking |
| ValidationRunner | `src/llm/validation_runner.py` | Compile + test validation |
| ReplacementValidator | `src/llm/replacement_validator.py` | Smoke matrix validation |

## 4. Transport Evolution

| Transport | Status | Test Coverage | Real Trace | Limitation |
|---|---|---|---|---|
| **TCP** | Stable | Full unit + integration | Phase 9 before/after, e2e-ping verified | No encryption |
| **TLS** | Stable | Full unit + integration | Phase 9 before/after | ping scenario aggregation_flush_limited |
| **WebSocket** | Stable | Full unit + integration | Phase 9 before/after, e2e-ping verified | Background asyncio event loop per instance |
| **SSH** | Client-only | Unit tests (client-side) | Skipped (requires SSH server setup) | No server-side implementation |
| **HTTP/2** | Experimental | Full unit + integration + protocol shape | Phase 10C→10E-B repeated matrix | Requires h2 dependency; HPACK not tuned |

## 5. Detection Surfaces from Papers

| Paper-inspired surface | Local gate | Key metrics | Countermeasure | Evidence |
|---|---|---|---|---|
| OpenVPN packet-size uniformity | Fingerprint Gate | `repeated_length_ratio`, `unique_length_count` | Padding, aggregation | Synthetic: padding reduces ratio 0.60→0.48 |
| OpenVPN small-packet dominance | Fingerprint Gate | `small_packet_ratio` | Padding + aggregation | Synthetic: aggregation eliminates (0.60→0.00) |
| OpenVPN inter-arrival timing | Fingerprint Gate | `avg_inter_arrival_ms` | Jitter, scheduler | Real traces: avg IAT 0.027-6.07ms; jitter implemented |
| OpenVPN directional burst | Fingerprint Gate | `burst_count`, `dominant_burst_direction_ratio` | Dummy injection, fragmentation | Real traces: dir_ratio 0.50-0.56 |
| Encapsulated TLS n-gram entropy | Fingerprint Gate | `ngram_entropy`, `dominant_ngram_ratio` | Padding, fragmentation, aggregation | Synthetic: entropy 2.74→3.02 |
| Encapsulated TLS handshake shape | Fingerprint Gate | `small_packet_ratio`, `repeated_length_ratio` | Combined pipeline | Shaping primitives configurable via YAML |
| Active probe malformed input | Active Probe Gate | `probe_response_variance`, `malformed_close_time_variance` | Uniform silent-drop | 9/9 malformed scenarios → silent drop |
| CalcuLatency app/transport RTT gap | Cross-Layer RTT Gate | `app_transport_diff_ms` | RTT-aware pacing, scheduler | Synthetic: proxy_like diff=34.3ms (high risk) |
| CalcuLatency WebSocket echo RTT | Cross-Layer RTT Gate | `application_rtt_ms` | Pacing | Local echo tests pass; production endpoint needed |
| Cross-layer timing stability | Cross-Layer RTT Gate | `timing_stability_score` | Jitter, randomized scheduling | Synthetic: CV=0.8575 (very stable/detectable) |
| Network-layer RTT (ICMP) | Cross-Layer RTT Gate | `app_network_diff_ms` | Not yet mapped | Requires CAP_NET_RAW; not implemented |
| Multi-layer session misalignment | Cross-Layer RTT Gate | `rtt_risk_score` (composite) | Multi-layer countermeasures | Mock data only |
| HTTP/2 SETTINGS fingerprint | Fingerprint Gate | `fingerprint_risk_score`, `ngram_entropy` | Settings profile randomization | All scores <0.60 after 10E-B |
| HTTP/2 stream multiplexing | Fingerprint Gate | `burst_count` | Multi-stream round_robin/random | No added risk under current matrix |

## 6. Evaluation Gates

### 6.1 Fingerprint Gate (`src/evaluation/fingerprint/`)

Packet-level feature extraction from pcap captures:
- **pcap_features**: small_packet_ratio, repeated_length_ratio, avg_inter_arrival_ms
- **ngram_features**: ngram_entropy, dominant_ngram_ratio (2-gram and 3-gram)
- **burst_features**: burst_count, max_burst_size, dominant_burst_direction_ratio
- **report**: DetectionReport JSON with per-metric risk levels

### 6.2 Active Probe Gate (`src/evaluation/probe/`)

Malformed-input resistance testing:
- 9 probe scenarios (truncated frame, invalid magic, bad version, oversized payload, etc.)
- probe_response_variance and malformed_close_time_variance metrics
- Unified silent-drop policy in `server_core.py`

### 6.3 Cross-Layer RTT Gate (`src/evaluation/rtt/`)

CalcuLatency-inspired multi-layer RTT comparison:
- Application-layer RTT (WebSocket echo)
- Transport-layer RTT (TCP)
- Network-layer RTT (ICMP, best-effort)
- Composite `rtt_risk_score`

### 6.4 HTTP/2 Trace Matrix (`scripts/run_phase9_real_trace_matrix.py`)

Extended Phase 9 runner with HTTP/2 support:
- env-check for h2 dependency
- Netns-based TUN trace capture
- Repeated before/after comparison with statistical aggregation
- Phase 10D→10E countermeasure evaluation

### 6.5 Integrated Summary (`scripts/generate_integrated_evaluation_summary.py`)

Cross-gate summary aggregating fingerprint, probe, RTT, and trace results into a single
JSON and Markdown report.

## 7. Countermeasure Modules

### 7.1 Generic (Transport-Agnostic)

| Module | File | Mechanism |
|---|---|---|
| **Padding** | `src/shaping/padding.py` | Random padding to smooth packet size distribution |
| **Aggregation** | `src/shaping/aggregation.py` | Buffer and coalesce small packets before send |
| **Jitter** | `src/shaping/jitter.py` | Random delay before send to disrupt timing |
| **Fragmentation** | `src/shaping/fragmentation.py` | Split large packets into variable-sized fragments |
| **Scheduler** | `src/shaping/scheduler.py` | Rate-limited send scheduling |
| **Timing hooks** | `src/shaping/timing.py` | RTT-aware pacing and dummy traffic injection hooks |
| **Malformed drop** | `src/core/server_core.py` | Silent-drop malformed frames (unified across all transports) |

### 7.2 HTTP/2-Aware (Phase 10D→10E-B)

| Countermeasure | Phase | Mechanism |
|---|---|---|
| **DATA chunking** | 10D | Randomize DATA frame payload sizes (configurable min/max) |
| **WINDOW_UPDATE batching** | 10D | Accumulate and batch WINDOW_UPDATE frames by threshold |
| **Multi-stream** | 10E-A | Round-robin or random stream assignment (1/N streams) |
| **SETTINGS profiles** | 10E-B | Configurable SETTINGS values (conservative / browser_like_low_variance) |

## 8. Real Trace Matrix Results (Phase 9)

Phase 9 introduced real TUN/netns before/after trace capture with statistical evaluation.

### Key Results

| Transport | Scenario | Δrisk | Verdict | Note |
|---|---|---|---|---|
| tcp | bulk | -0.068 | improved | Aggregation effective on bulk transfers |
| tcp | ping | — | unchanged | Long-capture ping stabilized |
| websocket | ping | -0.062 | improved | Shaping effective on WebSocket ping |
| tls | ping | — | aggregation_flush_limited | TLS adds its own framing |
| tcp | idle | — | insufficient (packet count <30) | Idle has very few packets |

### Methodology

- Linux network namespaces (netns) for isolation
- Real TUN devices with `sudo`
- 45-second captures, repeated (n=3 or more)
- `data_quality` field: ok / insufficient / skipped
- `min_packet_count` threshold for statistical validity
- No pcap files committed to repository

## 9. HTTP/2 Phase 10 Results

### Phase Progression

| Phase | Change | idle risk | ping risk | bulk risk | Key finding |
|---|---|---|---|---|---|
| **10C** | HTTP/2 real trace (generic shaping) | 0.536→0.610 | 0.560→0.636 | 0.637→0.619 | **Regression**: generic shaping increased risk on idle/ping |
| **10D** | +DATA chunking +WU batching | 0.569→0.558 | 0.560→0.522 | 0.629→0.479 | **Reversed regression**: all scores below 0.60 |
| **10E-A** | +multi-stream (round_robin/random) | 0.512 | 0.547 | 0.509 | No added risk under current matrix |
| **10E-B** | +SETTINGS profile (browser_like_low_variance) | 0.498 | 0.541 | 0.402 | No added risk; bulk risk decreased further |

### Cross-Phase Comparison (after risk scores)

| Scenario | 10C (generic) | 10D (aware) | 10E-A (multi-stream) | 10E-B (SETTINGS) |
|---|---|---|---|---|
| idle | 0.610 | 0.558 | 0.512 | **0.498** |
| ping | 0.636 | 0.522 | 0.547 | **0.541** |
| bulk | 0.619 | 0.479 | 0.509 | **0.402** |

### Key Conclusions

1. **Generic shaping causes HTTP/2 regression** — padding/aggregation not designed for
   HTTP/2 frame patterns (Phase 10C)
2. **WINDOW_UPDATE batching is the dominant countermeasure** — eliminates predictable
   66/92-byte control frame alternation (Phase 10D)
3. **Multi-stream does not introduce new risk** — under current Phase 9/10 trace matrix
   metrics, concurrent stream multiplexing is fingerprint-transparent (Phase 10E-A)
4. **SETTINGS profiles do not increase risk** — browser_like_low_variance profile shows
   lower bulk risk than h2 defaults (Phase 10E-B)
5. **All after-risk scores < 0.60** — HTTP/2 pipeline meets the risk threshold
6. **HPACK/header behavior is future work** — not implemented, not claimed as complete

## 10. Test Summary

**Latest run (2026-05-23): 1633 passed, 10 skipped**

```
python -m pytest tests/ -q
```

### Test Coverage by Area

| Area | Test files | Coverage |
|---|---|---|
| Transport (tcp, tls, websocket, ssh, http2, mock) | 7 files | Connect, send/recv, close, timeout, frame encoding |
| Core (client, server, frame) | 4 files | Forwarding loops, heartbeat, session_id, frame codec |
| Shaping (padding, aggregation, jitter, fragmentation, scheduler, timing, config, factory) | 8 files | Per-module unit tests, config parsing, factory composition |
| Fingerprint (pcap, ngram, burst, report) | 3 files | Feature extraction, report generation, edge cases |
| Active probe (scenarios, runner, report, policy) | 4 files | All 9 scenarios, silent-drop behavior |
| RTT (measurements, runner, report, websocket) | 4 files | Synthetic profiles, WebSocket echo, report |
| LLM framework (task planner, patch, safety, validation, file selection, impact, context, commit) | 16 files | Full replacement workflow, safety boundaries |
| Phase 9/10 runners (trace matrix, HTTP/2, smoke, integrated summary) | 5 files | Config selection, env-check, skip semantics |
| HTTP/2 specific (transport, settings, multi-stream, chunking, factory) | 1 file (411+ lines) | 5 test classes, 28+ tests |

### Skipped Tests (10)

All skips are intentional: root-required TUN tests, paramiko SSH server tests, and
bidirectional data path tests that are timing-sensitive in CI environments.

## 11. Safety and Scope

### What This Project Is

- A research platform for studying VPN transport detectability
- An LLM-assisted development workflow for countermeasure generation
- A local, controlled, reproducible evaluation framework

### What This Project Is NOT

- A production VPN
- A censorship circumvention tool
- A browser emulation or mimicry system
- A claim of real undetectability

### Explicit Boundaries

- **Local controlled experiments only** — all network targets are localhost
- **No third-party scanning** — no external network probing
- **No browser emulation claim** — HTTP/2 SETTINGS profiles are protocol-shape
  evaluation, not browser mimicry
- **No real undetectability claim** — lab results do not generalize to real networks
- **pcap not committed** — `.gitignore` excludes capture artifacts
- **No HPACK/header behavior** — explicitly marked as future work

## 12. Limitations

1. **SSH server integration limited** — client-side only, no server accept loop
2. **TLS ping flush-limited** — TLS framing interferes with aggregation flush timing
3. **Controlled-lab results only** — all measurements are localhost/netns, not real
   network paths
4. **No passive RTT estimation** — only active probing (ICMP/WebSocket echo)
5. **No HPACK/header behavior** — HTTP/2 HPACK tuning not implemented (future work)
6. **No full browser-like HTTP/2 stack** — no PRIORITY frames, no browser header order
7. **No large-scale real-world validation** — single-machine netns experiments only
8. **ICMP requires privileges** — network-layer RTT needs CAP_NET_RAW or root
9. **Packet counts low under aggressive batching** — WINDOW_UPDATE batching at high
   thresholds produces too few packets for statistical comparison (min_packet_count=30
   not met in after-traces)
10. **Shaper decode errors in edge cases** — padding envelope mismatch observed
    during bulk scenarios when HTTP/2-aware shaping is aggressive

## 13. Future Work

These items are explicitly deferred. They are not in the current scope and should
not be implemented without a new phase plan:

1. **HPACK/header behavior** (deferred from Phase 10E-C) — dynamic table tuning,
   pseudo-header ordering, header compression side effects
2. **Passive RTT estimation** — estimate RTT from existing traffic without active probes
3. **Full repeated matrix** — all transport × scenario combinations with n≥3 repeats
4. **Real WebSocket RTT before/after** — production echo endpoint + timing countermeasures
5. **Richer LLM auto-fix loop** — multi-turn patch refinement, failure feedback
6. **CI integration** — GitHub Actions for automated trace matrix on push
7. **Optional push/sync** — real network path testing (opt-in, manual only)
8. **Benchmark/stability gate** (Gate 5 from Phase 10) — throughput and long-run
   stability under shaping
