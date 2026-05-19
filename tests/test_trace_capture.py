"""Tests for scripts.trace_capture — local offline trace capture tool."""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.trace_capture import (
    build_tcpdump_command,
    build_tshark_csv_command,
    detect_tools,
    infer_direction,
    main as trace_capture_main,
    normalize_trace_rows,
    parse_tshark_text,
    rows_to_csv_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_main(*args: str) -> None:
    """Call main() with argv, suppressing SystemExit."""
    try:
        trace_capture_main(list(args))
    except SystemExit:
        pass


# ---------------------------------------------------------------------------
# 1. detect_tools monkeypatch
# ---------------------------------------------------------------------------


class TestDetectTools:
    def test_all_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import shutil
        monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/fake")
        result = detect_tools()
        assert result == {"tcpdump": True, "tshark": True, "timeout": True, "ip": True}

    def test_all_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import shutil
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        result = detect_tools()
        assert result == {"tcpdump": False, "tshark": False, "timeout": False, "ip": False}

    def test_mixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import shutil

        def _which(name: str) -> str | None:
            return "/usr/bin/fake" if name == "tcpdump" else None

        monkeypatch.setattr(shutil, "which", _which)
        result = detect_tools()
        assert result == {"tcpdump": True, "tshark": False, "timeout": False, "ip": False}


# ---------------------------------------------------------------------------
# 2. build_tcpdump_command
# ---------------------------------------------------------------------------


class TestBuildTcpdumpCommand:
    def test_minimal(self) -> None:
        cmd = build_tcpdump_command(
            interface="lo",
            output_pcap="/tmp/test.pcap",
            duration=10,
        )
        assert cmd == [
            "timeout", "10", "tcpdump", "-i", "lo",
            "-w", "/tmp/test.pcap",
        ]

    def test_with_host_and_port(self) -> None:
        cmd = build_tcpdump_command(
            interface="eth0",
            output_pcap="out.pcap",
            host="10.0.0.1",
            port=9000,
            duration=5,
        )
        assert "host 10.0.0.1 and port 9000" in " ".join(cmd)

    def test_with_sudo(self) -> None:
        cmd = build_tcpdump_command(
            interface="eth0",
            output_pcap="out.pcap",
            duration=10,
            sudo=True,
        )
        assert cmd[0] == "sudo"

    def test_with_extra_filter(self) -> None:
        cmd = build_tcpdump_command(
            interface="lo",
            output_pcap="out.pcap",
            duration=10,
            extra_filter="tcp",
        )
        joined = " ".join(cmd)
        assert "(tcp)" in joined

    def test_output_is_predictable(self) -> None:
        """Commands must be deterministic for testing."""
        cmd1 = build_tcpdump_command(
            interface="lo", output_pcap="a.pcap",
            host="127.0.0.1", port=9000, duration=10,
        )
        cmd2 = build_tcpdump_command(
            interface="lo", output_pcap="a.pcap",
            host="127.0.0.1", port=9000, duration=10,
        )
        assert cmd1 == cmd2


# ---------------------------------------------------------------------------
# 3. build_tshark_csv_command
# ---------------------------------------------------------------------------


class TestBuildTsharkCsvCommand:
    def test_minimal(self) -> None:
        cmd = build_tshark_csv_command(
            input_pcap="in.pcap",
            output_csv="out.csv",
            client_host="10.0.0.1",
            server_host="10.0.0.2",
            server_port=9000,
        )
        assert cmd[0] == "tshark"
        assert "-r" in cmd
        assert "in.pcap" in cmd
        assert "-T" in cmd
        assert "fields" in cmd
        assert "-E" in cmd
        assert "header=y" in cmd

    def test_output_is_predictable(self) -> None:
        cmd1 = build_tshark_csv_command(
            input_pcap="in.pcap", output_csv="out.csv",
            client_host="10.0.0.1", server_host="10.0.0.2",
        )
        cmd2 = build_tshark_csv_command(
            input_pcap="in.pcap", output_csv="out.csv",
            client_host="10.0.0.1", server_host="10.0.0.2",
        )
        assert cmd1 == cmd2


# ---------------------------------------------------------------------------
# 4. infer_direction host-based
# ---------------------------------------------------------------------------


class TestInferDirectionHost:
    def test_c2s_different_hosts(self) -> None:
        result = infer_direction(
            src="10.0.0.1", dst="10.0.0.2",
            src_port=45000, dst_port=9000,
            client_host="10.0.0.1", server_host="10.0.0.2",
        )
        assert result == "C2S"

    def test_s2c_different_hosts(self) -> None:
        result = infer_direction(
            src="10.0.0.2", dst="10.0.0.1",
            src_port=9000, dst_port=45000,
            client_host="10.0.0.1", server_host="10.0.0.2",
        )
        assert result == "S2C"


# ---------------------------------------------------------------------------
# 5. infer_direction server_port-based
# ---------------------------------------------------------------------------


class TestInferDirectionServerPort:
    def test_c2s_via_server_port(self) -> None:
        """Same host, dst_port matches server_port → C2S."""
        result = infer_direction(
            src="127.0.0.1", dst="127.0.0.1",
            src_port=45000, dst_port=9000,
            client_host="127.0.0.1", server_host="127.0.0.1",
            server_port=9000,
        )
        assert result == "C2S"

    def test_s2c_via_server_port(self) -> None:
        """Same host, src_port matches server_port → S2C."""
        result = infer_direction(
            src="127.0.0.1", dst="127.0.0.1",
            src_port=9000, dst_port=45000,
            client_host="127.0.0.1", server_host="127.0.0.1",
            server_port=9000,
        )
        assert result == "S2C"

    def test_c2s_via_client_port_fallback(self) -> None:
        """Same host, src_port matches client_port → C2S."""
        result = infer_direction(
            src="127.0.0.1", dst="127.0.0.1",
            src_port=45000, dst_port=9999,
            client_host="127.0.0.1", server_host="127.0.0.1",
            client_port=45000,
        )
        assert result == "C2S"


# ---------------------------------------------------------------------------
# 6. infer_direction cannot determine
# ---------------------------------------------------------------------------


class TestInferDirectionUnknown:
    def test_raises_valueerror_unknown(self) -> None:
        with pytest.raises(ValueError, match="Cannot infer direction"):
            infer_direction(
                src="10.0.0.3", dst="10.0.0.4",
                src_port=111, dst_port=222,
                client_host="10.0.0.1", server_host="10.0.0.2",
            )

    def test_raises_valueerror_same_host_no_ports(self) -> None:
        with pytest.raises(ValueError, match="Cannot infer direction"):
            infer_direction(
                src="127.0.0.1", dst="127.0.0.1",
                src_port=111, dst_port=222,
                client_host="127.0.0.1", server_host="127.0.0.1",
            )


# ---------------------------------------------------------------------------
# 7. normalize_trace_rows produces valid CSV rows
# ---------------------------------------------------------------------------


class TestNormalizeTraceRows:
    def test_produces_valid_rows(self) -> None:
        raw = [
            {
                "frame.time_epoch": "0.000",
                "ip.src": "10.0.0.1",
                "ip.dst": "10.0.0.2",
                "tcp.srcport": "45000",
                "tcp.dstport": "9000",
                "udp.srcport": "",
                "udp.dstport": "",
                "frame.protocols": "eth:ip:tcp",
                "frame.len": "64",
            },
            {
                "frame.time_epoch": "0.005",
                "ip.src": "10.0.0.2",
                "ip.dst": "10.0.0.1",
                "tcp.srcport": "9000",
                "tcp.dstport": "45000",
                "udp.srcport": "",
                "udp.dstport": "",
                "frame.protocols": "eth:ip:tcp",
                "frame.len": "64",
            },
        ]
        rows = normalize_trace_rows(
            raw,
            client_host="10.0.0.1",
            server_host="10.0.0.2",
            server_port=9000,
        )
        assert len(rows) == 2
        assert rows[0]["direction"] == "C2S"
        assert rows[0]["proto"] == "tcp"
        assert rows[0]["length"] == 64
        assert rows[1]["direction"] == "S2C"

    def test_csv_text_output(self) -> None:
        rows = [
            {
                "timestamp": 0.0, "src": "10.0.0.1", "dst": "10.0.0.2",
                "src_port": 45000, "dst_port": 9000, "proto": "tcp",
                "length": 64, "direction": "C2S",
            },
        ]
        text = rows_to_csv_text(rows)
        assert "timestamp,src,dst,src_port,dst_port,proto,length,direction" in text
        assert "C2S" in text

    def test_skips_rows_without_ip(self) -> None:
        raw = [
            {
                "frame.time_epoch": "0.000",
                "ip.src": "",
                "ip.dst": "",
                "tcp.srcport": "", "tcp.dstport": "",
                "udp.srcport": "", "udp.dstport": "",
                "frame.protocols": "arp",
                "frame.len": "42",
            },
        ]
        rows = normalize_trace_rows(
            raw,
            client_host="10.0.0.1",
            server_host="10.0.0.2",
        )
        assert len(rows) == 0

    def test_udp_fallback(self) -> None:
        raw = [
            {
                "frame.time_epoch": "0.000",
                "ip.src": "10.0.0.1",
                "ip.dst": "10.0.0.2",
                "tcp.srcport": "", "tcp.dstport": "",
                "udp.srcport": "5000", "udp.dstport": "53",
                "frame.protocols": "eth:ip:udp:dns",
                "frame.len": "128",
            },
        ]
        rows = normalize_trace_rows(
            raw,
            client_host="10.0.0.1",
            server_host="10.0.0.2",
            server_port=53,
        )
        assert len(rows) == 1
        assert rows[0]["dst_port"] == 53

    def test_tls_proto_detection(self) -> None:
        raw = [
            {
                "frame.time_epoch": "0.000",
                "ip.src": "10.0.0.1",
                "ip.dst": "10.0.0.2",
                "tcp.srcport": "45000", "tcp.dstport": "9000",
                "udp.srcport": "", "udp.dstport": "",
                "frame.protocols": "eth:ip:tcp:tls",
                "frame.len": "256",
            },
        ]
        rows = normalize_trace_rows(
            raw,
            client_host="10.0.0.1",
            server_host="10.0.0.2",
            server_port=9000,
        )
        assert rows[0]["proto"] == "tls"


# ---------------------------------------------------------------------------
# 8. CLI tools subcommand
# ---------------------------------------------------------------------------


class TestCliTools:
    def test_tools_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        _run_main("tools")
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "tcpdump" in data
        assert "tshark" in data
        assert isinstance(data["tcpdump"], bool)


# ---------------------------------------------------------------------------
# 9. CLI capture --dry-run does not execute
# ---------------------------------------------------------------------------


class TestCliCaptureDryRun:
    def test_dry_run_no_execution(self, capsys: pytest.CaptureFixture[str]) -> None:
        _run_main(
            "capture",
            "--interface", "lo",
            "--host", "127.0.0.1",
            "--port", "9000",
            "--duration", "3",
            "--output-pcap", "/tmp/vpn_llm_test.pcap",
            "--dry-run",
        )
        out = capsys.readouterr().out
        assert "[dry-run]" in out
        assert "tcpdump" in out


# ---------------------------------------------------------------------------
# 10. CLI convert with mock input
# ---------------------------------------------------------------------------


class TestCliConvert:
    def test_dry_run_with_text_input(self, capsys: pytest.CaptureFixture[str]) -> None:
        _run_main(
            "convert",
            "--input-pcap", "/tmp/test.pcap",
            "--output-csv", "/tmp/test.csv",
            "--client-host", "10.0.0.1",
            "--server-host", "10.0.0.2",
            "--server-port", "9000",
            "--dry-run",
            "--text-input",
            "frame.time_epoch,ip.src,ip.dst,tcp.srcport,tcp.dstport,udp.srcport,udp.dstport,frame.protocols,frame.len\n"
            "0.000,10.0.0.1,10.0.0.2,45000,9000,,,eth:ip:tcp,64\n"
            "0.005,10.0.0.2,10.0.0.1,9000,45000,,,eth:ip:tcp,64\n",
        )
        out = capsys.readouterr().out
        assert "[dry-run]" in out
        assert "tshark" in out
        # Should preview CSV rows
        assert "C2S" in out

    def test_dry_run_without_text_input(self, capsys: pytest.CaptureFixture[str]) -> None:
        _run_main(
            "convert",
            "--input-pcap", "/tmp/test.pcap",
            "--output-csv", "/tmp/test.csv",
            "--client-host", "10.0.0.1",
            "--server-host", "10.0.0.2",
            "--dry-run",
        )
        out = capsys.readouterr().out
        assert "[dry-run]" in out
        assert "tshark" in out


# ---------------------------------------------------------------------------
# parse_tshark_text
# ---------------------------------------------------------------------------


class TestParseTsharkText:
    def test_parses_with_header(self) -> None:
        text = (
            "frame.time_epoch,ip.src,ip.dst,tcp.srcport,tcp.dstport,udp.srcport,udp.dstport,frame.protocols,frame.len\n"
            "0.000,10.0.0.1,10.0.0.2,45000,9000,,,eth:ip:tcp,64\n"
        )
        rows = parse_tshark_text(text)
        assert len(rows) == 1
        assert rows[0]["frame.time_epoch"] == "0.000"

    def test_handles_empty_text(self) -> None:
        rows = parse_tshark_text("")
        assert len(rows) == 0
