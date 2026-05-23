# Detection Coverage Matrix

Machine-readable mapping of four detection papers to VPN-LLM evaluation infrastructure.

## Matrix

| Paper | Detection Surface | Local Gate | Report Fields | Countermeasure Policy | Implemented Patch Module | Current Evidence | Remaining Gap |
|---|---|---|---|---|---|---|---|
| **OpenVPN Fingerprinting** (Mazurczyk et al.) | Packet size uniformity (repeated lengths) | Fingerprint Gate (`src/evaluation/fingerprint/`) | `repeated_length_ratio`, `unique_length_count` | `_HINT_MAP["repeated_length_ratio"]` → random padding, aggregation | `src/shaping/padding.py`, `src/shaping/aggregation.py` | Synthetic: padding reduces small-packet ratio 0.60→0.48. Real traces: 3 idle captures show medium risk. | Real TUN trace capture for all transport×scenario combinations. Before/after with shaping on. |
| **OpenVPN Fingerprinting** (Mazurczyk et al.) | Small-packet dominance | Fingerprint Gate | `small_packet_ratio` | `_HINT_MAP["small_packet_ratio"]` → padding + aggregation | `src/shaping/padding.py`, `src/shaping/aggregation.py` | Synthetic: aggregation eliminates small packets (0.60→0.00). Real traces: 0.58-1.0 small-packet ratio. | Real trace before/after with padding enabled. |
| **OpenVPN Fingerprinting** (Mazurczyk et al.) | Inter-arrival timing patterns | Fingerprint Gate | `avg_inter_arrival_ms` | `_HINT_MAP["avg_inter_arrival_ms_deviation"]` → jitter | `src/shaping/jitter.py`, `src/shaping/scheduler.py` | Real traces: avg IAT 0.027-6.07ms. Jitter module implemented, not yet measured before/after. | Before/after IAT comparison with jitter enabled. |
| **OpenVPN Fingerprinting** (Mazurczyk et al.) | Directional burst patterns | Fingerprint Gate | `burst_count`, `max_burst_size`, `dominant_burst_direction_ratio` | `_HINT_MAP["dominant_burst_direction_ratio"]` → dummy injection, fragmentation | `src/shaping/fragmentation.py`, `src/shaping/timing.py` (dummy hook) | Real traces: dir_ratio 0.50-0.56. Dummy hook and fragmentation implemented, not yet measured. | Before/after burst comparison. |
| **Encapsulated TLS** (Anderson et al.) | N-gram entropy / dominant n-gram | Fingerprint Gate | `ngram_entropy`, `dominant_ngram_ratio` | `_HINT_MAP["dominant_ngram_ratio"]` → padding, fragmentation, aggregation | `src/shaping/padding.py`, `src/shaping/fragmentation.py`, `src/shaping/aggregation.py` | Synthetic: entropy 2.74→3.02 with padding. Real traces: entropy 1.59-3.12. | Real trace before/after with full shaping pipeline. |
| **Encapsulated TLS** (Anderson et al.) | TLS-over-TLS handshake shape | Fingerprint Gate | `small_packet_ratio`, `repeated_length_ratio` | `_HINT_MAP["small_packet_ratio"]` + `_HINT_MAP["repeated_length_ratio"]` combined | Padding + aggregation + fragmentation pipeline | Traffic shaping primitives implemented and configurable via YAML (`src/shaping/config.py`). | Real TLS transport trace capture and shaping before/after. |
| **Encapsulated TLS** (Anderson et al.) | Active probe resistance (malformed input) | Active Probe Gate (`src/evaluation/probe/`) | `probe_response_variance`, `malformed_close_time_variance` | `_HINT_MAP["probe_response_variance"]` → uniform malformed handling | `src/core/server_core.py` (malformed silent-drop policy) | 9/9 malformed scenarios → silent drop (unified). Probe gate report with mock runner. | Real network probe testing. Timing side-channel on drop decisions. |
| **CalcuLatency** (Xue et al.) | Application vs transport RTT gap | Cross-Layer RTT Gate (`src/evaluation/rtt/`) | `app_transport_diff_ms` | `_HINT_MAP["app_transport_diff_ms"]` → RTT-aware pacing, latency budget tracking, scheduler alignment | `src/shaping/timing.py` (RTT-aware hooks), `src/shaping/scheduler.py` | Synthetic: proxy_like diff=34.3ms (high risk), direct diff<1ms (low risk). Real WebSocket RTT runner implemented. | Real VPN-LLM WebSocket echo endpoint. Before/after RTT with timing countermeasures. |
| **CalcuLatency** (Xue et al.) | WebSocket echo application-layer RTT | Cross-Layer RTT Gate | `application_rtt_ms` | `_HINT_MAP["app_transport_diff_ms"]` → pacing | `src/evaluation/rtt/websocket_rtt.py` (real WebSocket echo client) | Local echo server tests pass. Nonce validation works. | Production WebSocket echo endpoint in VPN-LLM server. |
| **Cross-Layer RTT** (passive detection) | Timing stability (CV-based) | Cross-Layer RTT Gate | `timing_stability_score` | `_HINT_MAP["timing_stability_score"]` → jitter, randomized scheduling | `src/shaping/jitter.py`, `src/shaping/scheduler.py` | Synthetic: CV=0.8575 (very stable, detectable). Jitter and scheduler modules implemented. | Before/after stability with jitter+scheduler enabled. |
| **Cross-Layer RTT** (passive detection) | Network-layer RTT (ICMP) | Cross-Layer RTT Gate | `app_network_diff_ms` | Not yet mapped | Not yet implemented | `OptionalPingRunner` exists but ICMP is best-effort; no network diff data. | Requires CAP_NET_RAW or root for reliable ICMP. Real network-layer RTT measurement. |
| **Cross-Layer RTT** (passive detection) | Multi-layer session misalignment | Cross-Layer RTT Gate | `rtt_risk_score` (composite) | `_HINT_MAP["rtt_risk_score"]` → multi-layer countermeasures | Composite: pacing + jitter + scheduler | Composite risk score from app/transport/network layers. Mock data only. | Real multi-layer RTT before/after with all three layers measured. |
| **HTTP/2 Transport** (Phase 10E-B) | HTTP/2 wire-level fingerprinting: SETTINGS frames, HPACK, stream multiplexing patterns | Fingerprint Gate | `fingerprint_risk_score`, `ngram_entropy`, `burst_count` | Not yet mapped (HTTP/2-specific countermeasures) | `src/transport/http2_transport.py` (experimental, h2 required) | Phase 10D: chunking+WU-batching. Phase 10E-A: multi-stream round_robin/random. Phase 10E-B: SETTINGS profile randomization (default/conservative/browser_like_low_variance). All scores <0.60. | HPACK (10E-C). |

## Summary

| Category | Count |
|---|---|
| Detection surfaces mapped | 14 |
| Local gates implemented | 3 (Fingerprint, Active Probe, Cross-Layer RTT) |
| Countermeasure modules implemented | 8 (padding, aggregation, jitter, fragmentation, scheduler, timing, dummy, malformed-drop) |
| Transport types supported | 6 (tcp, tls, websocket, ssh, http2, mock) |
| Surfaces with real-trace evidence | 7 (4 with only 3 real idle traces) |
| Surfaces with synthetic-only evidence | 6 |
| Surfaces with before/after data | 7 (3 synthetic, 4 real including HTTP/2 multi-stream) |
| Remaining gaps: real TUN trace capture | 5+ surfaces |
| Remaining gaps: real WebSocket RTT before/after | 2 surfaces |
| Remaining gaps: real network probe testing | 2 surfaces |
| Remaining gaps: HTTP/2 HPACK | Phase 10D chunking+WU-batching resolved regression. 10E-A multi-stream completed. 10E-B SETTINGS profiles completed. 10E-C (HPACK) remains. |

## How to Read

- **Detection Surface**: Specific observable from the paper that enables VPN/proxy detection.
- **Local Gate**: Which evaluation gate exercises this surface locally.
- **Report Fields**: JSON fields in the `DetectionReport` that carry these metrics.
- **Countermeasure Policy**: Which hint in `CountermeasurePolicy._HINT_MAP` addresses this surface.
- **Implemented Patch Module**: Concrete code module that implements the countermeasure.
- **Current Evidence**: What data we have now (synthetic, real trace, or none).
- **Remaining Gap**: What's needed for complete coverage.
