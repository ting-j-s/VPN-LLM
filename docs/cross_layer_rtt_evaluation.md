# Phase 7: Cross-Layer RTT Evaluation Gate

## 1. Purpose

Phase 7 introduces a cross-layer RTT evaluation gate that measures and compares
application-layer, transport-layer, and network-layer round-trip times. The goal
is to detect stable cross-layer RTT differences that could serve as
fingerprinting vectors — a technique documented in the CalcuLatency and
cross-layer RTT passive detection papers.

This module:
- Collects RTT samples per layer (mock, TCP connect, optional ICMP ping)
- Computes cross-layer RTT differences (app-transport, app-network)
- Scores timing stability (lower variance = more detectable)
- Produces a report compatible with the Phase 4 DetectionGate / CountermeasurePolicy / LLM patch loop

## 2. Relationship to Detection Papers

### 2.1 CalcuLatency

The CalcuLatency paper demonstrates that an observer who can measure
application-layer RTT (e.g., via protocol timing) and compare it to
transport-layer RTT (e.g., TCP handshake) can detect the presence of an
intermediate relay or proxy. Key insight:

- Direct connections: application RTT ≈ transport RTT (minimal gap)
- Proxied connections: application RTT noticeably higher than transport RTT
  (proxy adds processing delay)

CalcuLatency uses WebSocket echo to measure application RTT and TCP connect
for transport RTT.

### 2.2 Cross-Layer RTTs

Cross-layer RTT passive detection extends the CalcuLatency concept to
identify session misalignment and timing inconsistencies across three layers
(application, transport, network). Key observables:

| Observable | Layer | Fingerprint Value |
|---|---|---|
| Application RTT (WebSocket echo) | Application | End-to-end timing including VPN |
| Transport RTT (TCP connect) | Transport | Network path latency |
| Network RTT (ICMP ping) | Network | Baseline network latency |
| App-Transport diff | Cross-layer | Proxy detection vector |
| Timing stability | Cross-layer | Stable inter-arrival or RTT patterns |

### 2.3 Project-Scoped Implementation

Phase 7 focuses on the **local controlled evaluation** of these concepts:

1. **Mock mode**: Deterministic RTT profiles (direct / proxy_like) for testing
2. **TCP mode**: Localhost TCP connect timing for transport RTT
3. **Optional ping**: Best-effort ICMP for network RTT (not required)

The first version does NOT implement:
- 0trace or raw sockets
- Passive cross-correlation of real traffic
- Real WebSocket RTT measurement against a running VPN-LLM server
- External network scanning

## 3. Security: Local-Only Testing

- `LocalTCPRTTRunner` rejects non-localhost targets (127.0.0.1, localhost, ::1)
- `OptionalPingRunner` rejects non-localhost targets
- `MockRTTRunner` never connects to any network
- No third-party scanning functionality exists or is planned

## 4. Data Structures

### 4.1 RTTMeasurement

| Field | Type | Description |
|---|---|---|
| name | str | Label (e.g. "app_echo", "tcp_connect") |
| layer | str | "application" / "transport" / "network" / "synthetic" |
| samples_ms | list[float] | Raw samples |
| min_ms | float | Minimum sample |
| median_ms | float | Median sample |
| avg_ms | float | Mean sample |
| max_ms | float | Maximum sample |
| sample_count | int | Number of valid samples |

### 4.2 CrossLayerRTTReport

| Field | Type | Description |
|---|---|---|
| detector_name | str | "cross_layer_rtt" |
| target | str | Hostname/IP |
| trace_type | str | "real" / "synthetic" / "mock" |
| application_rtt_ms | float | Application-layer RTT (median) |
| transport_rtt_ms | float | Transport-layer RTT (median) |
| network_rtt_ms | float | Network-layer RTT (median) |
| app_transport_diff_ms | float | abs(app - transport) |
| app_network_diff_ms | float | abs(app - network) |
| timing_stability_score | float | 0-1 (higher = more stable = detectable) |
| risk_score | float | 0-1 composite |
| risk_level | str | "low" / "medium" / "high" / "insufficient_data" |

## 5. Scoring Rules

### 5.1 Cross-Layer RTT Diff

| Diff Range | Risk Level | Score Range |
|---|---|---|
| < 15ms | low | 0.0 – 0.3 |
| 15ms – 50ms | medium | 0.3 – 0.6 |
| >= 50ms | high | 0.6 – 1.0 |
| None | insufficient_data | 0.0 |

Timing stability score > 0.7 can elevate a borderline medium to high.

### 5.2 Timing Stability

Coefficient of variation (std/mean) mapped to [0, 1]:
- CV <= 0.05 → stability = 1.0 (very stable = highly detectable)
- CV >= 0.5 → stability = 0.0 (variable = harder to fingerprint)

## 6. Mock Profiles

### Direct Profile
- app_echo: ~1.0–1.5ms (low, tight)
- tcp_connect: ~0.4–0.7ms
- Diff: < 1ms → risk_level: low

### Proxy-Like Profile
- app_echo: ~38–47ms (simulates proxy relay delay)
- tcp_connect: ~7–9ms
- Diff: ~34ms → risk_level: high

## 7. CLI Usage

```bash
# Mock direct profile (low risk)
python3 -m src.evaluation.rtt.report \
  --mock-profile direct \
  --output-json /tmp/rtt_direct.report.json

# Mock proxy_like profile (high risk)
python3 -m src.evaluation.rtt.report \
  --mock-profile proxy_like \
  --output-json /tmp/rtt_proxy.report.json

# TCP RTT to local server
python3 -m src.evaluation.rtt.report \
  --host 127.0.0.1 --port 9000 --mode tcp \
  --output-json traces/rtt/local.report.json
```

## 8. Integration with LLM Patch Loop

```
RTT report JSON
  → load_detection_report() → from_rtt_report()
    → DetectionReport (metrics: app_transport_diff_ms, timing_stability_score, rtt_risk_score)
      → evaluate_detection_report() with DetectionThresholds
        → suggest_countermeasures() from CountermeasurePolicy
          → build_adversarial_patch_prompt() → LLM prompt
```

Usage:

```bash
# Generate RTT report with high diff (proxy_like)
python3 -m src.evaluation.rtt.report \
  --mock-profile proxy_like \
  --output-json /tmp/rtt_proxy.report.json

# Run through patch loop
python3 -m src.llm.detection.patch_loop \
  --user-request "reduce cross-layer RTT fingerprint risk" \
  --report /tmp/rtt_proxy.report.json \
  --output-prompt /tmp/rtt_fix_prompt.txt \
  --output-json /tmp/rtt_patch_loop.json
```

## 9. DetectionThresholds

New RTT-specific thresholds:

| Threshold | Default | Description |
|---|---|---|
| max_app_transport_diff_ms | 50.0 | Max app-transport diff before fail |
| max_app_network_diff_ms | None | Max app-network diff before fail |
| max_timing_stability_score | None | Max timing stability (0-1) before fail |

## 10. Countermeasure Directions

### app_transport_diff_ms (high)

- RTT-aware pacing to align layer latencies
- Latency budget tracking in scheduler
- Reduce transport-layer buffering
- Evaluate multiplex / scheduler interaction
- Target app-transport diff < 15ms

### timing_stability_score (high)

- Add jitter to inter-packet timing
- Randomize scheduler intervals
- Avoid deterministic intervals in all layers

### rtt_risk_score (high)

- Multi-layer timing countermeasures: pacing + jitter + scheduler
- Prioritize reducing app-transport diff first
- Re-evaluate after each countermeasure

## 11. Current Limitations

1. **Mock-only for full cross-layer reports** — Real TCP mode only measures transport RTT; application RTT requires a running echo server or WebSocket endpoint
2. **No WebSocket RTT** — The first version does not implement application-layer RTT via WebSocket echo
3. **No passive RTT estimation** — Does not estimate RTT from existing traffic patterns
4. **No 0trace** — Does not implement TTL-based RTT via raw sockets
5. **ICMP ping is best-effort** — Requires ping binary and may fail without permissions
6. **No real before/after shaping comparison** — RTT report is standalone; does not yet compare shaped vs unshaped traffic
7. **Single-target measurement** — Does not test concurrent or multi-path RTT patterns

## 12. Future Plans

1. **WebSocket echo RTT** — Measure application RTT against running VPN-LLM server
2. **Passive request-response RTT estimation** — Estimate RTT from existing traffic
3. **Synthetic network delay** — Configurable artificial delay for testing
4. **RTT-aware scheduler** — Integrate RTT metrics into traffic shaping scheduler
5. **Before/after shaping RTT comparison** — Compare RTT metrics with traffic shaping on/off
6. **Phase 5D** — Dummy traffic + real jitter scheduler
7. **Phase 7B** — Apply RTT countermeasures and measure before/after improvement
