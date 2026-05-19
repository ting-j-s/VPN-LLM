# Phase 4: LLM Detection-Adversarial Workflow

## 1. Why LLM Is the Core of VPN-LLM

VPN-LLM is not just a multi-transport VPN. Its research goal is to let LLM
automatically generate countermeasure patches based on local detection reports.
The system must convert paper-level detection surfaces into evaluation gates
and feed failure signals back to the LLM as structured patch prompts.

## 2. Detection Capabilities from Phase 1–3.5

| Capability | Source | Status |
|---|---|---|
| Burst fingerprint features | `src/evaluation/fingerprint/burst_features.py` | Done |
| Ngram fingerprint features | `src/evaluation/fingerprint/ngram_features.py` | Done |
| Pcap-level features (small packet, repeated length, timing) | `src/evaluation/fingerprint/pcap_features.py` | Done |
| Fingerprint report generation | `src/evaluation/fingerprint/report.py` | Done |
| Trace capture (pcap → CSV → report) | `scripts/trace_capture.py` | Done |
| Batch scenario runner | `scripts/run_trace_scenarios.py` | Done |
| Report summarization (summary JSON/CSV) | `scripts/summarize_fingerprint_reports.py` | Done |
| Real idle baselines (TCP/TLS/WebSocket) | `traces/*/idle.{csv,report.json}` | Done |

## 3. What Phase 4 Adds

1. **DetectionReport** — unified data model for fingerprint / RTT / probe reports
2. **DetectionGate** — multi-metric threshold evaluation
3. **CountermeasurePolicy** — metric → patch direction mapping (based on 4 papers)
4. **PromptBuilder** — structured adversarial patch prompt generation
5. **PatchLoop CLI** — end-to-end detection → prompt pipeline
6. **File selection extension** — new area keys for detection/shaping

## 4. Workflow Diagram

```
User request ("reduce fingerprint risk of idle TCP transport")
   │
   ▼
LLM Planner (task_type=fingerprint_mitigation)
   │
   ▼
File Selection (FileRetriever + ImpactExpander)
   → detection / shaping area keys expand allowed paths
   │
   ▼
LLM Patch (PatchGenerator with allowed_edit_files / allowed_create_paths)
   │
   ▼
Functional Validation (compileall, pytest, git apply --check)
   │
   ▼
Fingerprint / RTT / Probe Report (Phase 1–3.5 tools)
   │
   ▼
Detection Gate (evaluate against thresholds)
   │
   ├── ALL PASS → done, no patch needed
   │
   └── FAIL → Countermeasure Policy
                  │
                  ▼
              Adversarial Patch Prompt
                  │
                  ▼
              Revised LLM Patch (next iteration)
```

## 5. DetectionReport Format

```json
{
  "detector_name": "fingerprint",
  "source_path": "traces/tcp/idle.report.json",
  "trace_type": "real",
  "transport": "tcp",
  "scenario": "idle",
  "passed": false,
  "risk_score": 0.85,
  "risk_level": "medium",
  "metrics": [
    {
      "name": "repeated_length_ratio",
      "value": 0.85,
      "threshold": 0.60,
      "passed": false,
      "severity": "fail",
      "explanation": "repeated_length_ratio=0.85 > max=0.60"
    }
  ],
  "notes": ["high repeated-length ratio"],
  "raw": { ... }
}
```

## 6. DetectionGate Thresholds

Default thresholds (all configurable):

| Threshold | Default | Source Paper |
|---|---|---|
| `max_risk_score` | 0.70 | All |
| `max_repeated_length_ratio` | 0.60 | 1OpenVPN |
| `max_small_packet_ratio` | 0.60 | 1OpenVPN |
| `min_ngram_entropy` | None | 2Encapsulated TLS |
| `max_dominant_ngram_ratio` | 0.50 | 2Encapsulated TLS |
| `max_dominant_burst_direction_ratio` | 0.80 | 2Encapsulated TLS |
| `min_packet_count` | 5 | All |
| `fail_on_insufficient_data` | false | All |
| `max_rtt_diff_ms` | None | 3CalcuLatency |
| `max_probe_response_variance` | None | 1OpenVPN |

## 7. CountermeasureHint Mapping

### 1OpenVPN-style Fingerprint

| Failed Metric | Recommended Changes | Affected Layers |
|---|---|---|
| `repeated_length_ratio` high | random padding, length bucket randomization, frame splitting | frame codec, traffic shaper |
| `small_packet_ratio` high | frame aggregation, delayed flush, heartbeat coalescing | traffic scheduler |
| `avg_inter_arrival_ms` stable | jitter, timer randomization | scheduler |
| `probe_response_variance` high | unified timeout, silent drop, constant close policy | server core, frame decoder |
| `malformed_close_time_variance` high | unified close delay | server core |

### 2Encapsulated TLS Fingerprint

| Failed Metric | Recommended Changes | Affected Layers |
|---|---|---|
| `dominant_ngram_ratio` high | split stable chunks, randomized padding, scheduler shuffle | traffic shaper, transport adapter |
| `ngram_entropy` low | randomized chunk size, dummy frames, multiplex scheduling | traffic shaper |
| `max_burst_size` high | fragmentation, pacing, burst smoothing | scheduler |
| `dominant_burst_direction_ratio` high | bidirectional pacing, reverse dummy traffic | scheduler |

### 3CalcuLatency / 4Cross-layer RTT

| Failed Metric | Recommended Changes | Affected Layers |
|---|---|---|
| `rtt_diff_ms` high | RTT-aware pacing, latency budget report | scheduler, evaluation |

## 8. PatchLoop CLI Usage

```bash
python3 -m src.llm.detection.patch_loop \
  --user-request "reduce fingerprint risk of idle TCP/TLS/WebSocket transport behavior" \
  --functional-test-summary "compileall passed; pytest passed" \
  --report traces/tcp/idle.report.json \
  --report traces/tls/idle.report.json \
  --report traces/websocket/idle.report.json \
  --max-risk-score 0.70 \
  --output-prompt /tmp/next_patch_prompt.txt \
  --output-json /tmp/patch_loop_result.json
```

**Exit codes:**
- `0` — all gates passed (no patch needed)
- `1` — some gates failed, prompt generated
- `2` — input error

## 9. How to Feed the Prompt to scripts/llm_task.py

```bash
# Step 1: Run detection gate, generate prompt
python3 -m src.llm.detection.patch_loop \
  --user-request "reduce fingerprint risk ..." \
  --report traces/tcp/idle.report.json \
  --output-prompt /tmp/next_patch_prompt.txt

# Step 2: Feed the prompt to llm_task.py as the request
python3 scripts/llm_task.py \
  --request "$(cat /tmp/next_patch_prompt.txt)" \
  --use-llm-planner \
  --generate-patch

# Or use the prompt directly with an external LLM
```

## 10. Integration with Future Traffic Shaper (Phase 5)

Phase 5 will let the LLM generate actual traffic shaper code based on the
adversarial patch prompt:

1. LLM receives prompt with specific metric failures
2. LLM generates `src/shaping/*.py` files (allowed by ImpactExpander)
3. Re-run fingerprint capture → gate evaluation → verify improvement
4. Iterate until all gates pass

## 11. Relationship to Detection Papers

| Paper | Gate Metrics |
|---|---|
| **1OpenVPN-style** | `repeated_length_ratio`, `small_packet_ratio`, `avg_inter_arrival_ms`, `probe_response_variance`, `malformed_close_time_variance` |
| **2Encapsulated TLS** | `ngram_entropy`, `dominant_ngram_ratio`, `max_burst_size`, `dominant_burst_direction_ratio` |
| **3CalcuLatency** | `rtt_diff_ms` |
| **4Cross-layer RTT** | `avg_inter_arrival_ms`, `rtt_diff_ms` |

## 12. Safety Boundaries

- Only for local, controlled experiment use
- Do NOT generate code that scans, attacks, or probes third-party hosts
- Do NOT claim real undetectability
- All patches must pass compileall, pytest, and detection gate
- Preserve core/transport interface unless explicitly requested otherwise
- No modification of .env, .git/, .claude/, *.key, *.pem, config/llm_agent.yaml

## 13. Current Limitations

1. **No real LLM call yet** — Phase 4 only generates the prompt; actual LLM call
   is planned for Phase 5 when `scripts/llm_task.py` integration is complete
2. **Thresholds are preliminary** — need calibration with more real trace samples
3. **Not a general proxy detector** — only works with VPN-LLM's own traffic
4. **No before/after auto-comparison** — manual re-run required
5. **Single iteration only** — `max_iterations=1` by default
6. **SSH transport not yet collectable** — server-side not integrated

## 14. Next Steps (Phase 5)

1. Feed the adversarial patch prompt to an LLM (via `scripts/llm_task.py`)
2. Let the LLM generate the first `src/shaping/` traffic shaper code
3. Re-run fingerprint capture → gate → verify improvement
4. Implement feedback loop: iterate until all gates pass
5. Calibrate thresholds against more real-world trace samples
