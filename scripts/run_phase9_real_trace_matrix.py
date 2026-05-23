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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_SUPPORTED_TRANSPORTS = frozenset({"tcp", "tls", "websocket", "ssh", "http2"})
_HTTP2_DEFAULT_PORT = 2225
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


@dataclass
class RuntimeConfig:
    """Phase 9E runtime parameters for scenario execution."""
    capture_duration: int = 30
    ping_count: int = 20
    ping_interval: float = 0.1
    curl_count: int = 10
    bulk_bytes: int = 1048576
    min_packet_count: int = 30
    scenario_timeout: int = 60
    repeat_count: int = 1
    curl_connect_timeout: int = 30
    curl_max_time: int = 60
    http_server_startup_timeout: int = 10
    post_scenario_wait: int = 2
    bulk_read_timeout: int = 60
    http2_aware_shaping: bool = False


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
    for tool in ["tcpdump", "tshark", "timeout", "ping", "ip", "curl"]:
        result["checks"][tool] = _check_executable(tool) is not None

    # Python modules
    for mod in ["websockets", "paramiko", "yaml", "h2", "hpack", "hyperframe"]:
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

    # HTTP/2 dependency status (experimental)
    h2_available = result["checks"].get("python_h2", False)
    hpack_available = result["checks"].get("python_hpack", False)
    hyperframe_available = result["checks"].get("python_hyperframe", False)
    result["http2_dependency"] = {
        "h2_available": h2_available,
        "hpack_available": hpack_available,
        "hyperframe_available": hyperframe_available,
        "http2_runnable": h2_available and hpack_available and hyperframe_available,
    }
    result["can_run_real_http2"] = result["http2_dependency"]["http2_runnable"] and result["can_run_real_tcp"]

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
    print(f"  can_run_real_http2:       {result['can_run_real_http2']}")
    if "http2_dependency" in result:
        h2d = result["http2_dependency"]
        print(f"  http2_dependency:")
        print(f"    h2_available:            {h2d['h2_available']}")
        print(f"    hpack_available:         {h2d['hpack_available']}")
        print(f"    hyperframe_available:    {h2d['hyperframe_available']}")
        print(f"    http2_runnable:          {h2d['http2_runnable']}")


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
    """Configure TUN IPs and bring interfaces UP.

    Client TUN is brought UP first so that when the server TUN comes UP and
    the kernel potentially queues IPv6 DAD/NDP packets, the client is ready
    to receive them — avoiding TUN write EIO errors.

    IPv6 is disabled on both TUN interfaces to prevent periodic Router
    Solicitations / Neighbor Discovery from continuously resetting the
    HEARTBEAT timer via TUN reads.
    """
    # Step 1: assign IPs to both TUN devices
    _sudo(["ip", "netns", "exec", _NS_SRV, "ip", "addr", "add", "10.8.0.1", "peer", "10.8.0.2/32", "dev", "tun0"], timeout=10)
    _sudo(["ip", "netns", "exec", _NS_CLI, "ip", "addr", "add", "10.8.0.2", "peer", "10.8.0.1/32", "dev", "tun1"], timeout=10)
    # Step 2: bring client TUN UP first, then server
    _sudo(["ip", "netns", "exec", _NS_CLI, "ip", "link", "set", "tun1", "up"], timeout=10)
    _sudo(["ip", "netns", "exec", _NS_SRV, "ip", "link", "set", "tun0", "up"], timeout=10)
    # Step 3: disable IPv6 on TUN interfaces (prevents periodic ND/RS from resetting HEARTBEAT timer)
    _sudo(["ip", "netns", "exec", _NS_SRV, "sysctl", "-w", "net.ipv6.conf.tun0.disable_ipv6=1"], timeout=5)
    _sudo(["ip", "netns", "exec", _NS_CLI, "sysctl", "-w", "net.ipv6.conf.tun1.disable_ipv6=1"], timeout=5)
    # Step 4: add routes after both interfaces are UP
    _sudo(["ip", "netns", "exec", _NS_SRV, "ip", "route", "add", "10.8.0.2", "dev", "tun0"], timeout=10)
    _sudo(["ip", "netns", "exec", _NS_CLI, "ip", "route", "add", "10.8.0.1", "dev", "tun1"], timeout=10)


def _kill_server_client() -> None:
    """Kill any lingering server/client processes."""
    for ns in [_NS_SRV, _NS_CLI]:
        _sudo(["ip", "netns", "exec", ns, "pkill", "-f", "src.server"], timeout=10)
        _sudo(["ip", "netns", "exec", ns, "pkill", "-f", "src.client"], timeout=10)
        _sudo(["ip", "netns", "exec", ns, "pkill", "-f", "http.server"], timeout=10)
    time.sleep(0.3)


def _get_config_for_phase(transport: str, phase: str, http2_aware: bool = False) -> tuple[str, str]:
    """Return (server_config, client_config) paths for a transport+phase.

    "before" phase: neither side uses shaping — baseline traces.
    "after" phase: BOTH sides use the same shaping pipeline so that
    encode/decode is symmetric and the TUN data path remains correct.
    """
    if transport == "tls":
        if phase == "before":
            return "config/server_netns_tls.yaml", "config/client_netns_tls.yaml"
        return "config/server_netns_tls_shaping.yaml", "config/client_netns_tls_shaping.yaml"

    if transport == "http2":
        if phase == "before":
            return "config/server_netns_http2.yaml", "config/client_netns_http2.yaml"
        if http2_aware:
            return "config/server_netns_http2_shaping_aware.yaml", "config/client_netns_http2_shaping_aware.yaml"
        return "config/server_netns_http2_shaping.yaml", "config/client_netns_http2_shaping.yaml"

    if phase == "before":
        return "config/server_netns.yaml", "config/client_netns.yaml"
    # Phase 9C: symmetric shaping with full pipeline (aggregation + padding + jitter)
    return "config/server_netns_shaping.yaml", "config/examples/shaping_padding_aggregation_netns.yaml"


# ---------------------------------------------------------------------------
# Scenario commands (parameterised by RuntimeConfig)
# ---------------------------------------------------------------------------


def _scenario_command(scenario: str, config: Optional[RuntimeConfig] = None) -> str:
    """Return the shell command to execute for a scenario inside the client netns."""
    cfg = config or RuntimeConfig()

    if scenario == "idle":
        idle_sleep = max(cfg.capture_duration - 5, 5)
        return f"sleep {idle_sleep}"

    if scenario == "ping":
        interval = cfg.ping_interval
        count = cfg.ping_count
        return f"ping -c {count} -i {interval} -W 2 10.8.0.1"

    if scenario == "curl":
        count = cfg.curl_count
        return (
            f"for i in $(seq 1 {count}); do "
            f"curl -s -o /dev/null --connect-timeout {cfg.curl_connect_timeout} "
            f"--max-time {cfg.curl_max_time} http://10.8.0.1:8080/test.bin; "
            f"sleep 0.1; done"
        )

    if scenario == "bulk":
        return (
            f"curl -s -o /dev/null --connect-timeout {cfg.curl_connect_timeout} "
            f"--max-time {cfg.curl_max_time} "
            f"http://10.8.0.1:8080/bulk.bin 2>/dev/null || echo 'no bulk server'"
        )

    if scenario == "reconnect":
        return "echo 'reconnect: current architecture does not support automatic reconnect; skipped'"

    return "sleep 3"


def _scenario_needs_http_server(scenario: str) -> bool:
    return scenario in ("curl", "bulk")


# ---------------------------------------------------------------------------
# HTTP server helpers (for curl/bulk scenarios)
# ---------------------------------------------------------------------------

_HTTP_SERVER_PROC: Optional[subprocess.Popen] = None
_HTTP_SERVER_DATA_DIR = "/tmp/vpn_phase9_http_data"


def _start_http_server_in_srv_ns(bulk_bytes: int = 1048576, startup_timeout: int = 10) -> bool:
    """Start a Python HTTP server in the server namespace, bound to TUN IP.

    Verifies the server is actually listening before returning.
    """
    global _HTTP_SERVER_PROC
    _stop_http_server_in_srv_ns()

    # Create data directory in server namespace with test files
    _sudo(["ip", "netns", "exec", _NS_SRV, "mkdir", "-p", _HTTP_SERVER_DATA_DIR], timeout=10)

    # Create a small test file for curl
    _sudo(["ip", "netns", "exec", _NS_SRV, "bash", "-c",
           f"echo 'hello from vpn server' > {_HTTP_SERVER_DATA_DIR}/test.bin"], timeout=10)

    # Create a bulk file for bulk download (bs=1024, count = bulk_bytes / 1024)
    bulk_count = max(1, bulk_bytes // 1024)
    _sudo(["ip", "netns", "exec", _NS_SRV, "bash", "-c",
           f"dd if=/dev/urandom of={_HTTP_SERVER_DATA_DIR}/bulk.bin bs=1024 count={bulk_count} 2>/dev/null"], timeout=30)

    try:
        proc = subprocess.Popen(
            ["sudo", "-S", "ip", "netns", "exec", _NS_SRV, "bash", "-c",
             f"cd {_HTTP_SERVER_DATA_DIR} && python3 -m http.server 8080 --bind 10.8.0.1 2>/dev/null"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        _HTTP_SERVER_PROC = proc

        # Verify server is actually listening
        t0 = time.time()
        while time.time() - t0 < startup_timeout:
            time.sleep(0.5)
            r = _sudo(
                ["ip", "netns", "exec", _NS_SRV, "ss", "-tlnp"],
                timeout=5,
            )
            if "8080" in r.stdout and "10.8.0.1" in r.stdout:
                return True
        return False
    except Exception as exc:
        print(f"  Error starting HTTP server: {exc}", file=sys.stderr)
        return False


def _stop_http_server_in_srv_ns() -> None:
    global _HTTP_SERVER_PROC
    if _HTTP_SERVER_PROC is not None:
        try:
            _HTTP_SERVER_PROC.kill()
            _HTTP_SERVER_PROC.wait(timeout=5)
        except Exception:
            pass
        _HTTP_SERVER_PROC = None
    # Clean up data directory
    _sudo(["ip", "netns", "exec", _NS_SRV, "rm", "-rf", _HTTP_SERVER_DATA_DIR], timeout=10)


# ---------------------------------------------------------------------------
# Plan subcommand
# ---------------------------------------------------------------------------


def build_matrix(
    transports: list[str],
    scenarios: list[str],
    output_dir: str,
    phases: Optional[list[str]] = None,
    runtime_config: Optional[RuntimeConfig] = None,
) -> list[dict[str, Any]]:
    """Build the before/after experiment matrix with optional repeat_count.

    When repeat_count > 1, each entry gets a run_id and paths use subdirectories:
      outputs/phase9/before/tcp/ping/run_01.report.json
    When repeat_count == 1, paths use the flat format:
      outputs/phase9/before/tcp/ping.report.json
    """
    if phases is None:
        phases = ["before", "after"]
    cfg = runtime_config or RuntimeConfig()
    repeat = max(1, cfg.repeat_count)

    entries: list[dict[str, Any]] = []
    for transport in transports:
        for scenario in scenarios:
            for phase in phases:
                dir_name = "before" if phase == "before" else "after"

                if repeat > 1:
                    for run_idx in range(1, repeat + 1):
                        run_id = f"run_{run_idx:02d}"
                        base = f"{output_dir}/{dir_name}/{transport}/{scenario}/{run_id}"
                        entries.append(_make_entry(
                            transport, scenario, phase, base, cfg, run_id=run_id,
                        ))
                else:
                    base = f"{output_dir}/{dir_name}/{transport}/{scenario}"
                    entries.append(_make_entry(transport, scenario, phase, base, cfg))

    return entries


def _make_entry(
    transport: str,
    scenario: str,
    phase: str,
    base_path: str,
    cfg: RuntimeConfig,
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "transport": transport,
        "scenario": scenario,
        "phase": phase,
        "pcap_path": f"{base_path}.pcap",
        "csv_path": f"{base_path}.csv",
        "report_path": f"{base_path}.report.json",
        "scenario_command": _scenario_command(scenario, cfg),
        "capture_interface": "veth_srv",
        "capture_host": "192.168.200.1",
        "needs_http_server": _scenario_needs_http_server(scenario),
        "capture_duration": cfg.capture_duration,
        "min_packet_count": cfg.min_packet_count,
        "repeat_count": cfg.repeat_count,
    }
    if run_id:
        entry["run_id"] = run_id
    return entry


def _run_plan(args: argparse.Namespace) -> None:
    transports = _parse_list(args.transports, _SUPPORTED_TRANSPORTS)
    scenarios = _parse_list(args.scenarios, _VALID_SCENARIOS)

    if not transports:
        print("Error: no valid transports specified", file=sys.stderr)
        sys.exit(1)
    if not scenarios:
        print("Error: no valid scenarios specified", file=sys.stderr)
        sys.exit(1)

    cfg = _runtime_config_from_args(args)
    entries = build_matrix(transports, scenarios, args.output_dir, runtime_config=cfg)
    manifest = {
        "generated_at": _now_iso(),
        "output_dir": args.output_dir,
        "transports": transports,
        "scenarios": scenarios,
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
            "repeat_count": cfg.repeat_count,
            "curl_connect_timeout": cfg.curl_connect_timeout,
            "curl_max_time": cfg.curl_max_time,
            "http_server_startup_timeout": cfg.http_server_startup_timeout,
            "post_scenario_wait": cfg.post_scenario_wait,
            "bulk_read_timeout": cfg.bulk_read_timeout,
        },
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
    port = "2225" if transport == "http2" else "2222"
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.5)
        r = _sudo(
            ["ip", "netns", "exec", _NS_SRV, "ss", "-tlnp"],
            timeout=5,
        )
        if port in r.stdout:
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
    duration: int = 30,
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


def _run_scenario_in_client(scenario: str, transport: str, config: Optional[RuntimeConfig] = None) -> bool:
    """Run the scenario command in client namespace."""
    cmd = _scenario_command(scenario, config)
    timeout = (config or RuntimeConfig()).scenario_timeout
    try:
        r = _sudo(
            ["ip", "netns", "exec", _NS_CLI, "bash", "-c", cmd],
            timeout=timeout,
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
    runtime_config: Optional[RuntimeConfig] = None,
) -> dict[str, Any]:
    """Execute a single experiment entry. Returns status dict."""
    transport = entry["transport"]
    scenario = entry["scenario"]
    phase = entry["phase"]
    pcap_path = entry["pcap_path"]
    csv_path = entry["csv_path"]
    report_path = entry["report_path"]
    cfg = runtime_config or RuntimeConfig()

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
        "capture_duration": entry.get("capture_duration", cfg.capture_duration),
        "min_packet_count": entry.get("min_packet_count", cfg.min_packet_count),
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
        if transport == "http2":
            h2d = env.get("http2_dependency", {})
            missing = [m for m in ("h2", "hpack", "hyperframe")
                       if not h2d.get(f"{m}_available", False)]
            if missing:
                result["error_reason"] = f"dependency_missing:{','.join(missing)}"
            else:
                result["error_reason"] = f"transport {transport} not runnable"
            result["dependency_status"] = {
                "h2_available": h2d.get("h2_available", False),
                "hpack_available": h2d.get("hpack_available", False),
                "hyperframe_available": h2d.get("hyperframe_available", False),
                "http2_runnable": False,
            }
            result["trace_type"] = "dependency_missing"
        else:
            result["error_reason"] = f"transport {transport} not runnable (missing tools or modules)"
        return result

    # Check curl availability for curl/bulk scenarios
    if scenario in ("curl", "bulk"):
        if not env.get("checks", {}).get("curl", False):
            result["error_reason"] = f"scenario '{scenario}' requires curl (not found)"
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
        server_cfg, client_cfg = _get_config_for_phase(transport, phase, cfg.http2_aware_shaping)

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

        # Start HTTP server if scenario needs it
        if entry.get("needs_http_server", False):
            if not _start_http_server_in_srv_ns(
                bulk_bytes=cfg.bulk_bytes,
                startup_timeout=cfg.http_server_startup_timeout,
            ):
                result["error_reason"] = "http server start failed (not listening within timeout)"
                _kill_server_client()
                _netns_cleanup()
                result["ended_at"] = _now_iso()
                result["duration_s"] = time.time() - t0
                return result

        # Start capture
        duration = cfg.capture_duration
        cap_proc = _capture_traffic(pcap_path, duration=duration)
        if cap_proc is None:
            result["error_reason"] = "tcpdump start failed"
            _stop_http_server_in_srv_ns()
            _kill_server_client()
            _netns_cleanup()
            result["ended_at"] = _now_iso()
            result["duration_s"] = time.time() - t0
            return result

        time.sleep(0.5)

        # Run scenario
        _run_scenario_in_client(scenario, transport, config=cfg)

        # Post-scenario wait to let residual packets be captured
        if cfg.post_scenario_wait > 0:
            time.sleep(cfg.post_scenario_wait)

        # Wait for capture to finish
        try:
            cap_proc.wait(timeout=duration + 10)
        except subprocess.TimeoutExpired:
            cap_proc.kill()
            cap_proc.wait()

        # Stop HTTP server if running
        _stop_http_server_in_srv_ns()

        # Convert pcap → CSV
        srv_port = 2225 if transport == "http2" else 2222
        if _convert_pcap(pcap_path, csv_path, client_host="192.168.200.2", server_host="192.168.200.1", server_port=srv_port):
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
        _stop_http_server_in_srv_ns()
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
    runtime_config: Optional[RuntimeConfig] = None,
) -> None:
    """Run a batch of experiment entries."""
    log_dir = Path(output_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    total = len(entries)
    for i, entry in enumerate(entries):
        label = f"{entry['transport']}/{entry['scenario']}/{entry['phase']}"
        if entry.get("run_id"):
            label += f"/{entry['run_id']}"
        if execute:
            print(f"[{i+1}/{total}] Running: {label} ...", flush=True)
        else:
            print(f"[{i+1}/{total}] dry-run: {label}")
        result = _run_one_entry(entry, env, execute=execute, runtime_config=runtime_config)
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


def _runtime_config_from_args(args: argparse.Namespace) -> RuntimeConfig:
    """Extract RuntimeConfig from CLI args (handles missing attributes gracefully)."""
    return RuntimeConfig(
        capture_duration=getattr(args, "capture_duration", 30),
        ping_count=getattr(args, "ping_count", 20),
        ping_interval=getattr(args, "ping_interval", 0.1),
        curl_count=getattr(args, "curl_count", 10),
        bulk_bytes=getattr(args, "bulk_bytes", 1048576),
        min_packet_count=getattr(args, "min_packet_count", 30),
        scenario_timeout=getattr(args, "scenario_timeout", 60),
        repeat_count=getattr(args, "repeat_count", 1),
        curl_connect_timeout=getattr(args, "curl_connect_timeout", 30),
        curl_max_time=getattr(args, "curl_max_time", 60),
        http_server_startup_timeout=getattr(args, "http_server_startup_timeout", 10),
        post_scenario_wait=getattr(args, "post_scenario_wait", 2),
        bulk_read_timeout=getattr(args, "bulk_read_timeout", 60),
        http2_aware_shaping=getattr(args, "http2_aware_shaping", False),
    )


def _run_run(args: argparse.Namespace) -> None:
    transports = _parse_list(args.transports, _SUPPORTED_TRANSPORTS)
    scenarios = _parse_list(args.scenarios, _VALID_SCENARIOS)

    if not transports:
        print("Error: no valid transports", file=sys.stderr)
        sys.exit(1)
    if not scenarios:
        print("Error: no valid scenarios", file=sys.stderr)
        sys.exit(1)

    cfg = _runtime_config_from_args(args)
    entries = build_matrix(transports, scenarios, args.output_dir, runtime_config=cfg)
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
        _run_batch(entries, env, execute=False, output_dir=args.output_dir, runtime_config=cfg)
        print(f"\nDry-run complete. {len(entries)} entries planned.")
        print("Use --execute to run real experiments.")
    else:
        if not env.get("can_run_real_tcp", False):
            print("Error: environment not ready for real execution.", file=sys.stderr)
            print("Missing: TUN device, netns capability, tcpdump, or tshark.", file=sys.stderr)
            sys.exit(1)

        print(f"=== Execute: {len(entries)} entries ===")
        _run_batch(entries, env, execute=True, output_dir=args.output_dir, runtime_config=cfg)

        # After execution, run comparison (repeated if repeat_count > 1)
        if cfg.repeat_count > 1:
            _run_repeated_comparison(args.output_dir, transports, scenarios,
                                     min_packet_count=cfg.min_packet_count,
                                     repeat_count=cfg.repeat_count)
        else:
            _run_comparison(args.output_dir, min_packet_count=cfg.min_packet_count)


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


def _run_comparison(output_dir: str, min_packet_count: int = 30) -> None:
    """Compare before/after fingerprint reports and write comparison files."""
    entries = []
    for transport in sorted(_SUPPORTED_TRANSPORTS):
        for scenario in sorted(_VALID_SCENARIOS):
            before_report = f"{output_dir}/before/{transport}/{scenario}.report.json"
            after_report = f"{output_dir}/after/{transport}/{scenario}.report.json"
            before = _load_report_safe(before_report)
            after = _load_report_safe(after_report)

            if before is None and after is None:
                continue

            entry: dict[str, Any] = {
                "transport": transport,
                "scenario": scenario,
                "min_packet_count": min_packet_count,
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

            # Packet count quality check
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

            # risk_score delta
            b_score = before.get("fingerprint_risk_score") if before else None
            a_score = after.get("fingerprint_risk_score") if after else None

            if entry["data_quality"] == "insufficient":
                entry["verdict"] = "insufficient"
                entry["risk_score_delta"] = "N/A"
            elif entry["data_quality"] == "skipped":
                entry["verdict"] = "skipped"
                entry["risk_score_delta"] = "N/A"
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

            entry["trace_type_before"] = "real" if before else "skipped"
            entry["trace_type_after"] = "real" if after else "skipped"
            entries.append(entry)

    # Write comparison CSVs
    summaries_dir = Path(output_dir) / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)

    # Fingerprint CSVs by phase
    for phase in ["before", "after"]:
        _write_phase_summary_csv(output_dir, phase, summaries_dir)

    # Skipped report
    _write_skipped_report_by_entries(entries, summaries_dir)

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
            "min_packet_count": min_packet_count,
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
        ok = sum(1 for e in entries if e["data_quality"] == "ok")

        print(f"\nComparison written to {summaries_dir}/")
        print(f"  data_quality: ok={ok}, insufficient={insufficient}, skipped={skipped}")
        print(f"  verdicts: improved={improved}, regressed={regressed}, "
              f"unchanged={unchanged}, insufficient={insufficient}, skipped={skipped}")

        # Generate patch prompts based on data quality
        _generate_patch_prompts(entries, output_dir)


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


def _write_skipped_report_by_entries(entries: list[dict[str, Any]], summaries_dir: Path) -> None:
    """Write skipped.json based on comparison entries."""
    skipped_list = []
    for e in entries:
        if e["verdict"] in ("skipped", "insufficient") and e["trace_type_before"] == "skipped" and e["trace_type_after"] == "skipped":
            skipped_list.append({
                "transport": e["transport"],
                "scenario": e["scenario"],
                "reason": "both before and after reports missing",
            })
        elif e["verdict"] == "insufficient":
            reasons = []
            if not e.get("packet_count_ok_before"):
                reasons.append(f"before packet_count={e.get('packet_count_before', 0)} < min_packet_count={e.get('min_packet_count', 'N/A')}")
            if not e.get("packet_count_ok_after"):
                reasons.append(f"after packet_count={e.get('packet_count_after', 0)} < min_packet_count={e.get('min_packet_count', 'N/A')}")
            if not reasons:
                reasons.append("missing before or after report")
            skipped_list.append({
                "transport": e["transport"],
                "scenario": e["scenario"],
                "reason": "; ".join(reasons),
            })

    if skipped_list:
        path = summaries_dir / "skipped.json"
        path.write_text(json.dumps({"generated_at": _now_iso(), "entries": skipped_list}, indent=2))


def _write_comparison_md(entries: list[dict[str, Any]], md_path: Path) -> None:
    """Write comparison markdown."""
    lines = [
        "# Phase 9: Before/After Trace Comparison",
        "",
        f"Generated: {_now_iso()}",
        "",
        "| Transport | Scenario | Before Pkts | After Pkts | Data Quality | Before Score | After Score | Delta | Before Level | After Level | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for e in entries:
        lines.append(
            f"| {e['transport']} | {e['scenario']} | "
            f"{e.get('packet_count_before', 'N/A')} | "
            f"{e.get('packet_count_after', 'N/A')} | "
            f"{e.get('data_quality', 'N/A')} | "
            f"{e.get('fingerprint_risk_score_before', 'N/A')} | "
            f"{e.get('fingerprint_risk_score_after', 'N/A')} | "
            f"{e.get('risk_score_delta', 'N/A')} | "
            f"{e.get('risk_level_before', 'N/A')} | "
            f"{e.get('risk_level_after', 'N/A')} | "
            f"**{e.get('verdict', 'N/A')}** |"
        )
    md_path.write_text("\n".join(lines))


def _discover_transport_scenarios(output_dir: str) -> list[tuple[str, str]]:
    """Discover all (transport, scenario) pairs with report data in output_dir.

    Scans before/*/*/ for run_*.report.json files so incremental batches
    (e.g. tcp+ws then tls) produce a combined summary instead of overwriting.
    """
    pairs: set[tuple[str, str]] = set()
    before_dir = Path(output_dir) / "before"
    if before_dir.is_dir():
        for transport_dir in before_dir.iterdir():
            if not transport_dir.is_dir():
                continue
            transport = transport_dir.name
            for scenario_dir in transport_dir.iterdir():
                if not scenario_dir.is_dir():
                    continue
                scenario = scenario_dir.name
                # Check for at least one report file
                if list(scenario_dir.glob("run_*.report.json")):
                    pairs.add((transport, scenario))
    return sorted(pairs)


def _run_repeated_comparison(
    output_dir: str,
    transports: list[str],
    scenarios: list[str],
    min_packet_count: int = 30,
    repeat_count: int = 3,
) -> None:
    """Phase 9E: Statistical before/after comparison with repeated runs.

    Groups run_XX.report.json files by (transport, scenario, phase),
    computes mean/std/min/max, and produces aggregate verdicts.

    Auto-discovers all transport/scenario pairs with data in output_dir
    so that incremental batches produce a combined summary.
    """
    summaries_dir = Path(output_dir) / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)

    aggregated: list[dict[str, Any]] = []

    discovered = _discover_transport_scenarios(output_dir)
    if discovered:
        pairs = discovered
    else:
        pairs = [(t, s) for t in transports for s in scenarios]

    for transport, scenario in pairs:
        agg = _aggregate_repeated_runs(
            output_dir, transport, scenario,
            min_packet_count=min_packet_count, repeat_count=repeat_count,
        )
        if agg is not None:
            aggregated.append(agg)

    if not aggregated:
        print("\nNo repeated comparison data available.")
        return

    # Write repeated comparison CSV
    _write_repeated_comparison_csv(aggregated, summaries_dir)

    # Write repeated comparison JSON
    json_path = summaries_dir / "repeated_before_after_comparison.json"
    json_path.write_text(json.dumps({
        "generated_at": _now_iso(),
        "min_packet_count": min_packet_count,
        "repeat_count": repeat_count,
        "entries": aggregated,
    }, indent=2))

    # Write Markdown
    _write_repeated_comparison_md(aggregated, summaries_dir)

    # Statistics
    improved = sum(1 for e in aggregated if e.get("aggregate_verdict") == "improved")
    regressed = sum(1 for e in aggregated if e.get("aggregate_verdict") == "regressed")
    unchanged = sum(1 for e in aggregated if e.get("aggregate_verdict") == "unchanged")
    mixed = sum(1 for e in aggregated if e.get("aggregate_verdict") == "mixed")
    insufficient = sum(1 for e in aggregated if e.get("aggregate_verdict") == "insufficient")
    partial = sum(1 for e in aggregated if e.get("data_quality") == "partial")
    ok = sum(1 for e in aggregated if e.get("data_quality") == "ok")

    print(f"\nRepeated comparison written to {summaries_dir}/")
    print(f"  data_quality: ok={ok}, partial={partial}, insufficient={insufficient}")
    print(f"  aggregate_verdicts: improved={improved}, regressed={regressed}, "
          f"unchanged={unchanged}, mixed={mixed}, insufficient={insufficient}")

    # Generate prompts based on aggregate results
    _generate_patch_prompts(aggregated, output_dir, repeated=True)


def _aggregate_repeated_runs(
    output_dir: str,
    transport: str,
    scenario: str,
    min_packet_count: int = 30,
    repeat_count: int = 3,
) -> Optional[dict[str, Any]]:
    """Aggregate repeated run reports for one transport/scenario.

    Returns a dict with per-phase statistics and aggregate verdict.
    """
    before_runs = _load_repeated_reports(output_dir, transport, scenario, "before", repeat_count)
    after_runs = _load_repeated_reports(output_dir, transport, scenario, "after", repeat_count)

    if not before_runs and not after_runs:
        return None

    valid_before = len(before_runs)
    valid_after = len(after_runs)

    # Extract per-run packet counts and risk scores
    before_pcs = [_safe_float(r, "packet_count") for r in before_runs]
    after_pcs = [_safe_float(r, "packet_count") for r in after_runs]
    before_scores = [_safe_float(r, "fingerprint_risk_score") for r in before_runs]
    after_scores = [_safe_float(r, "fingerprint_risk_score") for r in after_runs]

    # Filter out None values
    before_pcs_valid = [v for v in before_pcs if v is not None]
    after_pcs_valid = [v for v in after_pcs if v is not None]
    before_scores_valid = [v for v in before_scores if v is not None]
    after_scores_valid = [v for v in after_scores if v is not None]

    entry: dict[str, Any] = {
        "transport": transport,
        "scenario": scenario,
        "repeat_count": repeat_count,
        "valid_repeat_before": valid_before,
        "valid_repeat_after": valid_after,
    }

    # Packet count stats
    if before_pcs_valid:
        entry["before_packet_count_mean"] = round(_mean(before_pcs_valid), 1)
        entry["before_packet_count_std"] = round(_std(before_pcs_valid), 1)
        entry["before_packet_count_min"] = round(min(before_pcs_valid), 1)
        entry["before_packet_count_max"] = round(max(before_pcs_valid), 1)
    if after_pcs_valid:
        entry["after_packet_count_mean"] = round(_mean(after_pcs_valid), 1)
        entry["after_packet_count_std"] = round(_std(after_pcs_valid), 1)
        entry["after_packet_count_min"] = round(min(after_pcs_valid), 1)
        entry["after_packet_count_max"] = round(max(after_pcs_valid), 1)

    # Risk score stats
    if before_scores_valid:
        entry["before_risk_score_mean"] = round(_mean(before_scores_valid), 4)
        entry["before_risk_score_std"] = round(_std(before_scores_valid), 4)
    if after_scores_valid:
        entry["after_risk_score_mean"] = round(_mean(after_scores_valid), 4)
        entry["after_risk_score_std"] = round(_std(after_scores_valid), 4)

    # Per-run verdict counts
    verdicts = _per_run_verdicts(before_runs, after_runs, min_packet_count)
    entry["improved_count"] = verdicts.count("improved")
    entry["unchanged_count"] = verdicts.count("unchanged")
    entry["regressed_count"] = verdicts.count("regressed")
    entry["insufficient_count"] = verdicts.count("insufficient")
    entry["skipped_count"] = verdicts.count("skipped")

    # Compute risk_score_delta_mean from paired valid runs
    deltas = _paired_deltas(before_runs, after_runs, min_packet_count)
    if deltas:
        entry["risk_score_delta_mean"] = round(_mean(deltas), 4)
        entry["risk_score_delta_std"] = round(_std(deltas), 4)

    # Data quality
    valid_pair_count = len(deltas)
    if valid_before == 0 and valid_after == 0:
        entry["data_quality"] = "skipped"
        entry["aggregate_verdict"] = "skipped"
    elif valid_pair_count == 0:
        entry["data_quality"] = "insufficient"
        entry["aggregate_verdict"] = "insufficient"
    elif valid_pair_count < repeat_count:
        entry["data_quality"] = "partial"
    else:
        entry["data_quality"] = "ok"

    # Aggregate verdict (only when data_quality is ok or partial)
    if entry.get("data_quality") in ("ok", "partial"):
        imp = entry["improved_count"]
        reg = entry["regressed_count"]
        unc = entry["unchanged_count"]

        if imp >= 2 and reg == 0:
            entry["aggregate_verdict"] = "improved"
        elif reg >= 2:
            entry["aggregate_verdict"] = "regressed"
        elif unc >= 2:
            entry["aggregate_verdict"] = "unchanged"
        else:
            entry["aggregate_verdict"] = "mixed"

    return entry


def _load_repeated_reports(
    output_dir: str, transport: str, scenario: str, phase: str, repeat_count: int,
) -> list[dict[str, Any]]:
    """Load all run reports for a given transport/scenario/phase."""
    reports = []
    for run_idx in range(1, repeat_count + 1):
        run_id = f"run_{run_idx:02d}"
        report_path = f"{output_dir}/{phase}/{transport}/{scenario}/{run_id}.report.json"
        r = _load_report_safe(report_path)
        if r is not None:
            reports.append(r)
    return reports


def _safe_float(report: dict[str, Any], key: str) -> Optional[float]:
    """Safely extract a float value from a report dict."""
    val = report.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return (sum((v - m) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def _per_run_verdicts(
    before_runs: list[dict[str, Any]],
    after_runs: list[dict[str, Any]],
    min_packet_count: int,
) -> list[str]:
    """Compute per-run verdicts by pairing reports by index."""
    verdicts = []
    max_runs = max(len(before_runs), len(after_runs))
    for i in range(max_runs):
        b = before_runs[i] if i < len(before_runs) else None
        a = after_runs[i] if i < len(after_runs) else None

        if b is None and a is None:
            verdicts.append("skipped")
            continue

        pc_b = (b or {}).get("packet_count", 0) or 0
        pc_a = (a or {}).get("packet_count", 0) or 0

        if (b is None or a is None or pc_b < min_packet_count or pc_a < min_packet_count):
            verdicts.append("insufficient")
            continue

        b_score = b.get("fingerprint_risk_score")
        a_score = a.get("fingerprint_risk_score")
        if b_score is None or a_score is None:
            verdicts.append("insufficient")
            continue

        delta = a_score - b_score
        if delta < -0.05:
            verdicts.append("improved")
        elif delta > 0.05:
            verdicts.append("regressed")
        else:
            verdicts.append("unchanged")

    return verdicts


def _paired_deltas(
    before_runs: list[dict[str, Any]],
    after_runs: list[dict[str, Any]],
    min_packet_count: int,
) -> list[float]:
    """Compute risk_score_delta for valid paired runs."""
    deltas = []
    for i in range(min(len(before_runs), len(after_runs))):
        b = before_runs[i]
        a = after_runs[i]
        pc_b = b.get("packet_count", 0) or 0
        pc_a = a.get("packet_count", 0) or 0
        if pc_b < min_packet_count or pc_a < min_packet_count:
            continue
        b_score = b.get("fingerprint_risk_score")
        a_score = a.get("fingerprint_risk_score")
        if b_score is not None and a_score is not None:
            deltas.append(round(a_score - b_score, 4))
    return deltas


def _write_repeated_comparison_csv(entries: list[dict[str, Any]], summaries_dir: Path) -> None:
    fieldnames = [
        "transport", "scenario", "repeat_count",
        "valid_repeat_before", "valid_repeat_after",
        "before_packet_count_mean", "before_packet_count_std",
        "before_packet_count_min", "before_packet_count_max",
        "after_packet_count_mean", "after_packet_count_std",
        "after_packet_count_min", "after_packet_count_max",
        "before_risk_score_mean", "before_risk_score_std",
        "after_risk_score_mean", "after_risk_score_std",
        "risk_score_delta_mean", "risk_score_delta_std",
        "improved_count", "unchanged_count", "regressed_count",
        "insufficient_count", "skipped_count",
        "data_quality", "aggregate_verdict",
    ]
    csv_path = summaries_dir / "repeated_before_after_comparison.csv"
    with csv_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for e in entries:
            w.writerow(e)


def _write_repeated_comparison_md(entries: list[dict[str, Any]], summaries_dir: Path) -> None:
    lines = [
        "# Phase 9E: Repeated Before/After Trace Comparison",
        "",
        f"Generated: {_now_iso()}",
        "",
        "| Transport | Scenario | Valid Runs | Before Pkts (mean±std) | After Pkts (mean±std) | "
        "Before Risk (mean±std) | After Risk (mean±std) | Delta (mean±std) | "
        "I/U/R | Quality | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for e in entries:
        before_pkts = f"{e.get('before_packet_count_mean','N/A')}±{e.get('before_packet_count_std','N/A')}"
        after_pkts = f"{e.get('after_packet_count_mean','N/A')}±{e.get('after_packet_count_std','N/A')}"
        before_risk = f"{e.get('before_risk_score_mean','N/A')}±{e.get('before_risk_score_std','N/A')}"
        after_risk = f"{e.get('after_risk_score_mean','N/A')}±{e.get('after_risk_score_std','N/A')}"
        delta_s = f"{e.get('risk_score_delta_mean','N/A')}±{e.get('risk_score_delta_std','N/A')}"
        iur = f"{e.get('improved_count',0)}/{e.get('unchanged_count',0)}/{e.get('regressed_count',0)}"
        lines.append(
            f"| {e['transport']} | {e['scenario']} | "
            f"{e.get('valid_repeat_before','?')}/{e.get('valid_repeat_after','?')} | "
            f"{before_pkts} | {after_pkts} | "
            f"{before_risk} | {after_risk} | "
            f"{delta_s} | {iur} | "
            f"{e.get('data_quality','N/A')} | "
            f"**{e.get('aggregate_verdict','N/A')}** |"
        )
    md_path = summaries_dir / "repeated_before_after_comparison.md"
    md_path.write_text("\n".join(lines))


def _generate_patch_prompts(
    entries: list[dict[str, Any]], output_dir: str, repeated: bool = False,
) -> None:
    """Phase 9E: Generate prompts based on aggregate or single-run data quality.

    - aggregate_verdict=regressed or after_risk_score_mean >= 0.70 → countermeasure patch
    - data_quality=insufficient or partial → data collection prompt
    - aggregate_verdict=improved or unchanged → no_patch_needed
    """
    verdict_key = "aggregate_verdict" if repeated else "verdict"

    # Regressed or high risk → countermeasure patch
    regressed = [
        e for e in entries
        if e.get(verdict_key) == "regressed"
        and e.get("data_quality") in ("ok", "partial")
    ]
    high_risk = [
        e for e in entries
        if e.get("data_quality") in ("ok", "partial")
        and e.get("after_risk_score_mean", e.get("fingerprint_risk_score_after", 0) or 0) >= 0.70
    ]
    patch_targets = regressed + [e for e in high_risk if e not in regressed]

    if patch_targets:
        prompt_lines = [
            "Improve VPN-LLM traffic shaping based on Phase 9E repeated trace matrix regressions.",
            "",
            "The following transport/scenario combinations showed regressed",
            "fingerprint risk scores or persistently high risk after enabling shaping:",
            "",
        ]
        for e in patch_targets:
            if repeated:
                prompt_lines.append(
                    f"- {e['transport']}/{e['scenario']}: "
                    f"before_risk_mean={e.get('before_risk_score_mean', 'N/A')}, "
                    f"after_risk_mean={e.get('after_risk_score_mean', 'N/A')}, "
                    f"delta_mean={e.get('risk_score_delta_mean', 'N/A')}, "
                    f"aggregate_verdict={e.get('aggregate_verdict', 'N/A')}"
                )
            else:
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
        print(f"Countermeasure patch prompt written to {prompt_path}")

        result_path = Path(output_dir) / "patch_loop_result.json"
        result_path.write_text(json.dumps({
            "generated_at": _now_iso(),
            "patch_target_count": len(patch_targets),
            "patch_targets": patch_targets,
            "prompt_path": str(prompt_path),
        }, indent=2))

    # Insufficient or partial data → data collection prompt
    insufficient = [e for e in entries if e.get("data_quality") == "insufficient"]
    partial = [e for e in entries if e.get("data_quality") == "partial"]
    dc_targets = insufficient + partial

    if dc_targets:
        dc_lines = [
            "Phase 9 trace data is insufficient or partial for reliable fingerprint comparison.",
            "",
            "The following entries need more traffic or longer capture:",
            "",
        ]
        for e in dc_targets:
            if repeated:
                dc_lines.append(
                    f"- {e['transport']}/{e['scenario']}: "
                    f"valid_repeat_before={e.get('valid_repeat_before','?')}, "
                    f"valid_repeat_after={e.get('valid_repeat_after','?')}, "
                    f"before_pkt_mean={e.get('before_packet_count_mean','N/A')}, "
                    f"after_pkt_mean={e.get('after_packet_count_mean','N/A')}, "
                    f"min_packet_count={e.get('min_packet_count',30)}, "
                    f"data_quality={e.get('data_quality','N/A')}"
                )
            else:
                dc_lines.append(
                    f"- {e['transport']}/{e['scenario']}: "
                    f"before packets={e.get('packet_count_before', 'N/A')}, "
                    f"after packets={e.get('packet_count_after', 'N/A')}, "
                    f"min_packet_count={e.get('min_packet_count', 'N/A')}"
                )

        dc_lines += [
            "",
            "Recommendations:",
            "- Increase --capture-duration (try 45 or 60)",
            "- Use --ping-count 80 or higher with --ping-interval 0.05",
            "- For bulk, increase --bulk-bytes and --curl-connect-timeout",
            "- Ensure VPN tunnel is established before running scenario",
            "",
            "Do NOT modify countermeasure code based on insufficient data.",
        ]

        dc_path = Path(output_dir) / "data_collection_prompt.txt"
        dc_path.write_text("\n".join(dc_lines))
        print(f"Data collection prompt written to {dc_path}")

    # No patch needed
    if not patch_targets and not dc_targets:
        no_patch_path = Path(output_dir) / "no_patch_needed.txt"
        no_patch_path.write_text(
            f"# No patch needed\n\n"
            f"Generated: {_now_iso()}\n\n"
            f"All scenarios with sufficient data showed improved or unchanged verdicts.\n"
            f"No regressions or high-risk entries detected.\n"
        )
        print(f"No patch needed — all sufficient entries improved or unchanged.")


# ---------------------------------------------------------------------------
# Skipped transport report
# ---------------------------------------------------------------------------


def _write_skipped_report(output_dir: str, env: dict[str, Any]) -> None:
    """Write a report of which transports were skipped and why."""
    skipped = {}
    for transport in ["tcp", "tls", "websocket", "ssh", "http2"]:
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
            if transport == "http2":
                h2d = env.get("http2_dependency", {})
                if not h2d.get("h2_available", False):
                    reasons.append("h2 dependency missing")
                if not h2d.get("hpack_available", False):
                    reasons.append("hpack dependency missing")
                if not h2d.get("hyperframe_available", False):
                    reasons.append("hyperframe dependency missing")
            skipped[transport] = {"skipped": True, "reasons": reasons}
        else:
            skipped[transport] = {"skipped": False, "reasons": []}

    path = Path(output_dir) / "summaries" / "skipped.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(skipped, indent=2))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _add_runtime_args(parser: argparse.ArgumentParser) -> None:
    """Add Phase 9E runtime parameters to a subparser."""
    parser.add_argument("--capture-duration", type=int, default=30,
                        help="Capture duration in seconds (default: 30)")
    parser.add_argument("--ping-count", type=int, default=20,
                        help="Number of ping packets (default: 20)")
    parser.add_argument("--ping-interval", type=float, default=0.1,
                        help="Interval between pings in seconds (default: 0.1)")
    parser.add_argument("--curl-count", type=int, default=10,
                        help="Number of curl requests (default: 10)")
    parser.add_argument("--bulk-bytes", type=int, default=1048576,
                        help="Bulk transfer size in bytes (default: 1048576)")
    parser.add_argument("--min-packet-count", type=int, default=30,
                        help="Minimum packet count for valid data (default: 30)")
    parser.add_argument("--scenario-timeout", type=int, default=60,
                        help="Scenario execution timeout in seconds (default: 60)")
    parser.add_argument("--repeat-count", type=int, default=1,
                        help="Number of run repetitions per scenario (default: 1)")
    parser.add_argument("--curl-connect-timeout", type=int, default=30,
                        help="curl --connect-timeout seconds (default: 30)")
    parser.add_argument("--curl-max-time", type=int, default=60,
                        help="curl --max-time seconds (default: 60)")
    parser.add_argument("--http-server-startup-timeout", type=int, default=10,
                        help="HTTP server startup timeout seconds (default: 10)")
    parser.add_argument("--post-scenario-wait", type=int, default=2,
                        help="Post-scenario wait seconds (default: 2)")
    parser.add_argument("--bulk-read-timeout", type=int, default=60,
                        help="Bulk download read timeout seconds (default: 60)")
    parser.add_argument("--http2-aware-shaping", action="store_true",
                        help="Use HTTP/2-aware shaping configs (Phase 10D)")


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
    _add_runtime_args(plan_p)

    # run
    run_p = sub.add_parser("run", help="Run experiments (dry-run by default).")
    run_p.add_argument("--output-dir", default="outputs/phase9_real_matrix")
    run_p.add_argument("--transports", required=True)
    run_p.add_argument("--scenarios", required=True)
    run_p.add_argument("--dry-run", action="store_true", default=False)
    run_p.add_argument("--execute", action="store_true", default=False)
    run_p.add_argument("--verbose", action="store_true")
    _add_runtime_args(run_p)

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
