#!/usr/bin/env python3
"""Phase 9: Real TUN/netns Before/After Trace Matrix.

Uses real Linux netns + TUN to capture VPN-LLM tunnel traffic before and after
traffic shaping, producing a fingerprint comparison matrix.

Usage:
    python3 scripts/run_phase9_real_trace_matrix.py env-check
    python3 scripts/run_phase9_real_trace_matrix.py plan --output-dir outputs/phase9_real_matrix --transports tcp,tls,websocket --scenarios idle,ping
    python3 scripts/run_phase9_real_trace_matrix.py run --output-dir outputs/phase9_real_matrix --transports tcp --scenarios idle --dry-run
    python3 scripts/run_phase9_real_trace_matrix.py run --output-dir outputs/phase9_real_matrix --transports tcp --scenarios idle --execute
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_SUPPORTED_TRANSPORTS = frozenset({"tcp", "tls", "websocket", "ssh"})
_VALID_SCENARIOS = frozenset({"idle", "ping", "curl", "bulk", "reconnect"})
_SESSION_ID = "00112233445566778899aabbccddeeff"

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_list(arg: str, valid: frozenset[str]) -> list[str]:
    result = [x.strip() for x in arg.split(",") if x.strip()]
    filtered = [x for x in result if x in valid]
    for x in result:
        if x not in valid:
            print(f"Warning: ignoring unknown value '{x}'", file=sys.stderr)
    return filtered


# ---------------------------------------------------------------------------
# Step 1: Environment check
# ---------------------------------------------------------------------------


def _check_executable(name: str) -> Optional[str]:
    return shutil.which(name)


def _check_python_module(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def _can_create_netns() -> tuple[bool, str]:
    """Probe whether we can create a real network namespace."""
    probe = f"vpn_phase9_probe_{os.getpid()}"
    try:
        result = subprocess.run(
            ["sudo", "-S", "ip", "netns", "add", probe],
            capture_output=True, text=True, timeout=10,
            input="test2026\n",
        )
        if result.returncode == 0:
            subprocess.run(["sudo", "-S", "ip", "netns", "delete", probe],
                           capture_output=True, text=True, timeout=5, input="test2026\n")
            return True, ""
        return False, result.stderr.strip() or result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "sudo ip netns add timed out"
    except FileNotFoundError:
        return False, "ip command not found"


def run_env_check() -> dict[str, Any]:
    """Check the experimental environment and return a structured report."""
    result: dict[str, Any] = {
        "timestamp": _now_iso(),
        "hostname": os.uname().nodename,
        "kernel": os.uname().release,
        "arch": os.uname().machine,
        "user": os.environ.get("USER", ""),
        "checks": {},
    }

    # TUN device
    tun_exists = Path("/dev/net/tun").exists()
    result["checks"]["dev_net_tun"] = tun_exists

    # Tools
    for tool in ["tcpdump", "tshark", "timeout", "ping", "ip"]:
        result["checks"][tool] = _check_executable(tool) is not None

    # Python modules
    for mod in ["websockets", "paramiko", "yaml"]:
        result["checks"][f"python_{mod}"] = _check_python_module(mod)

    # Netns capability
    can_ns, ns_err = _can_create_netns()
    result["checks"]["can_create_netns"] = can_ns
    if ns_err:
        result["checks"]["netns_error"] = ns_err

    # Summary verdicts
    result["can_run_real_tcp"] = (
        tun_exists and can_ns and result["checks"]["tcpdump"] and result["checks"]["tshark"]
    )
    result["can_run_real_websocket"] = (
        result["can_run_real_tcp"] and result["checks"]["python_websockets"]
    )
    result["can_run_real_tls"] = result["can_run_real_tcp"]
    result["can_run_real_ssh"] = (
        result["can_run_real_tcp"] and result["checks"]["python_paramiko"]
    )

    return result


def _print_env_check(result: dict[str, Any]) -> None:
    checks = result["checks"]
    for key, val in checks.items():
        if key == "netns_error":
            continue
        status = "ok" if val else "MISSING"
        print(f"  {key:30s} {status}")
    print()
    print(f"  can_run_real_tcp:         {result['can_run_real_tcp']}")
    print(f"  can_run_real_tls:         {result['can_run_real_tls']}")
    print(f"  can_run_real_websocket:   {result['can_run_real_websocket']}")
    print(f"  can_run_real_ssh:         {result['can_run_real_ssh']}")


# ---------------------------------------------------------------------------
# Netns lifecycle
# ---------------------------------------------------------------------------

_NS_SRV = "vpn_phase9_srv"
_NS_CLI = "vpn_phase9_cli"


def _sudo(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sudo", "-S"] + cmd,
        capture_output=True, text=True, timeout=timeout,
        input="test2026\n",
    )


def _netns_cleanup() -> None:
    """Delete Phase 9 namespaces if they exist."""
    for ns in [_NS_SRV, _NS_CLI]:
        _sudo(["ip", "netns", "delete", ns], timeout=10)


def _netns_setup() -> bool:
    """Create network namespaces and veth pair. Returns True on success."""
    _netns_cleanup()

    for ns in [_NS_SRV, _NS_CLI]:
        r = _sudo(["ip", "netns", "add", ns], timeout=10)
        if r.returncode != 0:
            print(f"Error: failed to create namespace {ns}: {r.stderr}", file=sys.stderr)
            _netns_cleanup()
            return False

    # veth pair
    r = _sudo(["ip", "link", "add", "veth_srv", "type", "veth", "peer", "name", "veth_cli"], timeout=10)
    if r.returncode != 0:
        print(f"Error: failed to create veth pair: {r.stderr}", file=sys.stderr)
        _netns_cleanup()
        return False

    _sudo(["ip", "link", "set", "veth_srv", "netns", _NS_SRV], timeout=10)
    _sudo(["ip", "link", "set", "veth_cli", "netns", _NS_CLI], timeout=10)

    _sudo(["ip", "netns", "exec", _NS_SRV, "ip", "addr", "add", "192.168.200.1/24", "dev", "veth_srv"], timeout=10)
    _sudo(["ip", "netns", "exec", _NS_SRV, "ip", "link", "set", "lo", "up"], timeout=10)
    _sudo(["ip", "netns", "exec", _NS_SRV, "ip", "link", "set", "veth_srv", "up"], timeout=10)

    _sudo(["ip", "netns", "exec", _NS_CLI, "ip", "addr", "add", "192.168.200.2/24", "dev", "veth_cli"], timeout=10)
    _sudo(["ip", "netns", "exec", _NS_CLI, "ip", "link", "set", "lo", "up"], timeout=10)
    _sudo(["ip", "netns", "exec", _NS_CLI, "ip", "link", "set", "veth_cli", "up"], timeout=10)

    return True


def _configure_tun_ips() -> None:
    _sudo(["ip", "netns", "exec", _NS_SRV, "ip", "addr", "add", "10.8.0.1", "peer", "10.8.0.2/32", "dev", "tun0"], timeout=10)
    _sudo(["ip", "netns", "exec", _NS_SRV, "ip", "link", "set", "tun0", "up"], timeout=10)
    _sudo(["ip", "netns", "exec", _NS_CLI, "ip", "addr", "add", "10.8.0.2", "peer", "10.8.0.1/32", "dev", "tun1"], timeout=10)
    _sudo(["ip", "netns", "exec", _NS_CLI, "ip", "link", "set", "tun1", "up"], timeout=10)
    _sudo(["ip", "netns", "exec", _NS_SRV, "ip", "route", "add", "10.8.0.2", "dev", "tun0"], timeout=10)
    _sudo(["ip", "netns", "exec", _NS_CLI, "ip", "route", "add", "10.8.0.1", "dev", "tun1"], timeout=10)


def _kill_server_client() -> None:
    """Kill any lingering server/client processes."""
    for ns in [_NS_SRV, _NS_CLI]:
        _sudo(["ip", "netns", "exec", ns, "pkill", "-f", "src.server"], timeout=10)
        _sudo(["ip", "netns", "exec", ns, "pkill", "-f", "src.client"], timeout=10)
    time.sleep(0.3)


def _get_config_for_phase(transport: str, phase: str) -> tuple[str, str]:
    """Return (server_config, client_config) paths for a transport+phase."""
    server_cfg = f"config/server_netns.yaml"
    if phase == "before":
        client_cfg = f"config/client_netns.yaml"
    else:
        client_cfg = f"config/examples/shaping_padding_aggregation_netns.yaml"
    return server_cfg, client_cfg


# ---------------------------------------------------------------------------
# Scenario commands
# ---------------------------------------------------------------------------


def _scenario_command(scenario: str) -> str:
    commands: dict[str, str] = {
        "idle": "sleep 5",
        "ping": "ping -c 5 -W 2 10.8.0.1",
        "curl": "curl -s -o /dev/null --connect-timeout 5 http://10.8.0.1:8080/ 2>/dev/null || echo 'no http server'",
        "bulk": "dd if=/dev/zero bs=1K count=100 2>/dev/null | timeout 5 nc -w 3 10.8.0.1 8080 2>/dev/null || echo 'no tcp server'",
        "reconnect": "for i in 1 2 3; do sleep 1; done",
    }
    return commands.get(scenario, "sleep 3")


# ---------------------------------------------------------------------------
# Plan subcommand
# ---------------------------------------------------------------------------


def build_matrix(
    transports: list[str],
    scenarios: list[str],
    output_dir: str,
    phases: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Build the before/after experiment matrix."""
    if phases is None:
        phases = ["before", "after"]

    entries: list[dict[str, Any]] = []
    for transport in transports:
        for scenario in scenarios:
            for phase in phases:
                dir_name = "before" if phase == "before" else "after"
                pcap_path = f"{output_dir}/{dir_name}/{transport}/{scenario}.pcap"
                csv_path = f"{output_dir}/{dir_name}/{transport}/{scenario}.csv"
                report_path = f"{output_dir}/{dir_name}/{transport}/{scenario}.report.json"

                entries.append({
                    "transport": transport,
                    "scenario": scenario,
                    "phase": phase,
                    "pcap_path": pcap_path,
                    "csv_path": csv_path,
                    "report_path": report_path,
                    "scenario_command": _scenario_command(scenario),
                    "capture_interface": "veth_srv",
                    "capture_host": "192.168.200.1",
                })

    return entries


def _run_plan(args: argparse.Namespace) -> None:
    transports = _parse_list(args.transports, _SUPPORTED_TRANSPORTS)
    scenarios = _parse_list(args.scenarios, _VALID_SCENARIOS)

    if not transports:
        print("Error: no valid transports specified", file=sys.stderr)
        sys.exit(1)
    if not scenarios:
        print("Error: no valid scenarios specified", file=sys.stderr)
        sys.exit(1)

    entries = build_matrix(transports, scenarios, args.output_dir)
    manifest = {
        "generated_at": _now_iso(),
        "output_dir": args.output_dir,
        "transports": transports,
        "scenarios": scenarios,
        "phases": ["before", "after"],
        "total_entries": len(entries),
        "entries": entries,
    }

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"Manifest: {len(entries)} entries written to {manifest_path}")

    if args.verbose:
        for e in entries:
            print(f"  {e['transport']}/{e['scenario']}/{e['phase']} → {e['report_path']}")


# ---------------------------------------------------------------------------
# Run subcommand
# ---------------------------------------------------------------------------


def _start_server(transport: str, server_config: str) -> Optional[subprocess.Popen]:
    """Start VPN server in server namespace. Returns Popen or None on failure."""
    log_path = f"/tmp/vpn_phase9_server_{transport}.log"
    cmd = (
        f"cd {_REPO_ROOT} && "
        f"python3 -m src.server --config {server_config} --transport {transport} "
        f"--session-id {_SESSION_ID}"
    )
    try:
        proc = subprocess.Popen(
            ["sudo", "-S", "ip", "netns", "exec", _NS_SRV, "bash", "-c", cmd],
            stdout=open(log_path, "w"), stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        return proc
    except Exception as exc:
        print(f"  Error starting server: {exc}", file=sys.stderr)
        return None


def _wait_server_ready(transport: str, timeout: int = 15) -> bool:
    """Wait for server to be ready (port listening)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.5)
        r = _sudo(
            ["ip", "netns", "exec", _NS_SRV, "ss", "-tlnp"],
            timeout=5,
        )
        if "2222" in r.stdout:
            return True
    return False


def _start_client(transport: str, client_config: str) -> Optional[subprocess.Popen]:
    """Start VPN client in client namespace. Returns Popen or None on failure."""
    log_path = f"/tmp/vpn_phase9_client_{transport}.log"
    cmd = (
        f"cd {_REPO_ROOT} && "
        f"python3 -m src.client --config {client_config} --transport {transport} "
        f"--session-id {_SESSION_ID}"
    )
    try:
        proc = subprocess.Popen(
            ["sudo", "-S", "ip", "netns", "exec", _NS_CLI, "bash", "-c", cmd],
            stdout=open(log_path, "w"), stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        return proc
    except Exception as exc:
        print(f"  Error starting client: {exc}", file=sys.stderr)
        return None


def _wait_client_connected(transport: str, timeout: int = 15) -> bool:
    """Wait for client to connect (tunnel established)."""
    log_path = f"/tmp/vpn_phase9_client_{transport}.log"
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.5)
        try:
            content = Path(log_path).read_text()
            if "Tunnel established" in content or "tunnel established" in content:
                return True
        except OSError:
            pass
    return False


def _capture_traffic(
    output_pcap: str,
    interface: str = "veth_srv",
    host: str = "192.168.200.1",
    duration: int = 15,
) -> Optional[subprocess.Popen]:
    """Start tcpdump capture in server namespace on veth interface. Returns Popen."""
    from scripts.trace_capture import build_tcpdump_command

    cmd = build_tcpdump_command(
        interface=interface,
        output_pcap=output_pcap,
        host=host,
        duration=duration,
        sudo=False,
    )
    # Remove 'timeout N' prefix since we handle duration ourselves
    # cmd = ["timeout", str(duration), "tcpdump", ...]
    try:
        proc = subprocess.Popen(
            ["sudo", "-S", "ip", "netns", "exec", _NS_SRV] + cmd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        return proc
    except Exception as exc:
        print(f"  Error starting tcpdump: {exc}", file=sys.stderr)
        return None


def _run_scenario_in_client(scenario: str, transport: str) -> bool:
    """Run the scenario command in client namespace."""
    cmd = _scenario_command(scenario)
    try:
        r = _sudo(
            ["ip", "netns", "exec", _NS_CLI, "bash", "-c", cmd],
            timeout=30,
        )
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _convert_pcap(pcap_path: str, csv_path: str,
                   client_host: str = "192.168.200.2",
                   server_host: str = "192.168.200.1",
                   server_port: int = 2222) -> bool:
    """Convert pcap to CSV using tshark."""
    from scripts.trace_capture import build_tshark_csv_command, parse_tshark_text, normalize_trace_rows, rows_to_csv_text

    cmd = build_tshark_csv_command(
        input_pcap=pcap_path,
        output_csv=csv_path,
        client_host=client_host,
        server_host=server_host,
    )
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print(f"    tshark failed: {r.stderr}", file=sys.stderr)
            return False

        raw_rows = parse_tshark_text(r.stdout)
        rows = normalize_trace_rows(
            raw_rows,
            client_host=client_host,
            server_host=server_host,
            server_port=server_port,
        )
        csv_text = rows_to_csv_text(rows)
        Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
        Path(csv_path).write_text(csv_text)
        return True
    except Exception as exc:
        print(f"    convert error: {exc}", file=sys.stderr)
        return False


def _run_fingerprint_report(csv_path: str, report_path: str) -> bool:
    """Run fingerprint report on CSV."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "src.evaluation.fingerprint.report",
             "--input", csv_path, "--output", report_path],
            capture_output=True, text=True, timeout=30,
            cwd=str(_REPO_ROOT),
        )
        return r.returncode == 0
    except Exception as exc:
        print(f"    report error: {exc}", file=sys.stderr)
        return False


def _run_one_entry(
    entry: dict[str, Any],
    env: dict[str, Any],
    execute: bool = False,
) -> dict[str, Any]:
    """Execute a single experiment entry. Returns status dict."""
    transport = entry["transport"]
    scenario = entry["scenario"]
    phase = entry["phase"]
    pcap_path = entry["pcap_path"]
    csv_path = entry["csv_path"]
    report_path = entry["report_path"]

    started_at = _now_iso()
    result: dict[str, Any] = {
        "transport": transport,
        "scenario": scenario,
        "phase": phase,
        "trace_type": "skipped",
        "pcap_path": pcap_path,
        "csv_path": csv_path,
        "report_path": report_path,
        "scenario_command": entry["scenario_command"],
        "started_at": started_at,
        "ended_at": None,
        "duration_s": 0,
        "status": "skipped",
        "error_reason": "",
    }

    # Check if this transport is runnable
    can_run_key = f"can_run_real_{transport}"
    if not env.get("checks", {}).get("can_create_netns", False):
        result["error_reason"] = "cannot create netns"
        return result
    if not env.get(can_run_key, False):
        result["error_reason"] = f"transport {transport} not runnable (missing tools or modules)"
        return result

    if not execute:
        result["trace_type"] = "synthetic"
        result["status"] = "dry_run"
        result["error_reason"] = "dry-run: would execute with netns"
        return result

    # ---- Real execution below ----
    t0 = time.time()

    try:
        # Ensure output directory
        Path(pcap_path).parent.mkdir(parents=True, exist_ok=True)

        # Setup netns
        if not _netns_setup():
            result["error_reason"] = "netns setup failed"
            result["ended_at"] = _now_iso()
            result["duration_s"] = time.time() - t0
            return result

        # Get configs
        server_cfg, client_cfg = _get_config_for_phase(transport, phase)

        # Start server
        server_proc = _start_server(transport, server_cfg)
        if server_proc is None:
            result["error_reason"] = "server start failed"
            _netns_cleanup()
            result["ended_at"] = _now_iso()
            result["duration_s"] = time.time() - t0
            return result

        if not _wait_server_ready(transport):
            result["error_reason"] = "server not ready"
            _kill_server_client()
            _netns_cleanup()
            result["ended_at"] = _now_iso()
            result["duration_s"] = time.time() - t0
            return result

        # Start client
        client_proc = _start_client(transport, client_cfg)
        if client_proc is None:
            result["error_reason"] = "client start failed"
            _kill_server_client()
            _netns_cleanup()
            result["ended_at"] = _now_iso()
            result["duration_s"] = time.time() - t0
            return result

        if not _wait_client_connected(transport):
            result["error_reason"] = "client not connected"
            _kill_server_client()
            _netns_cleanup()
            result["ended_at"] = _now_iso()
            result["duration_s"] = time.time() - t0
            return result

        # Configure TUN IPs (after client creates the TUN devices)
        _configure_tun_ips()

        # Start capture
        duration = 15 if scenario != "idle" else 10
        cap_proc = _capture_traffic(pcap_path, duration=duration)
        if cap_proc is None:
            result["error_reason"] = "tcpdump start failed"
            _kill_server_client()
            _netns_cleanup()
            result["ended_at"] = _now_iso()
            result["duration_s"] = time.time() - t0
            return result

        time.sleep(0.5)

        # Run scenario
        _run_scenario_in_client(scenario, transport)

        # Wait for capture to finish
        try:
            cap_proc.wait(timeout=duration + 10)
        except subprocess.TimeoutExpired:
            cap_proc.kill()
            cap_proc.wait()

        # Convert pcap → CSV
        if _convert_pcap(pcap_path, csv_path, client_host="192.168.200.2", server_host="192.168.200.1", server_port=2222):
            # Run fingerprint report
            if _run_fingerprint_report(csv_path, report_path):
                result["trace_type"] = "real"
                result["status"] = "ok"
            else:
                result["error_reason"] = "fingerprint report failed"
                result["status"] = "failed"
        else:
            result["error_reason"] = "pcap convert failed"
            result["status"] = "failed"

    except Exception as exc:
        result["error_reason"] = str(exc)
        result["status"] = "failed"
    finally:
        _kill_server_client()
        _netns_cleanup()

    result["ended_at"] = _now_iso()
    result["duration_s"] = round(time.time() - t0, 1)
    return result


def _run_batch(
    entries: list[dict[str, Any]],
    env: dict[str, Any],
    execute: bool = False,
    output_dir: str = "outputs/phase9_real_matrix",
) -> None:
    """Run a batch of experiment entries."""
    log_dir = Path(output_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    total = len(entries)
    for i, entry in enumerate(entries):
        label = f"{entry['transport']}/{entry['scenario']}/{entry['phase']}"
        if execute:
            print(f"[{i+1}/{total}] Running: {label} ...", flush=True)
        else:
            print(f"[{i+1}/{total}] dry-run: {label}")
        result = _run_one_entry(entry, env, execute=execute)
        results.append(result)
        status = result["status"]
        err = f" ({result['error_reason']})" if result.get("error_reason") else ""
        print(f"  → {status}{err}")

    # Write results manifest
    results_path = Path(output_dir) / "results.json"
    results_path.write_text(json.dumps({
        "generated_at": _now_iso(),
        "execute": execute,
        "total": len(results),
        "ok": sum(1 for r in results if r["status"] == "ok"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "dry_run": sum(1 for r in results if r["status"] == "dry_run"),
        "results": results,
    }, indent=2))

    # Write commands log for reproducibility
    cmd_log_path = log_dir / "commands.txt"
    lines = [f"# Phase 9 {'execute' if execute else 'dry-run'} — {_now_iso()}", ""]
    for r in results:
        lines.append(f"# {r['transport']}/{r['scenario']}/{r['phase']}: {r['status']}")
        lines.append(f"  scenario_command: {r['scenario_command']}")
        lines.append(f"  pcap:  {r['pcap_path']}")
        lines.append(f"  csv:   {r['csv_path']}")
        lines.append(f"  report: {r['report_path']}")
        if r.get("error_reason"):
            lines.append(f"  error: {r['error_reason']}")
        lines.append("")
    cmd_log_path.write_text("\n".join(lines))


def _run_run(args: argparse.Namespace) -> None:
    transports = _parse_list(args.transports, _SUPPORTED_TRANSPORTS)
    scenarios = _parse_list(args.scenarios, _VALID_SCENARIOS)

    if not transports:
        print("Error: no valid transports", file=sys.stderr)
        sys.exit(1)
    if not scenarios:
        print("Error: no valid scenarios", file=sys.stderr)
        sys.exit(1)

    entries = build_matrix(transports, scenarios, args.output_dir)
    env = run_env_check()

    # Always run env-check first
    print("=== Environment check ===")
    _print_env_check(env)
    print()

    if args.dry_run or not args.execute:
        print(f"=== Dry-run: {len(entries)} entries ===")
        if args.verbose:
            for e in entries:
                print(f"  {e['transport']}/{e['scenario']}/{e['phase']}"
                      f" → {e['pcap_path']}")
        _run_batch(entries, env, execute=False, output_dir=args.output_dir)
        print(f"\nDry-run complete. {len(entries)} entries planned.")
        print("Use --execute to run real experiments.")
    else:
        if not env.get("can_run_real_tcp", False):
            print("Error: environment not ready for real execution.", file=sys.stderr)
            print("Missing: TUN device, netns capability, tcpdump, or tshark.", file=sys.stderr)
            sys.exit(1)

        print(f"=== Execute: {len(entries)} entries ===")
        _run_batch(entries, env, execute=True, output_dir=args.output_dir)

        # After execution, run comparison if both before and after have data
        _run_comparison(args.output_dir)


# ---------------------------------------------------------------------------
# Before/After comparison
# ---------------------------------------------------------------------------


def _load_report_safe(path: str) -> Optional[dict[str, Any]]:
    """Load a fingerprint report JSON, returning None on failure."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _run_comparison(output_dir: str) -> None:
    """Compare before/after fingerprint reports and write comparison files."""
    entries = []
    for transport in _SUPPORTED_TRANSPORTS:
        for scenario in _VALID_SCENARIOS:
            before_report = f"{output_dir}/before/{transport}/{scenario}.report.json"
            after_report = f"{output_dir}/after/{transport}/{scenario}.report.json"
            before = _load_report_safe(before_report)
            after = _load_report_safe(after_report)

            if before is None and after is None:
                continue

            entry = {
                "transport": transport,
                "scenario": scenario,
            }

            fields = [
                "packet_count", "fingerprint_risk_score", "risk_level",
                "small_packet_ratio", "repeated_length_ratio", "ngram_entropy",
                "dominant_ngram_ratio", "burst_count", "max_burst_size",
                "avg_inter_arrival_ms",
            ]

            for f in fields:
                b_val = before.get(f) if before else None
                a_val = after.get(f) if after else None
                entry[f"{f}_before"] = b_val
                entry[f"{f}_after"] = a_val

            # risk_score delta
            b_score = before.get("fingerprint_risk_score") if before else None
            a_score = after.get("fingerprint_risk_score") if after else None
            if b_score is not None and a_score is not None:
                entry["risk_score_delta"] = round(a_score - b_score, 4)
                delta = entry["risk_score_delta"]
                if delta < -0.05:
                    entry["verdict"] = "improved"
                elif delta > 0.05:
                    entry["verdict"] = "regressed"
                else:
                    entry["verdict"] = "unchanged"
            else:
                if before is None and after is None:
                    entry["verdict"] = "skipped"
                elif before is None or after is None:
                    entry["verdict"] = "insufficient"
                else:
                    entry["verdict"] = "insufficient"

            entry["trace_type_before"] = "real" if before else "skipped"
            entry["trace_type_after"] = "real" if after else "skipped"
            entries.append(entry)

    # Write comparison CSVs
    summaries_dir = Path(output_dir) / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)

    # Fingerprint CSVs by phase
    for phase in ["before", "after"]:
        _write_phase_summary_csv(output_dir, phase, summaries_dir)

    # Comparison CSV/JSON
    if entries:
        fieldnames = list(entries[0].keys())
        csv_path = summaries_dir / "before_after_comparison.csv"
        with csv_path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            for e in entries:
                w.writerow(e)

        json_path = summaries_dir / "before_after_comparison.json"
        json_path.write_text(json.dumps({
            "generated_at": _now_iso(),
            "entries": entries,
        }, indent=2))

        # Markdown
        md_path = summaries_dir / "before_after_comparison.md"
        _write_comparison_md(entries, md_path)

        improved = sum(1 for e in entries if e["verdict"] == "improved")
        regressed = sum(1 for e in entries if e["verdict"] == "regressed")
        unchanged = sum(1 for e in entries if e["verdict"] == "unchanged")
        insufficient = sum(1 for e in entries if e["verdict"] == "insufficient")
        skipped = sum(1 for e in entries if e["verdict"] == "skipped")

        print(f"\nComparison written to {summaries_dir}/")
        print(f"  improved: {improved}, regressed: {regressed}, "
              f"unchanged: {unchanged}, insufficient: {insufficient}, "
              f"skipped: {skipped}")

        # Generate next patch prompt if needed
        if regressed > 0 or insufficient > 0:
            _generate_patch_prompt(entries, output_dir)


def _write_phase_summary_csv(output_dir: str, phase: str, summaries_dir: Path) -> None:
    """Write a per-phase fingerprint summary CSV."""
    from scripts.summarize_fingerprint_reports import scan_reports

    input_dir = Path(output_dir) / phase
    if not input_dir.is_dir():
        return

    rows = scan_reports(str(input_dir))
    path = summaries_dir / f"fingerprint_{phase}.csv"
    with path.open("w", newline="") as fh:
        fields = [
            "transport", "scenario", "trace_type", "packet_count",
            "risk_level", "fingerprint_risk_score",
            "small_packet_ratio", "repeated_length_ratio",
            "ngram_entropy", "dominant_ngram_ratio",
            "burst_count", "max_burst_size",
        ]
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _write_comparison_md(entries: list[dict[str, Any]], md_path: Path) -> None:
    """Write comparison markdown."""
    lines = [
        "# Phase 9: Before/After Trace Comparison",
        "",
        f"Generated: {_now_iso()}",
        "",
        "| Transport | Scenario | Before Score | After Score | Delta | Before Level | After Level | Verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in entries:
        lines.append(
            f"| {e['transport']} | {e['scenario']} | "
            f"{e.get('fingerprint_risk_score_before', 'N/A')} | "
            f"{e.get('fingerprint_risk_score_after', 'N/A')} | "
            f"{e.get('risk_score_delta', 'N/A')} | "
            f"{e.get('risk_level_before', 'N/A')} | "
            f"{e.get('risk_level_after', 'N/A')} | "
            f"**{e.get('verdict', 'N/A')}** |"
        )
    md_path.write_text("\n".join(lines))


def _generate_patch_prompt(entries: list[dict[str, Any]], output_dir: str) -> None:
    """Generate next LLM patch loop prompt from regressed/insufficient entries."""
    regressed = [e for e in entries if e["verdict"] in ("regressed", "insufficient")]
    if not regressed:
        return

    prompt_lines = [
        "Improve VPN-LLM traffic shaping based on Phase 9 real trace matrix regressions.",
        "",
        "The following transport/scenario combinations showed regressed or insufficient",
        "fingerprint risk scores after enabling shaping (padding + aggregation + jitter):",
        "",
    ]
    for e in regressed:
        prompt_lines.append(
            f"- {e['transport']}/{e['scenario']}: "
            f"before={e.get('fingerprint_risk_score_before', 'N/A')}, "
            f"after={e.get('fingerprint_risk_score_after', 'N/A')}, "
            f"delta={e.get('risk_score_delta', 'N/A')}"
        )

    prompt_lines += [
        "",
        "Relevant metrics to address:",
        "- small_packet_ratio",
        "- repeated_length_ratio",
        "- ngram_entropy",
        "- burst_count",
        "- avg_inter_arrival_ms",
        "",
        "Countermeasure modules available:",
        "- src/shaping/padding.py",
        "- src/shaping/aggregation.py",
        "- src/shaping/jitter.py",
        "- src/shaping/fragmentation.py",
        "- src/shaping/scheduler.py",
        "- src/shaping/timing.py",
    ]

    prompt_path = Path(output_dir) / "next_patch_prompt.txt"
    prompt_path.write_text("\n".join(prompt_lines))
    print(f"Next patch prompt written to {prompt_path}")

    result_path = Path(output_dir) / "patch_loop_result.json"
    result_path.write_text(json.dumps({
        "generated_at": _now_iso(),
        "regressed_count": len(regressed),
        "regressed_entries": regressed,
        "prompt_path": str(prompt_path),
    }, indent=2))


# ---------------------------------------------------------------------------
# Skipped transport report
# ---------------------------------------------------------------------------


def _write_skipped_report(output_dir: str, env: dict[str, Any]) -> None:
    """Write a report of which transports were skipped and why."""
    skipped = {}
    for transport in ["tcp", "tls", "websocket", "ssh"]:
        key = f"can_run_real_{transport}"
        if not env.get(key, False):
            reasons = []
            if not env.get("checks", {}).get("dev_net_tun"):
                reasons.append("/dev/net/tun missing")
            if not env.get("checks", {}).get("can_create_netns"):
                reasons.append("cannot create netns")
            if not env.get("checks", {}).get("tcpdump"):
                reasons.append("tcpdump missing")
            if not env.get("checks", {}).get("tshark"):
                reasons.append("tshark missing")
            if transport == "websocket" and not env.get("checks", {}).get("python_websockets"):
                reasons.append("websockets module missing")
            if transport == "ssh" and not env.get("checks", {}).get("python_paramiko"):
                reasons.append("paramiko module missing")
            skipped[transport] = {"skipped": True, "reasons": reasons}
        else:
            skipped[transport] = {"skipped": False, "reasons": []}

    path = Path(output_dir) / "summaries" / "skipped.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(skipped, indent=2))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 9: Real TUN/netns Before/After Trace Matrix",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # env-check
    env_p = sub.add_parser("env-check", help="Check experimental environment.")
    env_p.add_argument("--json", action="store_true", help="Output as JSON.")

    # plan
    plan_p = sub.add_parser("plan", help="Generate experiment matrix plan.")
    plan_p.add_argument("--output-dir", default="outputs/phase9_real_matrix")
    plan_p.add_argument("--transports", required=True)
    plan_p.add_argument("--scenarios", required=True)
    plan_p.add_argument("--verbose", action="store_true")

    # run
    run_p = sub.add_parser("run", help="Run experiments (dry-run by default).")
    run_p.add_argument("--output-dir", default="outputs/phase9_real_matrix")
    run_p.add_argument("--transports", required=True)
    run_p.add_argument("--scenarios", required=True)
    run_p.add_argument("--dry-run", action="store_true", default=False)
    run_p.add_argument("--execute", action="store_true", default=False)
    run_p.add_argument("--verbose", action="store_true")

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "env-check":
        result = run_env_check()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            _print_env_check(result)
    elif args.subcommand == "plan":
        _run_plan(args)
    elif args.subcommand == "run":
        if not args.execute:
            args.dry_run = True
        _run_run(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
