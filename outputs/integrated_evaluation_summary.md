# VPN-LLM Integrated Evaluation Summary

Generated: 2026-05-20T08:59:26.290351+00:00
Test baseline: 1324 passed, 6 skipped

## Gate Status

| Gate | Available | Status |
|---|---|---|
| fingerprint | True | available |
| active_probe | True | available |
| cross_layer_rtt | True | available |
| traffic_shaping | True | available |

## 1. Fingerprint Gate

Status: **ok**
Source: `traces/summary.json`
Total entries: 20
Real traces: 3
Skipped: 17
Risk levels: {'medium': 3}

| Transport | Scenario | Risk Level | Risk Score | Packets |
|---|---|---|---|---|
| tcp | idle | medium | 0.6287 | 5 |
| tls | idle | medium | 0.6385 | 12 |
| websocket | idle | medium | 0.5843 | 9 |

## 2. Active Probe Gate

Status: **ok**
Source: `/tmp/probe.report.json`
Scenarios: 12
Silent drops: 0
Risk level: low

## 3. Cross-Layer RTT Gate

Status: **ok**
Source: `/tmp/rtt.report.json`
App RTT: 42.5 ms
Transport RTT: 8.2 ms
App-Transport diff: 34.3 ms
Timing stability: 0.8575
Risk score: 0.6
Risk level: **high**

## 4. Traffic Shaping (Synthetic)

Status: **ok**
Source: `traces_after/synthetic_comparison.json`
Before risk: 0.3899
After padding risk: 0.3229
After aggregation risk: 0.3497
Deltas: {"padding_vs_noop": {"repeated_length_ratio": 0.0, "small_packet_ratio": -0.12, "risk_score": -0.067}, "aggregation_vs_noop": {"repeated_length_ratio": 0.1029, "small_packet_ratio": -0.6, "risk_score": -0.0402, "packet_count_reduction": "100 -> 7"}}

## 5. Current Limitations

- Most fingerprint traces are 'report not found' — only 3 real idle traces exist
- Traffic shaping comparison is synthetic (computer-generated packet sizes)
- No real WebSocket RTT before/after measurement
- No real network probe testing
- No passive RTT estimation

---

See [docs/phase8_integrated_evaluation.md](docs/phase8_integrated_evaluation.md) for full details.