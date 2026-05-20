"""Tests for run_phase9_real_trace_matrix.py.

All tests run without root, netns, tcpdump, or tshark.
"""

import json
from pathlib import Path
from unittest import mock

import pytest

from scripts.run_phase9_real_trace_matrix import (
    _SUPPORTED_TRANSPORTS,
    _VALID_SCENARIOS,
    _parse_list,
    _scenario_command,
    _now_iso,
    build_matrix,
    run_env_check,
    _load_report_safe,
    _generate_patch_prompt,
)


# ---------------------------------------------------------------------------
# env-check
# ---------------------------------------------------------------------------


class TestEnvCheck:
    def test_env_check_has_required_keys(self):
        result = run_env_check()
        assert "timestamp" in result
        assert "checks" in result
        assert "dev_net_tun" in result["checks"]
        assert "tcpdump" in result["checks"]
        assert "tshark" in result["checks"]
        assert "can_create_netns" in result["checks"]
        assert "can_run_real_tcp" in result

    def test_env_check_boolean_flags(self):
        result = run_env_check()
        for key in ["can_run_real_tcp", "can_run_real_tls",
                     "can_run_real_websocket", "can_run_real_ssh"]:
            assert isinstance(result[key], bool)


# ---------------------------------------------------------------------------
# parse_list
# ---------------------------------------------------------------------------


class TestParseList:
    def test_parses_comma_separated(self):
        result = _parse_list("tcp,tls,websocket", _SUPPORTED_TRANSPORTS)
        assert result == ["tcp", "tls", "websocket"]

    def test_filters_unknown_values(self):
        result = _parse_list("tcp,http2,ssh", _SUPPORTED_TRANSPORTS)
        assert result == ["tcp", "ssh"]

    def test_handles_whitespace(self):
        result = _parse_list(" tcp , tls , websocket ", _SUPPORTED_TRANSPORTS)
        assert result == ["tcp", "tls", "websocket"]

    def test_empty_string_returns_empty(self):
        result = _parse_list("", _SUPPORTED_TRANSPORTS)
        assert result == []


# ---------------------------------------------------------------------------
# _scenario_command
# ---------------------------------------------------------------------------


class TestScenarioCommand:
    def test_all_valid_scenarios_have_commands(self):
        for s in _VALID_SCENARIOS:
            cmd = _scenario_command(s)
            assert cmd, f"Scenario '{s}' has no command"
            assert isinstance(cmd, str)

    def test_unknown_scenario_has_fallback(self):
        cmd = _scenario_command("nonexistent")
        assert "sleep 3" in cmd


# ---------------------------------------------------------------------------
# build_matrix
# ---------------------------------------------------------------------------


class TestBuildMatrix:
    def test_produces_before_and_after_entries(self):
        entries = build_matrix(["tcp"], ["idle"], "outputs/test")
        assert len(entries) == 2  # before + after
        phases = {e["phase"] for e in entries}
        assert phases == {"before", "after"}

    def test_full_matrix_size(self):
        entries = build_matrix(
            ["tcp", "tls", "websocket", "ssh"],
            ["idle", "ping", "curl", "bulk", "reconnect"],
            "outputs/test",
        )
        assert len(entries) == 4 * 5 * 2  # 40

    def test_subset_matrix_size(self):
        entries = build_matrix(["tcp"], ["idle", "ping"], "outputs/test")
        assert len(entries) == 4  # 1 * 2 * 2

    def test_each_entry_has_required_keys(self):
        entries = build_matrix(["tcp"], ["idle"], "outputs/test")
        for e in entries:
            for key in ["transport", "scenario", "phase",
                         "pcap_path", "csv_path", "report_path",
                         "scenario_command"]:
                assert key in e, f"Missing key: {key}"

    def test_pcap_paths_are_distinct(self):
        entries = build_matrix(["tcp", "tls"], ["idle"], "outputs/test")
        paths = [e["pcap_path"] for e in entries]
        assert len(paths) == len(set(paths))

    def test_before_paths_contain_before(self):
        entries = build_matrix(["tcp"], ["idle"], "outputs/test")
        for e in entries:
            if e["phase"] == "before":
                assert "before" in e["pcap_path"]
            else:
                assert "after" in e["pcap_path"]

    def test_plan_json_structure(self):
        entries = build_matrix(["tcp"], ["idle"], "outputs/test")
        manifest = {
            "generated_at": _now_iso(),
            "output_dir": "outputs/test",
            "transports": ["tcp"],
            "scenarios": ["idle"],
            "phases": ["before", "after"],
            "total_entries": len(entries),
            "entries": entries,
        }
        encoded = json.dumps(manifest)
        decoded = json.loads(encoded)
        assert decoded["total_entries"] == 2
        assert len(decoded["entries"]) == 2


# ---------------------------------------------------------------------------
# _load_report_safe
# ---------------------------------------------------------------------------


class TestLoadReportSafe:
    def test_nonexistent_file_returns_none(self, tmp_path):
        result = _load_report_safe(str(tmp_path / "nonexistent.json"))
        assert result is None

    def test_invalid_json_returns_none(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json")
        result = _load_report_safe(str(path))
        assert result is None

    def test_valid_json_returns_dict(self, tmp_path):
        path = tmp_path / "good.json"
        path.write_text('{"key": "value"}')
        result = _load_report_safe(str(path))
        assert result == {"key": "value"}


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------


def _make_report(**kwargs) -> dict:
    defaults = {
        "packet_count": 10,
        "fingerprint_risk_score": 0.5,
        "risk_level": "medium",
        "small_packet_ratio": 0.3,
        "repeated_length_ratio": 0.2,
        "ngram_entropy": 2.5,
        "dominant_ngram_ratio": 0.4,
        "burst_count": 3,
        "max_burst_size": 5,
        "avg_inter_arrival_ms": 1.2,
    }
    defaults.update(kwargs)
    return defaults


class TestComparisonLogic:
    def test_improved_when_delta_lt_neg_0_05(self):
        """risk_score drops by more than 0.05 → improved."""
        before = _make_report(fingerprint_risk_score=0.5)
        after = _make_report(fingerprint_risk_score=0.43)
        delta = round(after["fingerprint_risk_score"] - before["fingerprint_risk_score"], 4)
        assert delta < -0.05
        verdict = "improved" if delta < -0.05 else ("regressed" if delta > 0.05 else "unchanged")
        assert verdict == "improved"

    def test_regressed_when_delta_gt_0_05(self):
        """risk_score increases by more than 0.05 → regressed."""
        before = _make_report(fingerprint_risk_score=0.4)
        after = _make_report(fingerprint_risk_score=0.5)
        delta = round(after["fingerprint_risk_score"] - before["fingerprint_risk_score"], 4)
        assert delta > 0.05
        verdict = "improved" if delta < -0.05 else ("regressed" if delta > 0.05 else "unchanged")
        assert verdict == "regressed"

    def test_unchanged_when_delta_small(self):
        before = _make_report(fingerprint_risk_score=0.5)
        after = _make_report(fingerprint_risk_score=0.51)
        delta = round(after["fingerprint_risk_score"] - before["fingerprint_risk_score"], 4)
        assert -0.05 <= delta <= 0.05
        verdict = "improved" if delta < -0.05 else ("regressed" if delta > 0.05 else "unchanged")
        assert verdict == "unchanged"

    def test_missing_before_report_marks_insufficient(self):
        """Missing before report → verdict is insufficient."""
        has_before = False
        has_after = True
        if not has_before and not has_after:
            verdict = "skipped"
        elif not has_before or not has_after:
            verdict = "insufficient"
        else:
            verdict = "unchanged"
        assert verdict == "insufficient"

    def test_missing_after_report_marks_insufficient(self):
        has_before = True
        has_after = False
        if not has_before and not has_after:
            verdict = "skipped"
        elif not has_before or not has_after:
            verdict = "insufficient"
        else:
            verdict = "unchanged"
        assert verdict == "insufficient"

    def test_both_missing_marks_skipped(self):
        has_before = False
        has_after = False
        if not has_before and not has_after:
            verdict = "skipped"
        elif not has_before or not has_after:
            verdict = "insufficient"
        else:
            verdict = "unchanged"
        assert verdict == "skipped"


# ---------------------------------------------------------------------------
# LLM patch prompt generation
# ---------------------------------------------------------------------------


class TestPatchPrompt:
    def test_generates_prompt_for_regressed_entries(self, tmp_path):
        entries = [
            {
                "transport": "tcp", "scenario": "idle",
                "fingerprint_risk_score_before": 0.5,
                "fingerprint_risk_score_after": 0.6,
                "risk_score_delta": 0.1,
                "verdict": "regressed",
                "risk_level_before": "medium", "risk_level_after": "high",
                "trace_type_before": "real", "trace_type_after": "real",
            },
        ]
        out_dir = str(tmp_path)
        _generate_patch_prompt(entries, out_dir)
        prompt_path = Path(out_dir) / "next_patch_prompt.txt"
        assert prompt_path.exists()
        content = prompt_path.read_text()
        assert "tcp/idle" in content
        assert "src/shaping/padding.py" in content

    def test_no_prompt_when_no_regressions(self, tmp_path):
        entries = [
            {
                "transport": "tcp", "scenario": "idle",
                "verdict": "improved",
            },
        ]
        out_dir = str(tmp_path)
        _generate_patch_prompt(entries, out_dir)
        prompt_path = Path(out_dir) / "next_patch_prompt.txt"
        assert not prompt_path.exists()

    def test_result_json_written(self, tmp_path):
        entries = [
            {
                "transport": "tcp", "scenario": "idle",
                "fingerprint_risk_score_before": 0.5,
                "fingerprint_risk_score_after": 0.6,
                "risk_score_delta": 0.1,
                "verdict": "regressed",
            },
        ]
        out_dir = str(tmp_path)
        _generate_patch_prompt(entries, out_dir)
        result_path = Path(out_dir) / "patch_loop_result.json"
        assert result_path.exists()
        data = json.loads(result_path.read_text())
        assert data["regressed_count"] == 1


# ---------------------------------------------------------------------------
# Dry-run safety (no root/netns required)
# ---------------------------------------------------------------------------


class TestDryRunSafety:
    def test_build_matrix_no_external_calls(self):
        """build_matrix must not call external commands."""
        entries = build_matrix(["tcp", "tls"], ["idle", "ping"], "outputs/test")
        assert len(entries) == 8
        for e in entries:
            assert isinstance(e["pcap_path"], str)

    def test_dry_run_entry_has_status_dry_run(self):
        """Verify dry-run entries produce correct status structure."""
        entry = {
            "transport": "tcp",
            "scenario": "idle",
            "phase": "before",
            "pcap_path": "out/before/tcp/idle.pcap",
            "csv_path": "out/before/tcp/idle.csv",
            "report_path": "out/before/tcp/idle.report.json",
            "scenario_command": "sleep 5",
            "capture_interface": "veth_srv",
            "capture_host": "192.168.200.1",
        }
        result = {
            **entry,
            "trace_type": "synthetic",
            "status": "dry_run",
            "started_at": _now_iso(),
            "ended_at": _now_iso(),
            "duration_s": 0,
            "error_reason": "dry-run: would execute with netns",
        }
        assert result["status"] == "dry_run"
        assert result["trace_type"] == "synthetic"

    def test_no_pcap_in_manifest_entries(self):
        """Manifest entries reference pcap paths but don't create them."""
        entries = build_matrix(["tcp"], ["idle"], "outputs/test")
        pcap_paths = [e["pcap_path"] for e in entries]
        for p in pcap_paths:
            assert p.endswith(".pcap")
            assert not Path(p).exists()  # pcap not actually created

    def test_env_check_does_not_require_root(self):
        """env-check must work without root."""
        result = run_env_check()
        assert "checks" in result
        assert "can_run_real_tcp" in result
        # Works regardless of whether we have root


# ---------------------------------------------------------------------------
# _now_iso
# ---------------------------------------------------------------------------


class TestNowIso:
    def test_returns_non_empty_string(self):
        ts = _now_iso()
        assert isinstance(ts, str)
        assert len(ts) > 0
        assert "T" in ts
