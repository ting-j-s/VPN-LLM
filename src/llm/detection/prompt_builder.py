"""PromptBuilder — build adversarial patch prompts for LLM consumption.

Takes detection failures and countermeasure hints and produces a complete
prompt that can be handed to scripts/llm_task.py or any external LLM.
"""

from __future__ import annotations

from .detector_report import DetectionReport
from .countermeasure_policy import CountermeasureHint

_PATCH_PROTOCOL = """FILE: <path>
ACTION: replace
<<<FIND
exact lines to find in the file (must match exactly, including whitespace)
<<<REPLACE
replacement lines

FILE: <path>
ACTION: create
<<<CONTENT
full file content
>>>"""

_SAFETY_BOUNDARY = """CRITICAL SAFETY BOUNDARIES:
- This is for LOCAL, controlled experiment use ONLY.
- Do NOT generate code that scans, attacks, or probes third-party hosts.
- Do NOT generate code that blocks, drops, or interferes with third-party traffic.
- Do NOT claim that the resulting system is undetectable by real DPI.
- Do NOT modify .env, .git/, .claude/, *.key, *.pem, or config/llm_agent.yaml.
- All patches must pass compileall, pytest, and detection gate before being accepted.
- Preserve core/transport interface compatibility unless the user request explicitly requires interface changes."""


def _format_metric(metric) -> str:
    """Format a single detection metric for prompt inclusion."""
    status = "PASS" if metric.passed else f"FAIL ({metric.severity})"
    threshold_str = f" (threshold: {metric.threshold})" if metric.threshold is not None else ""
    return (f"- [{status}] **{metric.name}**: {metric.value}{threshold_str}\n"
            f"  {metric.explanation}")


def _format_hint(hint: CountermeasureHint) -> str:
    """Format a countermeasure hint for prompt inclusion."""
    lines = [
        f"### {hint.metric_name}",
        f"**Problem**: {hint.problem}",
        "",
        "**Recommended changes**:",
    ]
    for ch in hint.recommended_changes:
        lines.append(f"  - {ch}")
    lines.append("")
    lines.append(f"**Affected layers**: {', '.join(hint.affected_layers)}")
    if hint.tradeoffs:
        lines.append("")
        lines.append("**Tradeoffs**:")
        for t in hint.tradeoffs:
            lines.append(f"  - {t}")
    if hint.avoid:
        lines.append("")
        lines.append("**Avoid**:")
        for a in hint.avoid:
            lines.append(f"  - {a}")
    return "\n".join(lines)


def build_adversarial_patch_prompt(
    user_request: str,
    repo_status: str,
    functional_test_summary: str,
    detection_report: DetectionReport,
    countermeasure_hints: list[CountermeasureHint],
    patch_protocol: str | None = None,
) -> str:
    """Build a complete adversarial patch prompt for LLM consumption.

    Args:
        user_request: Original user request (e.g. "reduce fingerprint risk").
        repo_status: Git status / repository state summary.
        functional_test_summary: Result of compileall + pytest.
        detection_report: Evaluated DetectionReport with failed metrics.
        countermeasure_hints: CountermeasureHint list from suggest_countermeasures().
        patch_protocol: Protocol string (defaults to FILE/ACTION format).

    Returns:
        A complete prompt string ready for LLM input.
    """
    if patch_protocol is None:
        patch_protocol = _PATCH_PROTOCOL

    lines = []

    # --- Header ---
    lines.append("# Adversarial Patch Request — Detection Gate Failure")
    lines.append("")
    lines.append("The VPN-LLM traffic has FAILED the detection gate evaluation.")
    lines.append("You MUST generate a patch to reduce the fingerprint risk.")
    lines.append("")

    # --- 1. User request ---
    lines.append("## 1. User Request")
    lines.append("")
    lines.append(user_request)
    lines.append("")

    # --- 2. Repository status ---
    lines.append("## 2. Repository Status")
    lines.append("")
    lines.append(repo_status)
    lines.append("")

    # --- 3. Functional test summary ---
    lines.append("## 3. Functional Test Results")
    lines.append("")
    lines.append(functional_test_summary)
    lines.append("")

    # --- 4. Detection report summary ---
    lines.append("## 4. Detection Gate Results")
    lines.append("")
    gate_status = "PASS" if detection_report.passed else "FAIL"
    lines.append(f"- **Overall**: {gate_status}")
    lines.append(f"- **Detector**: {detection_report.detector_name}")
    lines.append(f"- **Transport**: {detection_report.transport or 'N/A'}")
    lines.append(f"- **Scenario**: {detection_report.scenario or 'N/A'}")
    lines.append(f"- **Trace type**: {detection_report.trace_type}")
    if detection_report.risk_score is not None:
        lines.append(f"- **Risk score**: {detection_report.risk_score}")
    if detection_report.risk_level is not None:
        lines.append(f"- **Risk level**: {detection_report.risk_level}")
    lines.append("")

    # --- 5. Failed metrics ---
    lines.append("### Failed Metrics")
    lines.append("")
    failed_metrics = [m for m in detection_report.metrics if not m.passed]
    if failed_metrics:
        for m in failed_metrics:
            lines.append(_format_metric(m))
            lines.append("")
    else:
        lines.append("_(no specific metric failures detected)_")
        lines.append("")

    # --- 5b. All metrics for context ---
    lines.append("### All Metrics (for context)")
    lines.append("")
    for m in detection_report.metrics:
        if m.passed:
            lines.append(f"- [PASS] **{m.name}**: {m.value}")
    lines.append("")

    # --- 5c. Detection paper mapping ---
    if failed_metrics:
        lines.append("### Which Detection Papers Does This Relate To?")
        lines.append("")
        paper_map = _build_paper_mapping(failed_metrics)
        for paper, metrics_list in paper_map.items():
            lines.append(f"- **{paper}**: {', '.join(metrics_list)}")
        lines.append("")

    # --- 6. Countermeasure hints ---
    if countermeasure_hints:
        lines.append("## 5. Countermeasure Directions")
        lines.append("")
        lines.append("The following countermeasure directions are recommended based on "
                     "the failed metrics. Use these as guidance for your patch design.")
        lines.append("")
        for hint in countermeasure_hints:
            lines.append(_format_hint(hint))
            lines.append("")
    else:
        lines.append("## 5. Countermeasure Directions")
        lines.append("")
        lines.append("_(no countermeasure hints available — all metrics passed)_")
        lines.append("")

    # --- 7. Patch requirements ---
    lines.append("## 6. Patch Requirements")
    lines.append("")
    lines.append("### What You MUST Do")
    lines.append("")
    lines.append("1. Generate FILE/ACTION edit blocks that implement countermeasures "
                 "to reduce the failed detection metrics.")
    lines.append("2. Create or update tests that verify the countermeasures work correctly.")
    lines.append("3. Create or update documentation describing the changes made.")
    lines.append("4. Preserve core/transport interface compatibility unless the user "
                 "request explicitly requires interface changes.")
    lines.append("5. Run compileall, pytest, and re-run the detection gate after applying "
                 "your patch to verify improvement.")
    lines.append("")

    # --- 8. Patch protocol ---
    lines.append("## 7. Patch Protocol (MUST FOLLOW EXACTLY)")
    lines.append("")
    lines.append("```")
    lines.append(patch_protocol)
    lines.append("```")
    lines.append("")

    # --- 9. Safety boundaries ---
    lines.append("## 8. Safety Boundaries")
    lines.append("")
    lines.append(_SAFETY_BOUNDARY)
    lines.append("")

    # --- 10. Output instruction ---
    lines.append("## 9. Output Instruction")
    lines.append("")
    lines.append("Generate the FILE/ACTION edit blocks now. Output ONLY edit blocks. "
                 "Start immediately with 'FILE: <path>'. No prose before or after. "
                 "No markdown fences. No reasoning.")
    lines.append("")

    return "\n".join(lines)


def _build_paper_mapping(metrics: list) -> dict[str, list[str]]:
    """Map failed metrics to which detection paper families they relate to."""
    mapping: dict[str, set[str]] = {}

    openvpn_metrics = {
        "repeated_length_ratio", "small_packet_ratio", "avg_inter_arrival_ms",
        "probe_response_variance", "malformed_close_time_variance",
    }
    enc_tls_metrics = {
        "ngram_entropy", "dominant_ngram_ratio", "max_burst_size",
        "dominant_burst_direction_ratio",
    }
    calcu_latency_metrics = {"rtt_diff_ms"}
    cross_layer_metrics = {"avg_inter_arrival_ms", "rtt_diff_ms"}

    for m in metrics:
        name = getattr(m, "name", "") if hasattr(m, "name") else str(m)
        if name in openvpn_metrics:
            mapping.setdefault("1OpenVPN Fingerprint", set()).add(name)
        if name in enc_tls_metrics:
            mapping.setdefault("2Encapsulated TLS", set()).add(name)
        if name in calcu_latency_metrics:
            mapping.setdefault("3CalcuLatency", set()).add(name)
        if name in cross_layer_metrics:
            mapping.setdefault("4Cross-layer RTT", set()).add(name)

    return {k: sorted(v) for k, v in mapping.items()}
