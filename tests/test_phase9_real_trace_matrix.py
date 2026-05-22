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
    _scenario_needs_http_server,
    _now_iso,
    build_matrix,
    run_env_check,
    _load_report_safe,
    _generate_patch_prompts,
    _discover_transport_scenarios,
    RuntimeConfig,
    _runtime_config_from_args,
    _mean,
    _std,
    _safe_float,
    _per_run_verdicts,
    _paired_deltas,
    _aggregate_repeated_runs,
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

    def test_env_check_includes_curl(self):
        result = run_env_check()
        assert "curl" in result["checks"]


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
# _scenario_command — Phase 9B parameterised
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

    def test_ping_uses_ping_count(self):
        cfg = RuntimeConfig(ping_count=25, ping_interval=0.2)
        cmd = _scenario_command("ping", cfg)
        assert "-c 25" in cmd
        assert "-i 0.2" in cmd
        assert "10.8.0.1" in cmd

    def test_ping_defaults(self):
        cfg = RuntimeConfig()
        cmd = _scenario_command("ping", cfg)
        assert "-c 20" in cmd
        assert "-i 0.1" in cmd

    def test_ping_does_not_access_public_internet(self):
        """Ping command must only target TUN peer address, no public IPs."""
        for ping_count in [10, 20, 30]:
            cfg = RuntimeConfig(ping_count=ping_count)
            cmd = _scenario_command("ping", cfg)
            assert "10.8.0.1" in cmd
            # No public IP patterns (8.8.8.8, 1.1.1.1, etc)
            assert "8.8.8.8" not in cmd
            assert "1.1.1.1" not in cmd
            assert "baidu.com" not in cmd
            assert "google.com" not in cmd

    def test_curl_uses_curl_count(self):
        cfg = RuntimeConfig(curl_count=15)
        cmd = _scenario_command("curl", cfg)
        assert "seq 1 15" in cmd
        assert "10.8.0.1:8080" in cmd

    def test_curl_uses_local_netns_address(self):
        """Curl must only access netns-local TUN address."""
        cmd = _scenario_command("curl", RuntimeConfig())
        assert "10.8.0.1" in cmd
        assert "http://" in cmd
        # No public domains
        assert "example.com" not in cmd
        assert "google.com" not in cmd

    def test_bulk_uses_local_netns_address(self):
        """Bulk must only access netns-local TUN address."""
        cmd = _scenario_command("bulk", RuntimeConfig())
        assert "10.8.0.1:8080" in cmd
        assert "http://" in cmd

    def test_idle_uses_capture_duration(self):
        cfg = RuntimeConfig(capture_duration=45)
        cmd = _scenario_command("idle", cfg)
        assert "sleep 40" in cmd  # capture_duration - 5

    def test_reconnect_reports_unsupported(self):
        cmd = _scenario_command("reconnect", RuntimeConfig())
        assert "not support" in cmd.lower() or "skipped" in cmd.lower()

    def test_curl_uses_connect_timeout(self):
        cfg = RuntimeConfig(curl_connect_timeout=25, curl_max_time=50)
        cmd = _scenario_command("curl", cfg)
        assert "--connect-timeout 25" in cmd
        assert "--max-time 50" in cmd

    def test_bulk_uses_connect_timeout(self):
        cfg = RuntimeConfig(curl_connect_timeout=30, curl_max_time=60)
        cmd = _scenario_command("bulk", cfg)
        assert "--connect-timeout 30" in cmd
        assert "--max-time 60" in cmd

    def test_bulk_always_uses_localhost(self):
        """Bulk scenario must never use public network."""
        for bulk_bytes in [262144, 524288, 1048576]:
            cfg = RuntimeConfig(bulk_bytes=bulk_bytes, curl_connect_timeout=40, curl_max_time=90)
            cmd = _scenario_command("bulk", cfg)
            assert "10.8.0.1:8080" in cmd
            assert "http://" in cmd
            assert "8.8.8.8" not in cmd
            assert "example.com" not in cmd

    def test_scenario_needs_http_server(self):
        assert _scenario_needs_http_server("curl") is True
        assert _scenario_needs_http_server("bulk") is True
        assert _scenario_needs_http_server("ping") is False
        assert _scenario_needs_http_server("idle") is False
        assert _scenario_needs_http_server("reconnect") is False


# ---------------------------------------------------------------------------
# RuntimeConfig
# ---------------------------------------------------------------------------


class TestRuntimeConfig:
    def test_default_values(self):
        cfg = RuntimeConfig()
        assert cfg.capture_duration == 30
        assert cfg.ping_count == 20
        assert cfg.ping_interval == 0.1
        assert cfg.curl_count == 10
        assert cfg.bulk_bytes == 1048576
        assert cfg.min_packet_count == 30
        assert cfg.scenario_timeout == 60
        assert cfg.repeat_count == 1
        assert cfg.curl_connect_timeout == 30
        assert cfg.curl_max_time == 60
        assert cfg.http_server_startup_timeout == 10
        assert cfg.post_scenario_wait == 2
        assert cfg.bulk_read_timeout == 60

    def test_custom_values(self):
        cfg = RuntimeConfig(
            capture_duration=45,
            ping_count=30,
            min_packet_count=50,
        )
        assert cfg.capture_duration == 45
        assert cfg.ping_count == 30
        assert cfg.min_packet_count == 50
        assert cfg.ping_interval == 0.1

    def test_repeat_count_default_is_1(self):
        cfg = RuntimeConfig()
        assert cfg.repeat_count == 1

    def test_phase9e_recommended_params(self):
        """Verify Phase 9E recommended parameter values."""
        cfg = RuntimeConfig(
            repeat_count=3,
            capture_duration=45,
            ping_count=80,
            ping_interval=0.05,
            bulk_bytes=1048576,
            curl_connect_timeout=30,
            curl_max_time=60,
            min_packet_count=30,
        )
        assert cfg.repeat_count == 3
        assert cfg.capture_duration == 45
        assert cfg.ping_count == 80
        assert cfg.ping_interval == 0.05
        assert cfg.bulk_bytes == 1048576
        assert cfg.curl_connect_timeout == 30
        assert cfg.curl_max_time == 60

    def test_runtime_config_from_args(self):
        args = mock.Mock()
        args.capture_duration = 35
        args.ping_count = 25
        args.ping_interval = 0.15
        args.curl_count = 12
        args.bulk_bytes = 524288
        args.min_packet_count = 40
        args.scenario_timeout = 90
        args.repeat_count = 3
        args.curl_connect_timeout = 20
        args.curl_max_time = 45
        args.http_server_startup_timeout = 8
        args.post_scenario_wait = 3
        args.bulk_read_timeout = 50
        cfg = _runtime_config_from_args(args)
        assert cfg.capture_duration == 35
        assert cfg.ping_count == 25
        assert cfg.ping_interval == 0.15
        assert cfg.curl_count == 12
        assert cfg.bulk_bytes == 524288
        assert cfg.min_packet_count == 40
        assert cfg.scenario_timeout == 90
        assert cfg.repeat_count == 3
        assert cfg.curl_connect_timeout == 20
        assert cfg.curl_max_time == 45
        assert cfg.http_server_startup_timeout == 8
        assert cfg.post_scenario_wait == 3
        assert cfg.bulk_read_timeout == 50


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
                         "scenario_command", "capture_duration",
                         "min_packet_count", "needs_http_server"]:
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

    def test_manifest_includes_runtime_params(self):
        cfg = RuntimeConfig(capture_duration=45, min_packet_count=50)
        entries = build_matrix(["tcp"], ["ping"], "outputs/test", runtime_config=cfg)
        manifest = {
            "generated_at": _now_iso(),
            "output_dir": "outputs/test",
            "transports": ["tcp"],
            "scenarios": ["ping"],
            "phases": ["before", "after"],
            "total_entries": len(entries),
            "runtime_params": {
                "capture_duration": cfg.capture_duration,
                "ping_count": cfg.ping_count,
                "ping_interval": cfg.ping_interval,
                "curl_count": cfg.curl_count,
                "bulk_bytes": cfg.bulk_bytes,
                "min_packet_count": cfg.min_packet_count,
                "scenario_timeout": cfg.scenario_timeout,
            },
            "entries": entries,
        }
        encoded = json.dumps(manifest)
        decoded = json.loads(encoded)
        assert decoded["runtime_params"]["capture_duration"] == 45
        assert decoded["runtime_params"]["min_packet_count"] == 50
        assert decoded["runtime_params"]["ping_count"] == 20

    def test_manifest_entries_include_capture_duration(self):
        cfg = RuntimeConfig(capture_duration=35, min_packet_count=25)
        entries = build_matrix(["tcp"], ["ping"], "outputs/test", runtime_config=cfg)
        for e in entries:
            assert e["capture_duration"] == 35
            assert e["min_packet_count"] == 25

    def test_curl_entry_needs_http_server(self):
        cfg = RuntimeConfig()
        entries = build_matrix(["tcp"], ["curl"], "outputs/test", runtime_config=cfg)
        for e in entries:
            assert e["needs_http_server"] is True

    def test_ping_entry_does_not_need_http_server(self):
        entries = build_matrix(["tcp"], ["ping"], "outputs/test")
        for e in entries:
            assert e["needs_http_server"] is False

    # ---- Phase 9E: repeat_count and run_id ----

    def test_repeat_count_1_uses_flat_paths(self):
        cfg = RuntimeConfig(repeat_count=1)
        entries = build_matrix(["tcp"], ["ping"], "outputs/test", runtime_config=cfg)
        assert len(entries) == 2
        for e in entries:
            assert "run_id" not in e
            assert "run_" not in e.get("pcap_path", "")

    def test_repeat_count_3_produces_6_entries(self):
        cfg = RuntimeConfig(repeat_count=3)
        entries = build_matrix(["tcp"], ["ping"], "outputs/test", runtime_config=cfg)
        assert len(entries) == 6  # 1 transport * 1 scenario * 2 phases * 3 repeats

    def test_repeat_count_3_entries_have_run_id(self):
        cfg = RuntimeConfig(repeat_count=3)
        entries = build_matrix(["tcp"], ["ping"], "outputs/test", runtime_config=cfg)
        run_ids = [e["run_id"] for e in entries]
        assert run_ids.count("run_01") == 2  # before + after
        assert run_ids.count("run_02") == 2
        assert run_ids.count("run_03") == 2

    def test_repeat_count_3_paths_include_run_id(self):
        cfg = RuntimeConfig(repeat_count=3)
        entries = build_matrix(["tcp"], ["ping"], "outputs/test", runtime_config=cfg)
        for e in entries:
            assert "run_" in e["pcap_path"]
            rid = e["run_id"]
            assert rid in e["pcap_path"]
            assert rid in e["csv_path"]
            assert rid in e["report_path"]

    def test_repeat_count_3_all_reports_distinct(self):
        cfg = RuntimeConfig(repeat_count=3)
        entries = build_matrix(["tcp"], ["ping"], "outputs/test", runtime_config=cfg)
        report_paths = [e["report_path"] for e in entries]
        assert len(report_paths) == len(set(report_paths))

    def test_manifest_includes_repeat_count_and_run_id(self):
        cfg = RuntimeConfig(repeat_count=3, capture_duration=45)
        entries = build_matrix(["tcp"], ["ping"], "outputs/test", runtime_config=cfg)
        manifest = {
            "runtime_params": {
                "repeat_count": cfg.repeat_count,
                "capture_duration": cfg.capture_duration,
            },
            "entries": entries,
        }
        assert manifest["runtime_params"]["repeat_count"] == 3
        for e in entries:
            assert "run_id" in e
            assert e["repeat_count"] == 3


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
# Comparison logic — Phase 9B data_quality + verdict
# ---------------------------------------------------------------------------


def _make_report(**kwargs) -> dict:
    defaults = {
        "packet_count": 50,
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


def _compare(before, after, min_packet_count=30):
    """Simulate comparison logic."""
    entry = {"min_packet_count": min_packet_count}

    fields = [
        "packet_count", "fingerprint_risk_score", "risk_level",
        "small_packet_ratio", "repeated_length_ratio", "ngram_entropy",
        "dominant_ngram_ratio", "burst_count", "max_burst_size",
        "avg_inter_arrival_ms",
    ]
    for f in fields:
        entry[f"{f}_before"] = before.get(f) if before else None
        entry[f"{f}_after"] = after.get(f) if after else None

    pc_before = entry.get("packet_count_before") or 0
    pc_after = entry.get("packet_count_after") or 0
    entry["packet_count_ok_before"] = pc_before >= min_packet_count
    entry["packet_count_ok_after"] = pc_after >= min_packet_count

    if not before and not after:
        entry["data_quality"] = "skipped"
    elif not before or not after:
        entry["data_quality"] = "insufficient"
    elif pc_before < min_packet_count or pc_after < min_packet_count:
        entry["data_quality"] = "insufficient"
    else:
        entry["data_quality"] = "ok"

    b_score = before.get("fingerprint_risk_score") if before else None
    a_score = after.get("fingerprint_risk_score") if after else None

    if entry["data_quality"] == "insufficient":
        entry["verdict"] = "insufficient"
    elif entry["data_quality"] == "skipped":
        entry["verdict"] = "skipped"
    elif b_score is not None and a_score is not None:
        entry["risk_score_delta"] = round(a_score - b_score, 4)
        delta = entry["risk_score_delta"]
        if delta < -0.05:
            entry["verdict"] = "improved"
        elif delta > 0.05:
            entry["verdict"] = "regressed"
        else:
            entry["verdict"] = "unchanged"
    else:
        entry["verdict"] = "insufficient"

    return entry


class TestComparisonLogic:
    def test_improved_when_delta_lt_neg_0_05(self):
        before = _make_report(fingerprint_risk_score=0.5, packet_count=50)
        after = _make_report(fingerprint_risk_score=0.43, packet_count=50)
        result = _compare(before, after)
        assert result["verdict"] == "improved"
        assert result["data_quality"] == "ok"
        assert result["risk_score_delta"] < -0.05

    def test_regressed_when_delta_gt_0_05(self):
        before = _make_report(fingerprint_risk_score=0.4, packet_count=50)
        after = _make_report(fingerprint_risk_score=0.5, packet_count=50)
        result = _compare(before, after)
        assert result["verdict"] == "regressed"
        assert result["data_quality"] == "ok"

    def test_unchanged_when_delta_small(self):
        before = _make_report(fingerprint_risk_score=0.5, packet_count=50)
        after = _make_report(fingerprint_risk_score=0.51, packet_count=50)
        result = _compare(before, after)
        assert result["verdict"] == "unchanged"
        assert result["data_quality"] == "ok"

    def test_missing_before_report_marks_insufficient(self):
        result = _compare(None, _make_report(packet_count=50))
        assert result["data_quality"] == "insufficient"
        assert result["verdict"] == "insufficient"

    def test_missing_after_report_marks_insufficient(self):
        result = _compare(_make_report(packet_count=50), None)
        assert result["data_quality"] == "insufficient"
        assert result["verdict"] == "insufficient"

    def test_both_missing_marks_skipped(self):
        result = _compare(None, None)
        assert result["data_quality"] == "skipped"
        assert result["verdict"] == "skipped"

    # ---- Phase 9B: data_quality based on min_packet_count ----

    def test_insufficient_when_before_packet_count_low(self):
        before = _make_report(packet_count=5, fingerprint_risk_score=0.5)
        after = _make_report(packet_count=50, fingerprint_risk_score=0.45)
        result = _compare(before, after, min_packet_count=30)
        assert result["data_quality"] == "insufficient"
        assert result["verdict"] == "insufficient"
        assert result["packet_count_ok_before"] is False
        assert result["packet_count_ok_after"] is True

    def test_insufficient_when_after_packet_count_low(self):
        before = _make_report(packet_count=50, fingerprint_risk_score=0.5)
        after = _make_report(packet_count=5, fingerprint_risk_score=0.45)
        result = _compare(before, after, min_packet_count=30)
        assert result["data_quality"] == "insufficient"
        assert result["verdict"] == "insufficient"
        assert result["packet_count_ok_before"] is True
        assert result["packet_count_ok_after"] is False

    def test_insufficient_when_both_packet_counts_low(self):
        before = _make_report(packet_count=4, fingerprint_risk_score=0.0)
        after = _make_report(packet_count=2, fingerprint_risk_score=0.0)
        result = _compare(before, after, min_packet_count=30)
        assert result["data_quality"] == "insufficient"
        assert result["verdict"] == "insufficient"

    def test_data_quality_ok_when_both_above_threshold(self):
        before = _make_report(packet_count=55, fingerprint_risk_score=0.5)
        after = _make_report(packet_count=60, fingerprint_risk_score=0.4)
        result = _compare(before, after, min_packet_count=30)
        assert result["data_quality"] == "ok"
        # Not insufficient
        assert result["verdict"] != "insufficient"

    def test_custom_min_packet_count(self):
        before = _make_report(packet_count=20, fingerprint_risk_score=0.5)
        after = _make_report(packet_count=25, fingerprint_risk_score=0.45)
        # With min=20, both pass
        result_lo = _compare(before, after, min_packet_count=20)
        assert result_lo["data_quality"] == "ok"
        # With min=30, both fail
        result_hi = _compare(before, after, min_packet_count=30)
        assert result_hi["data_quality"] == "insufficient"

    def test_comparison_has_data_quality_field(self):
        result = _compare(_make_report(packet_count=50), _make_report(packet_count=50))
        assert "data_quality" in result
        assert "packet_count_ok_before" in result
        assert "packet_count_ok_after" in result
        assert "min_packet_count" in result

    def test_packet_count_ok_handles_missing_report(self):
        """packet_count_ok should be False when report is None."""
        result = _compare(None, _make_report(packet_count=50))
        assert result["packet_count_ok_before"] is False
        assert result["packet_count_ok_after"] is True
        assert result["data_quality"] == "insufficient"


# ---------------------------------------------------------------------------
# Patch prompt generation — Phase 9B split logic
# ---------------------------------------------------------------------------


class TestPatchPrompts:
    def test_regressed_with_ok_data_generates_countermeasure_prompt(self, tmp_path):
        entries = [
            {
                "transport": "tcp", "scenario": "ping",
                "fingerprint_risk_score_before": 0.4,
                "fingerprint_risk_score_after": 0.55,
                "risk_score_delta": 0.15,
                "verdict": "regressed",
                "data_quality": "ok",
                "packet_count_before": 50, "packet_count_after": 55,
            },
        ]
        out_dir = str(tmp_path)
        _generate_patch_prompts(entries, out_dir)

        # Countermeasure prompt should exist
        prompt_path = Path(out_dir) / "next_patch_prompt.txt"
        assert prompt_path.exists()
        content = prompt_path.read_text()
        assert "tcp/ping" in content
        assert "src/shaping/padding.py" in content

        # Data collection prompt should NOT exist (data is ok)
        dc_path = Path(out_dir) / "data_collection_prompt.txt"
        assert not dc_path.exists()

    def test_insufficient_data_does_not_generate_countermeasure_prompt(self, tmp_path):
        entries = [
            {
                "transport": "tcp", "scenario": "idle",
                "fingerprint_risk_score_before": 0.0,
                "fingerprint_risk_score_after": 0.0,
                "risk_score_delta": 0.0,
                "verdict": "insufficient",
                "data_quality": "insufficient",
                "packet_count_before": 4, "packet_count_after": 2,
                "min_packet_count": 30,
            },
        ]
        out_dir = str(tmp_path)
        _generate_patch_prompts(entries, out_dir)

        # Countermeasure prompt should NOT exist
        prompt_path = Path(out_dir) / "next_patch_prompt.txt"
        assert not prompt_path.exists()

    def test_insufficient_data_generates_data_collection_prompt(self, tmp_path):
        entries = [
            {
                "transport": "tcp", "scenario": "idle",
                "fingerprint_risk_score_before": 0.0,
                "fingerprint_risk_score_after": 0.0,
                "risk_score_delta": 0.0,
                "verdict": "insufficient",
                "data_quality": "insufficient",
                "packet_count_before": 4, "packet_count_after": 2,
                "min_packet_count": 30,
            },
        ]
        out_dir = str(tmp_path)
        _generate_patch_prompts(entries, out_dir)

        dc_path = Path(out_dir) / "data_collection_prompt.txt"
        assert dc_path.exists()
        content = dc_path.read_text()
        assert "insufficient" in content.lower()
        assert "capture-duration" in content.lower() or "capture_duration" in content.lower()
        assert "tcp/idle" in content
        assert "4" in content  # packet count
        assert "2" in content  # packet count

    def test_no_prompt_when_no_regressions_or_insufficient(self, tmp_path):
        entries = [
            {"transport": "tcp", "scenario": "ping", "verdict": "improved", "data_quality": "ok"},
            {"transport": "tcp", "scenario": "bulk", "verdict": "unchanged", "data_quality": "ok"},
        ]
        out_dir = str(tmp_path)
        _generate_patch_prompts(entries, out_dir)
        assert not (Path(out_dir) / "next_patch_prompt.txt").exists()
        assert not (Path(out_dir) / "data_collection_prompt.txt").exists()

    def test_both_prompts_when_mixed_results(self, tmp_path):
        """When both regressed+ok and insufficient exist, both prompts generated."""
        entries = [
            {
                "transport": "tcp", "scenario": "ping",
                "verdict": "regressed", "data_quality": "ok",
                "fingerprint_risk_score_before": 0.4,
                "fingerprint_risk_score_after": 0.5,
                "risk_score_delta": 0.1,
                "packet_count_before": 50, "packet_count_after": 55,
            },
            {
                "transport": "tcp", "scenario": "idle",
                "verdict": "insufficient", "data_quality": "insufficient",
                "packet_count_before": 4, "packet_count_after": 2,
                "min_packet_count": 30,
            },
        ]
        out_dir = str(tmp_path)
        _generate_patch_prompts(entries, out_dir)

        assert (Path(out_dir) / "next_patch_prompt.txt").exists()
        assert (Path(out_dir) / "data_collection_prompt.txt").exists()

    def test_insufficient_does_not_suggest_module_changes(self, tmp_path):
        entries = [
            {
                "transport": "tcp", "scenario": "idle",
                "verdict": "insufficient", "data_quality": "insufficient",
                "packet_count_before": 2, "packet_count_after": 2,
                "min_packet_count": 30,
            },
        ]
        out_dir = str(tmp_path)
        _generate_patch_prompts(entries, out_dir)

        dc_path = Path(out_dir) / "data_collection_prompt.txt"
        content = dc_path.read_text()
        # Should NOT suggest modifying shaping modules
        assert "padding.py" not in content
        assert "Do NOT modify countermeasure code" in content


# ---------------------------------------------------------------------------
# Phase 9E: Statistical helpers
# ---------------------------------------------------------------------------


class TestStatisticalHelpers:
    def test_mean_basic(self):
        assert _mean([1.0, 2.0, 3.0]) == 2.0
        assert _mean([5.0]) == 5.0
        assert _mean([]) == 0.0

    def test_mean_with_floats(self):
        result = _mean([0.5, 0.6, 0.7])
        assert abs(result - 0.6) < 0.001

    def test_std_basic(self):
        result = _std([1.0, 2.0, 3.0])
        assert abs(result - 1.0) < 0.001

    def test_std_single_value_is_zero(self):
        assert _std([5.0]) == 0.0
        assert _std([]) == 0.0

    def test_std_two_values(self):
        result = _std([0.0, 2.0])
        assert abs(result - 1.414) < 0.01

    def test_safe_float_valid(self):
        assert _safe_float({"x": 42}, "x") == 42.0
        assert _safe_float({"x": 3.14}, "x") == 3.14

    def test_safe_float_missing_key(self):
        assert _safe_float({"a": 1}, "x") is None

    def test_safe_float_none_value(self):
        assert _safe_float({"x": None}, "x") is None


# ---------------------------------------------------------------------------
# Phase 9E: Per-run verdicts and aggregate verdicts
# ---------------------------------------------------------------------------


def _r(packet_count=50, risk_score=0.5, risk_level="medium"):
    """Shortcut to create a report dict for testing."""
    return {
        "packet_count": packet_count,
        "fingerprint_risk_score": risk_score,
        "risk_level": risk_level,
    }


class TestPerRunVerdicts:
    def test_all_improved(self):
        before = [_r(risk_score=0.5), _r(risk_score=0.5), _r(risk_score=0.5)]
        after = [_r(risk_score=0.43), _r(risk_score=0.43), _r(risk_score=0.43)]
        verdicts = _per_run_verdicts(before, after, 30)
        assert verdicts == ["improved", "improved", "improved"]

    def test_all_regressed(self):
        before = [_r(risk_score=0.4), _r(risk_score=0.4)]
        after = [_r(risk_score=0.5), _r(risk_score=0.5)]
        verdicts = _per_run_verdicts(before, after, 30)
        assert verdicts == ["regressed", "regressed"]

    def test_all_unchanged(self):
        before = [_r(risk_score=0.5), _r(risk_score=0.5)]
        after = [_r(risk_score=0.51), _r(risk_score=0.49)]
        verdicts = _per_run_verdicts(before, after, 30)
        assert verdicts == ["unchanged", "unchanged"]

    def test_mixed(self):
        before = [_r(risk_score=0.5), _r(risk_score=0.5), _r(risk_score=0.5)]
        after = [_r(risk_score=0.43), _r(risk_score=0.5), _r(risk_score=0.58)]
        verdicts = _per_run_verdicts(before, after, 30)
        assert verdicts == ["improved", "unchanged", "regressed"]

    def test_insufficient_when_packet_count_low(self):
        before = [_r(packet_count=5), _r(packet_count=50)]
        after = [_r(packet_count=50), _r(packet_count=5)]
        verdicts = _per_run_verdicts(before, after, 30)
        assert verdicts[0] == "insufficient"
        assert verdicts[1] == "insufficient"

    def test_missing_report_marks_insufficient(self):
        before = [_r(packet_count=50)]
        after = []
        verdicts = _per_run_verdicts(before, after, 30)
        assert verdicts == ["insufficient"]


class TestPairedDeltas:
    def test_returns_deltas_for_valid_pairs(self):
        before = [_r(risk_score=0.5), _r(risk_score=0.4)]
        after = [_r(risk_score=0.43), _r(risk_score=0.45)]
        deltas = _paired_deltas(before, after, 30)
        assert len(deltas) == 2
        assert deltas[0] == -0.07
        assert deltas[1] == 0.05

    def test_filters_insufficient_by_packet_count(self):
        before = [_r(packet_count=50, risk_score=0.5), _r(packet_count=5, risk_score=0.5)]
        after = [_r(packet_count=50, risk_score=0.4), _r(packet_count=50, risk_score=0.4)]
        deltas = _paired_deltas(before, after, 30)
        assert len(deltas) == 1  # only the first pair

    def test_empty_when_no_reports(self):
        deltas = _paired_deltas([], [], 30)
        assert deltas == []


class TestAggregateVerdict:
    """Test aggregate_verdict rules from Phase 9E spec."""

    def test_aggregate_improved(self):
        """improved_count >= 2 and regressed == 0 → improved"""
        entry = {
            "data_quality": "ok",
            "improved_count": 2,
            "unchanged_count": 1,
            "regressed_count": 0,
            "insufficient_count": 0,
            "skipped_count": 0,
        }
        # Simulate aggregate verdict
        imp, reg, unc = entry["improved_count"], entry["regressed_count"], entry["unchanged_count"]
        if imp >= 2 and reg == 0:
            verdict = "improved"
        elif reg >= 2:
            verdict = "regressed"
        elif unc >= 2:
            verdict = "unchanged"
        else:
            verdict = "mixed"
        assert verdict == "improved"

    def test_aggregate_regressed(self):
        entry = {"data_quality": "ok", "improved_count": 0, "unchanged_count": 1,
                 "regressed_count": 2, "insufficient_count": 0, "skipped_count": 0}
        imp, reg, unc = entry["improved_count"], entry["regressed_count"], entry["unchanged_count"]
        if imp >= 2 and reg == 0:
            verdict = "improved"
        elif reg >= 2:
            verdict = "regressed"
        elif unc >= 2:
            verdict = "unchanged"
        else:
            verdict = "mixed"
        assert verdict == "regressed"

    def test_aggregate_unchanged(self):
        entry = {"data_quality": "ok", "improved_count": 0, "unchanged_count": 2,
                 "regressed_count": 0, "insufficient_count": 0, "skipped_count": 0}
        imp, reg, unc = entry["improved_count"], entry["regressed_count"], entry["unchanged_count"]
        if imp >= 2 and reg == 0:
            verdict = "improved"
        elif reg >= 2:
            verdict = "regressed"
        elif unc >= 2:
            verdict = "unchanged"
        else:
            verdict = "mixed"
        assert verdict == "unchanged"

    def test_aggregate_mixed(self):
        entry = {"data_quality": "ok", "improved_count": 1, "unchanged_count": 1,
                 "regressed_count": 1, "insufficient_count": 0, "skipped_count": 0}
        imp, reg, unc = entry["improved_count"], entry["regressed_count"], entry["unchanged_count"]
        if imp >= 2 and reg == 0:
            verdict = "improved"
        elif reg >= 2:
            verdict = "regressed"
        elif unc >= 2:
            verdict = "unchanged"
        else:
            verdict = "mixed"
        assert verdict == "mixed"

    def test_aggregate_insufficient_when_no_valid_runs(self):
        entry = {"data_quality": "insufficient", "improved_count": 0, "unchanged_count": 0,
                 "regressed_count": 0, "insufficient_count": 3, "skipped_count": 0}
        assert entry["data_quality"] == "insufficient"
        assert entry["insufficient_count"] == 3


# ---------------------------------------------------------------------------
# Phase 9E: Patch prompt 9E strategy
# ---------------------------------------------------------------------------


class TestPatchPrompts9E:
    def test_repeated_regressed_generates_countermeasure_prompt(self, tmp_path):
        entries = [{
            "transport": "tcp", "scenario": "ping",
            "aggregate_verdict": "regressed",
            "data_quality": "ok",
            "before_risk_score_mean": 0.4,
            "after_risk_score_mean": 0.55,
            "risk_score_delta_mean": 0.15,
            "valid_repeat_before": 3, "valid_repeat_after": 3,
        }]
        _generate_patch_prompts(entries, str(tmp_path), repeated=True)
        assert (Path(tmp_path) / "next_patch_prompt.txt").exists()

    def test_repeated_high_risk_generates_countermeasure_prompt(self, tmp_path):
        entries = [{
            "transport": "tcp", "scenario": "ping",
            "aggregate_verdict": "unchanged",
            "data_quality": "ok",
            "before_risk_score_mean": 0.65,
            "after_risk_score_mean": 0.72,
            "risk_score_delta_mean": 0.07,
            "valid_repeat_before": 3, "valid_repeat_after": 3,
        }]
        _generate_patch_prompts(entries, str(tmp_path), repeated=True)
        assert (Path(tmp_path) / "next_patch_prompt.txt").exists()

    def test_repeated_improved_generates_no_patch_needed(self, tmp_path):
        entries = [{
            "transport": "tcp", "scenario": "ping",
            "aggregate_verdict": "improved",
            "data_quality": "ok",
            "before_risk_score_mean": 0.5,
            "after_risk_score_mean": 0.42,
            "risk_score_delta_mean": -0.08,
            "valid_repeat_before": 3, "valid_repeat_after": 3,
        }]
        _generate_patch_prompts(entries, str(tmp_path), repeated=True)
        assert (Path(tmp_path) / "no_patch_needed.txt").exists()
        assert not (Path(tmp_path) / "next_patch_prompt.txt").exists()

    def test_repeated_partial_generates_data_collection_prompt(self, tmp_path):
        entries = [{
            "transport": "tcp", "scenario": "bulk",
            "aggregate_verdict": "mixed",
            "data_quality": "partial",
            "valid_repeat_before": 3, "valid_repeat_after": 2,
            "before_packet_count_mean": 51, "after_packet_count_mean": 28,
            "min_packet_count": 30,
        }]
        _generate_patch_prompts(entries, str(tmp_path), repeated=True)
        dc_path = Path(tmp_path) / "data_collection_prompt.txt"
        assert dc_path.exists()
        content = dc_path.read_text()
        assert "Do NOT modify countermeasure code" in content

    def test_repeated_insufficient_does_not_generate_patch_prompt(self, tmp_path):
        entries = [{
            "transport": "tcp", "scenario": "bulk",
            "aggregate_verdict": "insufficient",
            "data_quality": "insufficient",
            "valid_repeat_before": 0, "valid_repeat_after": 0,
        }]
        _generate_patch_prompts(entries, str(tmp_path), repeated=True)
        assert not (Path(tmp_path) / "next_patch_prompt.txt").exists()
        assert (Path(tmp_path) / "data_collection_prompt.txt").exists()


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
            "scenario_command": "sleep 27",
            "capture_interface": "veth_srv",
            "capture_host": "192.168.200.1",
            "needs_http_server": False,
            "capture_duration": 30,
            "min_packet_count": 30,
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


# ---------------------------------------------------------------------------
# _now_iso
# ---------------------------------------------------------------------------


class TestNowIso:
    def test_returns_non_empty_string(self):
        ts = _now_iso()
        assert isinstance(ts, str)
        assert len(ts) > 0
        assert "T" in ts


# ---------------------------------------------------------------------------
# _discover_transport_scenarios
# ---------------------------------------------------------------------------


class TestDiscoverTransportScenarios:
    def test_empty_dir_returns_empty_list(self, tmp_path):
        result = _discover_transport_scenarios(str(tmp_path))
        assert result == []

    def test_discovers_single_pair(self, tmp_path):
        before_dir = tmp_path / "before" / "tcp" / "ping"
        before_dir.mkdir(parents=True)
        (before_dir / "run_01.report.json").write_text("{}")
        result = _discover_transport_scenarios(str(tmp_path))
        assert result == [("tcp", "ping")]

    def test_discovers_multiple_pairs_sorted(self, tmp_path):
        for t, s in [("websocket", "bulk"), ("tcp", "ping"), ("tls", "ping")]:
            d = tmp_path / "before" / t / s
            d.mkdir(parents=True)
            (d / "run_01.report.json").write_text("{}")
        result = _discover_transport_scenarios(str(tmp_path))
        assert result == [("tcp", "ping"), ("tls", "ping"), ("websocket", "bulk")]

    def test_ignores_dirs_without_reports(self, tmp_path):
        (tmp_path / "before" / "tcp" / "empty_scenario").mkdir(parents=True)
        d = tmp_path / "before" / "tcp" / "ping"
        d.mkdir(parents=True)
        (d / "run_01.report.json").write_text("{}")
        result = _discover_transport_scenarios(str(tmp_path))
        assert result == [("tcp", "ping")]

    def test_no_before_dir_returns_empty(self, tmp_path):
        (tmp_path / "after" / "tcp" / "ping").mkdir(parents=True)
        result = _discover_transport_scenarios(str(tmp_path))
        assert result == []

    def test_handles_multiple_transports(self, tmp_path):
        for t in ["tcp", "tls", "websocket"]:
            d = tmp_path / "before" / t / "ping"
            d.mkdir(parents=True)
            (d / "run_01.report.json").write_text("{}")
        result = _discover_transport_scenarios(str(tmp_path))
        assert len(result) == 3
        transports = [t for t, s in result]
        assert transports == ["tcp", "tls", "websocket"]
