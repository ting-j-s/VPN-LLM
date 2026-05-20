# Phase 8: Integrated Evaluation — System Overview

## 1. Project Objective

VPN-LLM is an LLM-assisted modular VPN transport research platform. Its core research question is:

> **Can we convert VPN/proxy detection surfaces from four peer-reviewed papers into local,
> reproducible evaluation gates, and use an LLM patch loop to generate and verify
> countermeasure patches?**

All evaluation is local, controlled, and reproducible. No claims of real undetectability.

## 2. LLM as Core Workflow

The LLM is not a general-purpose auto-coder. It is the central routing engine in a
detection-adversarial patch loop:

```
Detection Report (JSON)
  → DetectionGate.evaluate()
    → CountermeasurePolicy.suggest()
      → PromptBuilder.build()
        → LLM generates patch
          → git apply --check
            → compileall + pytest
              → re-evaluate with DetectionGate
```

Every countermeasure is measurable before and after. No speculative fixes.

## 3. Four-Paper Detection Surface Mapping

| Paper | Detection Surface | Year |
|---|---|---|
| **OpenVPN Fingerprinting** (Mazurczyk et al.) | Packet size distribution, repeated lengths, inter-arrival timing, direction patterns — passive traffic analysis | 2016 |
| **Encapsulated TLS Handshakes** | N-gram entropy, dominant n-gram ratio, small-packet ratio, burst profiles — TLS-in-TLS shape recognition | 2020 |
| **CalcuLatency** | Cross-layer RTT: application-layer (WebSocket echo) vs transport-layer (TCP connect) latency gap — relay/proxy detection | 2020 |
| **Cross-Layer RTT Passive Detection** | Multi-layer RTT (app/transport/network), timing stability, session misalignment — extended CalcuLatency observables | 2022 |

## 4. System Architecture (Text Diagram)

```
┌─────────────────────────────────────────────────────────────────────┐
│                        VPN-LLM System                                │
│                                                                      │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐        │
│  │ Transport │   │ VPN Core │   │   TUN    │   │ Forward  │        │
│  │ mock/tcp  │   │ Frame    │   │  mock/   │   │ NAT /    │        │
│  │ /tls/ws   │   │ Session  │   │  linux   │   │ Route    │        │
│  └─────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘        │
│        │              │              │              │               │
│        └──────────────┴──────────────┴──────────────┘               │
│                           │                                          │
│  ┌────────────────────────┼────────────────────────────────────┐    │
│  │            TRAFFIC SHAPING LAYER (Phase 5/5B/5C)             │    │
│  │  ┌──────┐ ┌──────┐ ┌──────────┐ ┌────────┐ ┌──────────┐   │    │
│  │  │Padding│ │Jitter│ │Aggregation│ │Fragment│ │Scheduler │   │    │
│  │  └──────┘ └──────┘ └──────────┘ └────────┘ └──────────┘   │    │
│  │  ┌──────────────────────────────────────────────────────┐   │    │
│  │  │         Timing Policy (RTT-aware hooks)               │   │    │
│  │  └──────────────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                           │                                          │
│  ┌────────────────────────┼────────────────────────────────────┐    │
│  │                EVALUATION GATES                              │    │
│  │                                                              │    │
│  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐   │    │
│  │  │  Fingerprint  │  │  Active Probe │  │ Cross-Layer   │   │    │
│  │  │  Gate          │  │  Gate         │  │  RTT Gate     │   │    │
│  │  │  (Phase 1-3.5)│  │  (Phase 6/6B) │  │  (Phase 7/7C) │   │    │
│  │  └───────┬───────┘  └───────┬───────┘  └───────┬───────┘   │    │
│  │          │                  │                  │            │    │
│  │          └──────────────────┴──────────────────┘            │    │
│  │                             │                                │    │
│  │               DetectionReport (unified)                      │    │
│  └─────────────────────────────┼────────────────────────────────┘    │
│                                │                                     │
│  ┌─────────────────────────────┼────────────────────────────────┐    │
│  │                  LLM PATCH LOOP (Phase 4)                     │    │
│  │                                                               │    │
│  │  DetectionGate ─→ CountermeasurePolicy ─→ PromptBuilder       │    │
│  │       │                   │                    │               │    │
│  │       │          ┌────────┘                    │               │    │
│  │       │          ▼                             ▼               │    │
│  │       │    CountermeasureHints          LLM Patch Prompt       │    │
│  │       │          │                             │               │    │
│  │       │          └─────────┬───────────────────┘               │    │
│  │       │                    ▼                                    │    │
│  │       │            Patch + Re-evaluate                         │    │
│  │       └────────────────────┘                                    │    │
│  └────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

## 5. Evaluation Gates

### 5.1 Fingerprint Gate (Phase 1-3.5)

**Paper**: OpenVPN Fingerprinting + Encapsulated TLS Handshakes

**Input**: PCAP traces or trace scenario outputs from `scripts/trace_capture.py`

**Metrics**:
- `packet_count` — minimum data threshold
- `repeated_length_ratio` — packet size uniformity
- `small_packet_ratio` — proportion < 100 bytes
- `ngram_entropy` — 3-gram entropy over packet sizes
- `dominant_ngram_ratio` — most-common 3-gram proportion
- `burst_count` / `max_burst_size` — burst behavior
- `dominant_burst_direction_ratio` — direction asymmetry
- `avg_inter_arrival_ms` — timing consistency

**CLI**:
```bash
python3 scripts/run_trace_scenarios.py
python3 scripts/summarize_fingerprint_reports.py --output-json traces/summary.json
```

### 5.2 Active Probe Gate (Phase 6/6B)

**Paper**: Encapsulated TLS (malformed-input resistance)

**Input**: Simulated probe scenarios against VPN protocol

**Scenarios**:
- Malformed frame decode (too short, bad magic, invalid type, oversized length)
- HTTP GET / TLS ClientHello garbage → silent drop
- Wrong session_id → no response
- Normal frames unaffected by malformed handling

**Metrics**:
- `probe_response_variance` — consistency of response behavior
- `malformed_close_time_variance` — timing consistency of drop decisions

**CLI**:
```bash
python3 -m src.evaluation.probe.report --mock --output-json /tmp/probe.report.json
```

### 5.3 Cross-Layer RTT Gate (Phase 7/7B/7C)

**Papers**: CalcuLatency + Cross-Layer RTT Passive Detection

**Layers**:
- **Application**: WebSocket echo RTT (real, via `websocket_rtt.py`) or mock
- **Transport**: TCP connect RTT (local, via `LocalTCPRTTRunner`) or mock
- **Network**: ICMP ping RTT (optional, best-effort, via `OptionalPingRunner`)

**Metrics**:
- `app_transport_diff_ms` — CalcuLatency detection vector
- `app_network_diff_ms` — extended cross-layer diff
- `timing_stability_score` — CV-based stability (higher = more detectable)

**Modes**:
- `--mock-profile direct` — synthetic direct connection (low risk)
- `--mock-profile proxy_like` — synthetic proxy relay (high risk)
- `--mode tcp` — real TCP connect to localhost
- `--mode websocket` — real WebSocket echo + TCP connect cross-layer

**CLI**:
```bash
python3 -m src.evaluation.rtt.report --mock-profile proxy_like --output-json /tmp/rtt.report.json
python3 -m src.evaluation.rtt.report --mode websocket --ws-port 8765 --output-json /tmp/ws_rtt.report.json
```

## 6. Countermeasure Modules

| Module | Source | Mechanism | Triggered By |
|---|---|---|---|
| **Padding** | `src/shaping/padding.py` | Random padding 8-32 bytes per frame | `repeated_length_ratio`, `small_packet_ratio` |
| **Aggregation** | `src/shaping/aggregation.py` | Buffer frames up to N bytes before send | `repeated_length_ratio`, `small_packet_ratio` |
| **Jitter** | `src/shaping/jitter.py` | Random delay per send | `avg_inter_arrival_ms` deviation |
| **Fragmentation** | `src/shaping/fragmentation.py` | Split large frames | `max_burst_size` |
| **Scheduler** | `src/shaping/scheduler.py` | Configurable send scheduling | timing stability |
| **Timing Policy** | `src/shaping/timing.py` | RTT-aware pacing hooks | `app_transport_diff_ms` |
| **Dummy Hook** | `src/shaping/timing.py` | Synthetic dummy traffic injection | `dominant_burst_direction_ratio` |
| **Malformed Silent-Drop** | `src/core/server_core.py` | Uniform silent-drop for all malformed inputs | `probe_response_variance`, `malformed_close_time_variance` |

## 7. LLM Patch Loop

### 7.1 Components

| Component | File | Role |
|---|---|---|
| **DetectionReport** | `src/llm/detection/detector_report.py` | Unified report format from fingerprint/probe/RTT sources |
| **DetectionGate** | `src/llm/detection/gate.py` | Multi-metric threshold evaluation |
| **CountermeasurePolicy** | `src/llm/detection/countermeasure_policy.py` | Metric → countermeasure hint mapping |
| **PromptBuilder** | `src/llm/detection/prompt_builder.py` | Build LLM patch prompt from failed metrics + code context |
| **PatchLoop** | `src/llm/detection/patch_loop.py` | End-to-end detection → hint → prompt → validation loop |

### 7.2 Flow Example

```bash
# Generate a high-risk RTT report
python3 -m src.evaluation.rtt.report --mock-profile proxy_like --output-json /tmp/rtt_proxy.report.json

# Run patch loop — produces countermeasure hints + LLM prompt
python3 -m src.llm.detection.patch_loop \
  --user-request "reduce cross-layer RTT fingerprint risk" \
  --report /tmp/rtt_proxy.report.json \
  --output-prompt /tmp/rtt_fix_prompt.txt \
  --output-json /tmp/rtt_patch_loop.json
```

### 7.3 Threshold Config (selected)

| Threshold | Default | Source Gate |
|---|---|---|
| `max_risk_score` | 0.70 | All |
| `max_repeated_length_ratio` | 0.60 | Fingerprint |
| `max_small_packet_ratio` | 0.60 | Fingerprint |
| `max_dominant_ngram_ratio` | 0.50 | Fingerprint |
| `max_dominant_burst_direction_ratio` | 0.80 | Fingerprint |
| `min_packet_count` | 5 | Fingerprint |
| `max_app_transport_diff_ms` | 50.0 | RTT |
| `max_probe_response_variance` | — | Probe |

## 8. Before/After Results

### 8.1 Traffic Shaping — Synthetic Comparison

Generated by `scripts/synthetic_shaping_comparison.py` with seed=42, 100 synthetic packets.

| Metric | Noop | Padding | Padding+Aggregation |
|---|---|---|---|
| Packet count | 100 | 100 | 7 |
| Small-packet ratio | 0.60 | 0.48 | 0.00 |
| Repeated-length ratio | 0.04 | 0.04 | 0.14 |
| N-gram entropy | 2.74 | 3.02 | 0.00 |
| Fingerprint risk score | 0.39 | 0.32 | 0.35 |
| Risk level | medium | medium | medium |

*Note: Synthetic only. Not from real network capture.*

### 8.2 Active Probe — Before/After

Malformed input handling unified in Phase 6B:

| Scenario | Before (Phase 6) | After (Phase 6B) |
|---|---|---|
| Frame too short | Error response | Silent drop |
| Bad magic | Error response | Silent drop |
| Invalid frame type | Error response | Silent drop |
| Oversized declared length | Error response | Silent drop |
| HTTP GET garbage | Error response | Silent drop |
| TLS ClientHello-like | Error response | Silent drop |
| Random bytes | Error response | Silent drop |
| Zero bytes | Error response | Silent drop |
| Wrong session_id | Error response | Silent drop |

*Unified behavior: all malformed inputs dropped silently, no response generated.*

### 8.3 Cross-Layer RTT — Synthetic Before/After

Mock profiles:

| Profile | App RTT (ms) | Transport RTT (ms) | Diff (ms) | Risk |
|---|---|---|---|---|
| `direct` | ~1.0-1.5 | ~0.4-0.7 | < 1 | low |
| `proxy_like` | ~38-47 | ~7-9 | ~34 | **high** |

*See `scripts/synthetic_rtt_countermeasure_comparison.py` for countermeasure before/after comparison.*

## 9. Current Limitations

1. **No real network evaluation** — All gates use mock data, localhost TCP, or best-effort ping. No real WAN measurement.
2. **No passive RTT estimation** — Does not estimate RTT from existing traffic patterns.
3. **No 0trace / raw socket** — Does not implement TTL-based RTT measurement.
4. **ICMP ping is best-effort** — Requires ping binary and may fail without CAP_NET_RAW.
5. **Synthetic before/after only** — Traffic shaping comparison uses computer-generated packet sizes, not real captures.
6. **Single-target measurement** — No concurrent or multi-path RTT patterns.
7. **No real WebSocket RTT before/after** — WebSocket runner works, but no before/after comparison with traffic shaping on/off.
8. **Fingerprint traces sparse** — Most transport/scenario combinations are "report not found"; only 3 real traces (tcp idle, tls idle, websocket idle).

## 10. Next Steps

### 10.1 Real TUN/netns Trace Capture
Capture real traces through the TUN tunnel in network namespaces for all four transports and all five scenarios. This gives the fingerprint gate real data to evaluate.

### 10.2 Real WebSocket RTT Before/After
Start a real WebSocket echo endpoint in VPN-LLM, measure RTT with shaping on vs off, and quantify the countermeasure effect.

### 10.3 HTTP/2 Transport
Add HTTP/2 as a transport option to evaluate against fingerprint and probe gates.

### 10.4 Passive RTT Estimation
Estimate application-layer RTT from existing request-response patterns without requiring a dedicated echo endpoint.

### 10.5 Richer LLM Auto-Fix Loop
Multi-iteration patch loop with before/after re-evaluation: apply countermeasure, re-measure, re-evaluate, iterate until gate passes.
