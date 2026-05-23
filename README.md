# VPN-LLM — LLM-Assisted Modular VPN Transport Research Platform

VPN-LLM is a research platform that closes the loop from **detection paper analysis**
through **local evaluation gates** and **LLM-generated countermeasures** to
**real TUN/netns before/after trace comparison**. It supports pluggable transports
(TCP, TLS, WebSocket, SSH, HTTP/2), traffic shaping primitives, and three local
evaluation gates (fingerprint, active probe, cross-layer RTT).

> **Security boundary**: Research and education only. No real undetectability claim.
> No third-party scanning. All evaluation is local, controlled, reproducible.
> No pcap files committed. No browser emulation claim.

## Architecture

```
Detection papers → local evaluation gate → DetectionReport
  → CountermeasurePolicy → LLM prompt → code patch
  → unit tests → real TUN/netns trace → before/after risk comparison
```

**Data plane**: TUN → Core → Frame → TrafficShaper → Transport → Wire

**Control loop** (LLM is NOT in the runtime data path): DetectionReport → Gate →
CountermeasurePolicy → PromptBuilder → LLM Patch Loop → SafetyGuard → ValidationRunner →
pytest → Real Trace Matrix

## Quick Start

```bash
# Run all tests
python -m pytest tests/ --ignore=vpn_tunnel -q

# Mock TUN mode (no root)
python -m src.server --config config/server.yaml --transport tcp --mock-tun &
python -m src.client --config config/client.yaml --transport tcp --mock-tun
```

## Key Modules

| Area | Path | Purpose |
|---|---|---|
| **Transport** | `src/transport/` | TCP, TLS, WebSocket, SSH, HTTP/2 — pluggable via factory |
| **Core** | `src/core/` | Client/server forwarding loops, heartbeat, session_id |
| **Shaping** | `src/shaping/` | Padding, aggregation, jitter, fragmentation, scheduler, timing |
| **Fingerprint** | `src/evaluation/fingerprint/` | Packet size, n-gram, burst feature extraction from pcap |
| **Active Probe** | `src/evaluation/probe/` | Malformed-input resistance (9 scenarios, silent-drop) |
| **Cross-Layer RTT** | `src/evaluation/rtt/` | App/transport/network RTT comparison |
| **LLM Detection** | `src/llm/detection/` | DetectionReport, Gate, CountermeasurePolicy, PromptBuilder, PatchLoop |
| **LLM Framework** | `src/llm/` | Task planning, file selection, impact expansion, patch generation, safety |

## LLM Workflow

```
User request → TaskPlanner → RepoIndexer → FileRetriever → ImpactExpander
  → ContextBuilder → PatchGenerator → SafetyGuard → ValidationRunner
  → pytest → ReplacementValidator → Report + Commit Advice
```

Key safety boundaries: never auto-commit, never auto-push, `--apply-patch` is an
explicit human gate, SafetyGuard blocks dangerous paths (`.git`, `.env`, `*.key`)
and commands (`sudo`, `rm -rf`).

## Evaluation Gates

| Gate | Path | What it measures |
|---|---|---|
| **Fingerprint** | `src/evaluation/fingerprint/` | Packet size uniformity, small-packet ratio, n-gram entropy, burst patterns, IAT |
| **Active Probe** | `src/evaluation/probe/` | Malformed-input response variance, close-time consistency |
| **Cross-Layer RTT** | `src/evaluation/rtt/` | App/transport/network RTT gap, timing stability |
| **Real Trace Matrix** | `scripts/run_phase9_real_trace_matrix.py` | TUN/netns before/after repeated capture + statistical comparison |

## Transport Status

| Transport | Status | Real Trace | Notes |
|---|---|---|---|
| TCP | Stable | Phase 9 before/after, e2e-ping | No encryption |
| TLS | Stable | Phase 9 before/after | ping flush-limited |
| WebSocket | Stable | Phase 9 before/after, e2e-ping | Background asyncio loop |
| SSH | Client-only | Skipped | No server accept loop |
| HTTP/2 | Experimental | Phase 10C→10E-B repeated | All after-risk <0.60; HPACK deferred |

## HTTP/2 Pipeline (Phase 10)

| Phase | Countermeasure | Result |
|---|---|---|
| 10C | Generic shaping | Regression on idle/ping |
| 10D | DATA chunking + WU batching | Reversed regression |
| 10E-A | Multi-stream (round_robin/random) | No added risk |
| 10E-B | SETTINGS profiles (3 profiles, ±5% jitter) | No added risk; bulk after-risk 0.402 |

HPACK/header behavior is explicitly deferred as future work.

## Documentation Index

| Document | Content |
|---|---|
| [docs/final_integrated_report.md](docs/final_integrated_report.md) | **Complete Phase 11 report** — architecture, LLM role, detection surfaces, evaluation gates, countermeasures, trace results, limitations, future work |
| [docs/detection_coverage_matrix.md](docs/detection_coverage_matrix.md) | Paper-to-gate coverage matrix (4 papers, 14 surfaces) |
| [docs/phase9_real_trace_matrix.md](docs/phase9_real_trace_matrix.md) | Phase 9 real TUN/netns trace methodology and results |
| [docs/transports/http2.md](docs/transports/http2.md) | HTTP/2 transport design, phases 10C-10E-B, trace results |
| [docs/llm_detection_adversarial_loop.md](docs/llm_detection_adversarial_loop.md) | LLM detection-adversarial patch loop design |
| [docs/traffic_shaping.md](docs/traffic_shaping.md) | Traffic shaping design and primitives |
| [docs/fingerprint_evaluation.md](docs/fingerprint_evaluation.md) | Fingerprint evaluation design |
| [docs/active_probe_resistance.md](docs/active_probe_resistance.md) | Active probe resistance design |
| [docs/cross_layer_rtt_evaluation.md](docs/cross_layer_rtt_evaluation.md) | Cross-layer RTT evaluation design |
| [docs/llm_agent_design.md](docs/llm_agent_design.md) | LLM agent framework design |
| [docs/phase10_netns_tun_validation.md](docs/phase10_netns_tun_validation.md) | netns + TUN validation gates |

## Current Status — Phase 11 Convergence

- **Branch**: `test-2`, HEAD: `cfa4faf`
- **Tests**: 1619 passed, 10 skipped
- **Phase**: 11 — Final Integrated Report and Project Convergence
- **No new features planned** — HPACK, passive RTT, full repeated matrix deferred to future work
- **Focus**: documentation, experiment archiving, presentation materials

## Quick Commands

```bash
# Tests
python -m pytest tests/ --ignore=vpn_tunnel -q

# Integrated evaluation summary
python scripts/generate_integrated_evaluation_summary.py

# Final project summary (Phase 11)
python scripts/generate_final_project_summary.py --output-dir outputs/final_summary

# Fingerprint report
python scripts/summarize_fingerprint_reports.py --output-json traces/summary.json

# LLM detection patch loop
python -m src.llm.detection.patch_loop \
  --user-request "reduce cross-layer RTT fingerprint risk" \
  --report /tmp/rtt.report.json \
  --output-prompt /tmp/fix_prompt.txt

# Real trace matrix (requires sudo + netns)
sudo python scripts/run_phase9_real_trace_matrix.py run \
  --output-dir outputs/phase9_run --transports tcp,websocket \
  --scenarios idle,ping,bulk --repeat-count 3 --capture-duration 45 --execute
```
