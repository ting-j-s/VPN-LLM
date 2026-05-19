#!/usr/bin/env python3
"""Batch scenario runner for VPN-LLM local offline trace capture and fingerprint evaluation.

Organises experiments across transport × scenario combinations, produces a
manifest of pcap/csv/report paths, and optionally executes the full pipeline
(capture → scenario command → convert → report).

Usage:
    python3 scripts/run_trace_scenarios.py plan --transports tcp,tls,websocket,ssh --scenarios idle,ping,curl,bulk --output-dir traces --server-host 127.0.0.1 --base-port 9000
    python3 scripts/run_trace_scenarios.py run --transports tcp --scenarios idle --output-dir traces --server-host 127.0.0.1 --base-port 9000 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SUPPORTED_TRANSPORTS = frozenset({"tcp", "tls", "websocket", "ssh"})
_VALID_SCENARIOS = frozenset({"idle", "ping", "curl", "bulk", "reconnect"})
_DEFAULT_BASE_PORT = 9000


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ScenarioSpec:
    """Description of a single trace-capture scenario.

    Attributes:
        name: scenario label (idle / ping / curl / bulk / reconnect).
        transport: transport name (tcp / tls / websocket / ssh).
        duration: capture duration in seconds.
        interface: network interface for tcpdump.
        server_host: server IP address.
        server_port: server port for this transport.
        output_dir: base output directory.
        command: optional scenario command to run during capture.
        notes: human-readable notes.
    """

    name: str
    transport: str
    duration: int = 10
    interface: str = "lo"
    server_host: str = "127.0.0.1"
    server_port: int = 9000
    output_dir: str = "traces"
    command: str = ""
    notes: str = ""


# ---------------------------------------------------------------------------
# Scenario matrix builder
# ---------------------------------------------------------------------------


def build_scenario_matrix(
    transports: list[str],
    scenarios: list[str],
    output_dir: str = "traces",
    *,
    server_host: str = "127.0.0.1",
    base_port: int = _DEFAULT_BASE_PORT,
    interface: str = "lo",
    duration: int = 10,
) -> list[ScenarioSpec]:
    """Build the cross-product of transports × scenarios.

    Each transport is assigned a unique port (base_port + index).
    Only supported transports and valid scenarios are included.
    """
    specs: list[ScenarioSpec] = []

    for t_idx, transport in enumerate(transports):
        if transport not in _SUPPORTED_TRANSPORTS:
            print(
                f"Warning: unsupported transport '{transport}' — skipping.",
                file=sys.stderr,
            )
            continue

        for scenario in scenarios:
            if scenario not in _VALID_SCENARIOS:
                print(
                    f"Warning: unknown scenario '{scenario}' — skipping.",
                    file=sys.stderr,
                )
                continue

            port = base_port + t_idx
            command = _scenario_command(scenario, transport)

            specs.append(
                ScenarioSpec(
                    name=scenario,
                    transport=transport,
                    duration=duration,
                    interface=interface,
                    server_host=server_host,
                    server_port=port,
                    output_dir=output_dir,
                    command=command,
                    notes=f"{transport} {scenario} scenario",
                )
            )

    return specs


def _scenario_command(scenario: str, transport: str) -> str:
    """Return a suggested shell command for a given scenario.

    These are *templates* — the caller can override or skip them.
    """
    templates: dict[str, str] = {
        "idle": "# no traffic — just keep the tunnel open for the capture duration",
        "ping": "ping -c 5 10.0.0.2",
        "curl": "curl -s -o /dev/null http://10.0.0.2:8080/",
        "bulk": "dd if=/dev/zero bs=1M count=10 | nc 10.0.0.2 8080",
        "reconnect": "# open/close the tunnel 3 times during capture",
    }
    return templates.get(scenario, "# no command specified")


# ---------------------------------------------------------------------------
# Capture plan builder
# ---------------------------------------------------------------------------


def build_capture_plan(
    spec: ScenarioSpec,
    *,
    sudo: bool = False,
) -> dict[str, Any]:
    """Build a full capture → convert → report plan for a single scenario.

    Returns a dict with pcap_path, csv_path, report_path, capture_command,
    convert_command, and report_command.
    """
    transport_dir = Path(spec.output_dir) / spec.transport
    base_name = spec.name

    pcap_path = transport_dir / f"{base_name}.pcap"
    csv_path = transport_dir / f"{base_name}.csv"
    report_path = transport_dir / f"{base_name}.report.json"

    # Import locally to avoid requiring the module at parse time
    from scripts.trace_capture import build_tcpdump_command, build_tshark_csv_command

    capture_cmd = build_tcpdump_command(
        interface=spec.interface,
        output_pcap=pcap_path,
        host=spec.server_host,
        port=spec.server_port,
        duration=spec.duration,
        sudo=sudo,
    )

    convert_cmd = build_tshark_csv_command(
        input_pcap=pcap_path,
        output_csv=csv_path,
        client_host=spec.server_host,
        server_host=spec.server_host,
        server_port=spec.server_port,
    )

    report_cmd = [
        sys.executable,
        "-m", "src.evaluation.fingerprint.report",
        "--input", str(csv_path),
        "--output", str(report_path),
    ]

    return {
        "scenario": spec.name,
        "transport": spec.transport,
        "server_port": spec.server_port,
        "pcap_path": str(pcap_path),
        "csv_path": str(csv_path),
        "report_path": str(report_path),
        "scenario_command": spec.command,
        "capture_command": " ".join(capture_cmd),
        "convert_command": " ".join(convert_cmd),
        "report_command": " ".join(report_cmd),
    }


# ---------------------------------------------------------------------------
# Manifest management
# ---------------------------------------------------------------------------


def build_manifest(
    specs: list[ScenarioSpec],
    *,
    sudo: bool = False,
) -> list[dict[str, Any]]:
    """Build capture plans for all specs and return a manifest list."""
    return [build_capture_plan(s, sudo=sudo) for s in specs]


# ---------------------------------------------------------------------------
# Subcommand: plan
# ---------------------------------------------------------------------------


def _run_plan(args: argparse.Namespace) -> None:
    transports = [t.strip() for t in args.transports.split(",") if t.strip()]
    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]

    specs = build_scenario_matrix(
        transports=transports,
        scenarios=scenarios,
        output_dir=args.output_dir,
        server_host=args.server_host,
        base_port=args.base_port,
        interface=args.interface,
        duration=args.duration,
    )

    manifest = build_manifest(specs, sudo=False)

    output: dict[str, Any] = {
        "server_host": args.server_host,
        "base_port": args.base_port,
        "output_dir": args.output_dir,
        "entry_count": len(manifest),
        "entries": manifest,
    }

    if args.manifest_file:
        Path(args.manifest_file).write_text(
            json.dumps(output, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Manifest written to {args.manifest_file}")
    else:
        print(json.dumps(output, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Subcommand: run
# ---------------------------------------------------------------------------


def _run_run(args: argparse.Namespace) -> None:
    transports = [t.strip() for t in args.transports.split(",") if t.strip()]
    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]

    specs = build_scenario_matrix(
        transports=transports,
        scenarios=scenarios,
        output_dir=args.output_dir,
        server_host=args.server_host,
        base_port=args.base_port,
        interface=args.interface,
        duration=args.duration,
    )

    manifest = build_manifest(specs, sudo=args.sudo if hasattr(args, "sudo") else False)

    if not manifest:
        print("No valid scenarios to run.", file=sys.stderr)
        sys.exit(1)

    # Check tool availability
    from scripts.trace_capture import detect_tools

    tools = detect_tools()
    can_capture = tools.get("tcpdump", False) and tools.get("timeout", False)
    can_convert = tools.get("tshark", False)

    if args.dry_run:
        print(f"[dry-run] Would run {len(manifest)} scenario(s):")
        for entry in manifest:
            print(f"  {entry['transport']}/{entry['scenario']} :{entry['server_port']}")
            print(f"    pcap  → {entry['pcap_path']}")
            print(f"    csv   → {entry['csv_path']}")
            print(f"    report → {entry['report_path']}")
            if entry["scenario_command"] and not entry["scenario_command"].startswith("#"):
                print(f"    cmd   → {entry['scenario_command']}")
        return

    # --execute path
    if not can_capture:
        print(
            "Error: tcpdump and/or timeout not found. "
            "Use --dry-run to preview the plan.",
            file=sys.stderr,
        )
        sys.exit(4)

    for entry in manifest:
        print(f"\n{'='*60}")
        print(f"Running: {entry['transport']}/{entry['scenario']} "
              f"on port {entry['server_port']}")
        print(f"{'='*60}")

        # 1. Ensure output directory exists
        Path(entry["pcap_path"]).parent.mkdir(parents=True, exist_ok=True)

        # 2. Capture (tcpdump in background)
        capture_tokens = entry["capture_command"].split()
        print(f"[capture] {' '.join(capture_tokens)}")
        # We run tcpdump via timeout; it exits after duration seconds
        capture_proc = subprocess.Popen(
            capture_tokens,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # 3. Run scenario command (if any)
        scenario_cmd = entry["scenario_command"]
        if scenario_cmd and not scenario_cmd.startswith("#"):
            # Brief sleep to let tcpdump start
            import time
            time.sleep(0.5)
            print(f"[scenario] {scenario_cmd}")
            try:
                subprocess.run(
                    scenario_cmd,
                    shell=True,
                    timeout=entry.get("duration", 10) + 5,
                )
            except subprocess.TimeoutExpired:
                print("  (scenario command timed out)")
            except Exception as exc:
                print(f"  (scenario command failed: {exc})")

        # 4. Wait for capture to finish
        try:
            capture_proc.wait(timeout=entry.get("duration", 10) + 10)
        except subprocess.TimeoutExpired:
            capture_proc.kill()
            capture_proc.wait()

        # 5. Convert pcap → CSV
        if can_convert:
            convert_tokens = entry["convert_command"].split()
            print(f"[convert] {' '.join(convert_tokens)}")
            try:
                subprocess.run(
                    convert_tokens,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except subprocess.CalledProcessError as exc:
                print(f"  convert failed: {exc.stderr}")
                continue
        else:
            print("[convert] tshark not found — skipping CSV generation")

        # 6. Report
        report_tokens = entry["report_command"]
        print(f"[report] {report_tokens}")
        try:
            subprocess.run(
                report_tokens.split(),
                check=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as exc:
            print(f"  report failed with code {exc.returncode}")

    print(f"\nDone. Processed {len(manifest)} scenario(s).")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="VPN-LLM batch trace scenario runner.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # ---- plan --------------------------------------------------------------
    plan_p = sub.add_parser("plan", help="Generate a capture plan (manifest).")
    plan_p.add_argument(
        "--transports", required=True,
        help="Comma-separated transports: tcp,tls,websocket,ssh.",
    )
    plan_p.add_argument(
        "--scenarios", required=True,
        help="Comma-separated scenarios: idle,ping,curl,bulk,reconnect.",
    )
    plan_p.add_argument(
        "--output-dir", default="traces",
        help="Base output directory (default: traces).",
    )
    plan_p.add_argument(
        "--server-host", default="127.0.0.1",
        help="Server IP (default: 127.0.0.1).",
    )
    plan_p.add_argument(
        "--base-port", type=int, default=_DEFAULT_BASE_PORT,
        help=f"Starting port for transport index (default: {_DEFAULT_BASE_PORT}).",
    )
    plan_p.add_argument(
        "--interface", default="lo",
        help="Network interface (default: lo).",
    )
    plan_p.add_argument(
        "--duration", type=int, default=10,
        help="Capture duration per scenario in seconds (default: 10).",
    )
    plan_p.add_argument(
        "--manifest-file", default=None,
        help="Write manifest JSON to this file instead of stdout.",
    )

    # ---- run ---------------------------------------------------------------
    run_p = sub.add_parser("run", help="Execute capture → convert → report.")
    run_p.add_argument(
        "--transports", required=True,
        help="Comma-separated transports: tcp,tls,websocket,ssh.",
    )
    run_p.add_argument(
        "--scenarios", required=True,
        help="Comma-separated scenarios: idle,ping,curl,bulk,reconnect.",
    )
    run_p.add_argument(
        "--output-dir", default="traces",
        help="Base output directory (default: traces).",
    )
    run_p.add_argument(
        "--server-host", default="127.0.0.1",
        help="Server IP (default: 127.0.0.1).",
    )
    run_p.add_argument(
        "--base-port", type=int, default=_DEFAULT_BASE_PORT,
        help=f"Starting port for transport index (default: {_DEFAULT_BASE_PORT}).",
    )
    run_p.add_argument(
        "--interface", default="lo",
        help="Network interface (default: lo).",
    )
    run_p.add_argument(
        "--duration", type=int, default=10,
        help="Capture duration per scenario in seconds (default: 10).",
    )
    run_p.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Print plan without executing (default).",
    )
    run_p.add_argument(
        "--execute", action="store_true", default=False,
        help="Really execute the scenarios (requires tcpdump/tshark).",
    )

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "plan":
        _run_plan(args)
    elif args.subcommand == "run":
        # --dry-run is enabled by default; --execute must be explicit
        if not args.execute:
            args.dry_run = True
        _run_run(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
