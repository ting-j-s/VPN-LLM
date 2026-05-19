"""Tests for scripts.run_trace_scenarios — batch scenario runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_trace_scenarios import (
    ScenarioSpec,
    build_capture_plan,
    build_manifest,
    build_scenario_matrix,
    main as scenario_main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_main(*args: str) -> None:
    try:
        scenario_main(list(args))
    except SystemExit:
        pass


# ---------------------------------------------------------------------------
# 1. build_scenario_matrix
# ---------------------------------------------------------------------------


class TestBuildScenarioMatrix:
    def test_full_matrix(self) -> None:
        specs = build_scenario_matrix(
            transports=["tcp", "tls", "websocket", "ssh"],
            scenarios=["idle", "ping", "curl", "bulk"],
            output_dir="traces",
            server_host="127.0.0.1",
            base_port=9000,
        )
        # 4 transports × 4 scenarios = 16
        assert len(specs) == 16

    def test_subset(self) -> None:
        specs = build_scenario_matrix(
            transports=["tcp", "tls"],
            scenarios=["idle", "ping"],
            output_dir="traces",
        )
        assert len(specs) == 4

    def test_includes_reconnect(self) -> None:
        specs = build_scenario_matrix(
            transports=["tcp"],
            scenarios=["reconnect"],
            output_dir="traces",
        )
        assert len(specs) == 1
        assert specs[0].name == "reconnect"

    def test_each_spec_has_fields(self) -> None:
        specs = build_scenario_matrix(
            transports=["tcp"],
            scenarios=["idle"],
            output_dir="traces",
        )
        s = specs[0]
        assert s.name == "idle"
        assert s.transport == "tcp"
        assert s.server_port == 9000
        assert s.interface == "lo"
        assert s.output_dir == "traces"
        assert isinstance(s.duration, int)

    def test_order_preserved(self) -> None:
        specs = build_scenario_matrix(
            transports=["tcp", "tls"],
            scenarios=["idle", "ping"],
            output_dir="traces",
        )
        # (tcp,idle), (tcp,ping), (tls,idle), (tls,ping)
        assert specs[0].transport == "tcp" and specs[0].name == "idle"
        assert specs[1].transport == "tcp" and specs[1].name == "ping"
        assert specs[2].transport == "tls" and specs[2].name == "idle"
        assert specs[3].transport == "tls" and specs[3].name == "ping"


# ---------------------------------------------------------------------------
# 2. port assignment
# ---------------------------------------------------------------------------


class TestPortAssignment:
    def test_stable_port_per_transport_index(self) -> None:
        specs = build_scenario_matrix(
            transports=["tcp", "tls", "websocket", "ssh"],
            scenarios=["idle"],
            base_port=9000,
        )
        ports = {s.transport: s.server_port for s in specs}
        assert ports["tcp"] == 9000
        assert ports["tls"] == 9001
        assert ports["websocket"] == 9002
        assert ports["ssh"] == 9003

    def test_custom_base_port(self) -> None:
        specs = build_scenario_matrix(
            transports=["tcp"],
            scenarios=["idle"],
            base_port=5000,
        )
        assert specs[0].server_port == 5000


# ---------------------------------------------------------------------------
# 3. build_capture_plan
# ---------------------------------------------------------------------------


class TestBuildCapturePlan:
    def test_generates_paths(self) -> None:
        spec = ScenarioSpec(
            name="idle",
            transport="tcp",
            output_dir="traces",
            server_port=9000,
        )
        plan = build_capture_plan(spec)
        assert plan["pcap_path"] == "traces/tcp/idle.pcap"
        assert plan["csv_path"] == "traces/tcp/idle.csv"
        assert plan["report_path"] == "traces/tcp/idle.report.json"

    def test_includes_commands(self) -> None:
        spec = ScenarioSpec(
            name="idle",
            transport="tcp",
            server_port=9000,
        )
        plan = build_capture_plan(spec)
        assert "tcpdump" in plan["capture_command"]
        assert "tshark" in plan["convert_command"]
        assert "report" in plan["report_command"]

    def test_scenario_command_included(self) -> None:
        spec = ScenarioSpec(
            name="curl",
            transport="tcp",
            server_port=9000,
            command="curl -s http://10.0.0.2:8080/",
        )
        plan = build_capture_plan(spec)
        assert plan["scenario_command"] != ""


# ---------------------------------------------------------------------------
# 4. build_manifest
# ---------------------------------------------------------------------------


class TestBuildManifest:
    def test_returns_list_of_dicts(self) -> None:
        specs = build_scenario_matrix(
            transports=["tcp"],
            scenarios=["idle", "ping"],
            output_dir="traces",
        )
        manifest = build_manifest(specs)
        assert len(manifest) == 2
        assert all(isinstance(e, dict) for e in manifest)
        assert all("pcap_path" in e for e in manifest)
        assert all("csv_path" in e for e in manifest)
        assert all("report_path" in e for e in manifest)


# ---------------------------------------------------------------------------
# 5. plan CLI
# ---------------------------------------------------------------------------


class TestPlanCli:
    def test_plan_outputs_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        _run_main(
            "plan",
            "--transports", "tcp",
            "--scenarios", "idle",
            "--output-dir", "traces",
            "--server-host", "127.0.0.1",
            "--base-port", "9000",
        )
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["entry_count"] == 1
        assert len(data["entries"]) == 1
        assert data["entries"][0]["transport"] == "tcp"
        assert data["entries"][0]["scenario"] == "idle"

    def test_plan_with_manifest_file(self, tmp_path: Path) -> None:
        mf = tmp_path / "manifest.json"
        _run_main(
            "plan",
            "--transports", "tcp",
            "--scenarios", "idle",
            "--output-dir", str(tmp_path / "traces"),
            "--server-host", "127.0.0.1",
            "--base-port", "9000",
            "--manifest-file", str(mf),
        )
        assert mf.is_file()
        data = json.loads(mf.read_text())
        assert data["entry_count"] == 1


# ---------------------------------------------------------------------------
# 6. run --dry-run does not execute
# ---------------------------------------------------------------------------


class TestRunDryRun:
    def test_dry_run_no_execution(self, capsys: pytest.CaptureFixture[str]) -> None:
        _run_main(
            "run",
            "--transports", "tcp",
            "--scenarios", "idle",
            "--output-dir", "traces",
            "--server-host", "127.0.0.1",
            "--base-port", "9000",
            "--dry-run",
        )
        out = capsys.readouterr().out
        assert "[dry-run]" in out

    def test_no_execute_flag_defaults_to_dry_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Without --execute, run should default to dry-run."""
        _run_main(
            "run",
            "--transports", "tcp",
            "--scenarios", "idle",
            "--output-dir", "traces",
        )
        out = capsys.readouterr().out
        assert "[dry-run]" in out


# ---------------------------------------------------------------------------
# 7. no http2
# ---------------------------------------------------------------------------


class TestNoHttp2:
    def test_http2_not_in_supported_transports(self) -> None:
        from scripts.run_trace_scenarios import _SUPPORTED_TRANSPORTS
        assert "http2" not in _SUPPORTED_TRANSPORTS

    def test_http2_warns_and_skips(self) -> None:
        specs = build_scenario_matrix(
            transports=["http2"],
            scenarios=["idle"],
        )
        assert len(specs) == 0


# ---------------------------------------------------------------------------
# 8. unknown scenario name errors cleanly
# ---------------------------------------------------------------------------


class TestUnknownScenario:
    def test_unknown_scenario_warns_and_skips(self) -> None:
        specs = build_scenario_matrix(
            transports=["tcp"],
            scenarios=["unknown_scenario"],
        )
        assert len(specs) == 0

    def test_invalid_transport_warns_and_skips(self) -> None:
        specs = build_scenario_matrix(
            transports=["invalid_proto"],
            scenarios=["idle"],
        )
        assert len(specs) == 0

    def test_mixed_valid_invalid(self) -> None:
        specs = build_scenario_matrix(
            transports=["tcp", "invalid"],
            scenarios=["idle", "bad"],
        )
        # Only (tcp, idle) should survive
        assert len(specs) == 1
        assert specs[0].transport == "tcp"
        assert specs[0].name == "idle"


# ---------------------------------------------------------------------------
# ScenarioSpec serialization
# ---------------------------------------------------------------------------


class TestScenarioSpec:
    def test_dataclass_fields(self) -> None:
        spec = ScenarioSpec(
            name="idle",
            transport="tcp",
            duration=10,
            interface="lo",
            server_host="127.0.0.1",
            server_port=9000,
            output_dir="traces",
            command="# test",
            notes="test note",
        )
        d = spec.__dict__
        assert d["name"] == "idle"
        assert d["transport"] == "tcp"
        assert d["server_port"] == 9000
