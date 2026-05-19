#!/usr/bin/env python3
"""Lightweight tcpdump/tshark wrapper for VPN-LLM local offline trace capture.

Converts captured pcap files into the CSV format expected by the fingerprint
evaluator (src.evaluation.fingerprint).  All subcommands support --dry-run so
that the script can be inspected and tested without requiring root or actual
tcpdump/tshark installations.

Usage:
    python3 scripts/trace_capture.py tools
    python3 scripts/trace_capture.py capture --interface lo --host 127.0.0.1 --port 9000 --duration 10 --output-pcap traces/idle.pcap --dry-run
    python3 scripts/trace_capture.py convert --input-pcap traces/idle.pcap --output-csv traces/idle.csv --client-host 127.0.0.1 --server-host 127.0.0.1 --server-port 9000
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# ---------------------------------------------------------------------------
# Tool detection
# ---------------------------------------------------------------------------

_TOOLS = ("tcpdump", "tshark", "timeout", "ip")


def detect_tools() -> dict[str, bool]:
    """Check which external tools are available on PATH.

    Returns a dict mapping tool name to boolean availability.
    """
    result: dict[str, bool] = {}
    for tool in _TOOLS:
        result[tool] = shutil.which(tool) is not None
    return result


# ---------------------------------------------------------------------------
# Command builders (pure — never execute)
# ---------------------------------------------------------------------------


def build_tcpdump_command(
    *,
    interface: str,
    output_pcap: str | Path,
    host: Optional[str] = None,
    port: Optional[int] = None,
    duration: int = 10,
    extra_filter: str = "",
    sudo: bool = False,
    snaplen: int = 0,
) -> list[str]:
    """Build a tcpdump command line as a list of tokens.

    The command is *not* executed by this function.  Duration is enforced by
    wrapping the invocation with ``timeout`` (or the closest available
    alternative).

    Returns a list of string tokens suitable for ``subprocess`` or display.
    """
    cmd: list[str] = []

    if sudo:
        cmd.append("sudo")

    # Use timeout to bound capture duration
    cmd.extend(["timeout", str(duration)])

    cmd.extend(["tcpdump", "-i", interface, "-w", str(output_pcap)])

    if snaplen > 0:
        cmd.extend(["-s", str(snaplen)])

    # Build BPF filter
    filter_parts: list[str] = []
    if host:
        filter_parts.append(f"host {host}")
    if port is not None:
        filter_parts.append(f"port {port}")
    if extra_filter.strip():
        filter_parts.append(f"({extra_filter.strip()})")

    if filter_parts:
        cmd.append(" and ".join(filter_parts))

    return cmd


def build_tshark_csv_command(
    *,
    input_pcap: str | Path,
    output_csv: str | Path,
    client_host: str,
    server_host: str,
    server_port: Optional[int] = None,
) -> list[str]:
    """Build a tshark command that converts a pcap to the fingerprint CSV format.

    The output CSV has columns:
        timestamp,src,dst,src_port,dst_port,proto,length,direction

    Direction is computed from the source/destination addresses relative to
    *client_host* / *server_host*.
    """
    # tshark -r in.pcap -T fields
    #   -e frame.time_epoch
    #   -e ip.src -e ip.dst
    #   -e tcp.srcport -e tcp.dstport -e udp.srcport -e udp.dstport
    #   -e frame.protocols
    #   -e frame.len
    #   -E header=y -E separator=,
    #
    # We use -T fields to emit structured columns, then post-process with
    # normalize_trace_rows() because tshark cannot natively express the
    # C2S/S2C direction column.
    return [
        "tshark",
        "-r", str(input_pcap),
        "-T", "fields",
        "-e", "frame.time_epoch",
        "-e", "ip.src",
        "-e", "ip.dst",
        "-e", "tcp.srcport",
        "-e", "tcp.dstport",
        "-e", "udp.srcport",
        "-e", "udp.dstport",
        "-e", "frame.protocols",
        "-e", "frame.len",
        "-E", "header=y",
        "-E", "separator=,",
        "-E", "quote=n",
        "-E", "occurrence=f",
    ]


# ---------------------------------------------------------------------------
# Direction inference
# ---------------------------------------------------------------------------


def infer_direction(
    src: str,
    dst: str,
    src_port: int,
    dst_port: int,
    client_host: str,
    server_host: str,
    client_port: Optional[int] = None,
    server_port: Optional[int] = None,
) -> str:
    """Infer direction as ``C2S`` (client→server) or ``S2C`` (server→client).

    Rules (evaluated in order):
        1. src == client_host and dst == server_host → C2S
        2. src == server_host and dst == client_host → S2C
        3. If hosts are equal, use server_port to disambiguate:
           dst_port == server_port → C2S
           src_port == server_port → S2C
        4. If hosts are equal and client_port is given, use it as fallback:
           src_port == client_port → C2S
           dst_port == client_port → S2C

    Raises ValueError when direction cannot be determined.
    """
    # When src == dst (e.g. loopback), host-based rules are ambiguous.
    # Skip them and rely on port-based disambiguation.
    if src != dst:
        # Rule 1 & 2: host-based
        if src == client_host and dst == server_host:
            return "C2S"
        if src == server_host and dst == client_host:
            return "S2C"

    # Rule 3: same-host case — use server_port
    if server_port is not None:
        if dst_port == server_port:
            return "C2S"
        if src_port == server_port:
            return "S2C"

    # Rule 4: same-host + client_port fallback
    if client_port is not None:
        if src_port == client_port:
            return "C2S"
        if dst_port == client_port:
            return "S2C"

    raise ValueError(
        f"Cannot infer direction: src={src}, dst={dst},"
        f" src_port={src_port}, dst_port={dst_port},"
        f" client_host={client_host}, server_host={server_host},"
        f" server_port={server_port}, client_port={client_port}"
    )


# ---------------------------------------------------------------------------
# Row normalisation
# ---------------------------------------------------------------------------


def _pick_port(src_port_str: str, dst_port_str: str) -> tuple[int, int]:
    """Select src/dst port from tshark fields, handling empty strings."""
    sp = int(src_port_str) if src_port_str.strip() else 0
    dp = int(dst_port_str) if dst_port_str.strip() else 0
    return sp, dp


def _infer_proto(protocols_field: str) -> str:
    """Heuristically pick the highest-level protocol from the tshark frame.protocols field."""
    if not protocols_field.strip():
        return "unknown"
    layers = [p.strip() for p in protocols_field.split(":")]
    for candidate in ("tls", "ssh", "websocket", "ws", "udp", "tcp"):
        for layer in layers:
            if layer.lower() == candidate:
                return candidate
    return layers[-1] if layers else "unknown"


def normalize_trace_rows(
    raw_rows: list[dict[str, str]],
    *,
    client_host: str,
    server_host: str,
    server_port: Optional[int] = None,
    client_port: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Convert raw tshark/parse rows into fingerprint-evaluator-compatible rows.

    Each raw row is a dict with keys matching the tshark ``-e`` fields:
    frame.time_epoch, ip.src, ip.dst, tcp.srcport, tcp.dstport,
    udp.srcport, udp.dstport, frame.protocols, frame.len.

    Returns rows with keys: timestamp, src, dst, src_port, dst_port,
    proto, length, direction.
    """
    normalized: list[dict[str, Any]] = []
    for row in raw_rows:
        src = row.get("ip.src", "").strip()
        dst = row.get("ip.dst", "").strip()
        if not src or not dst:
            continue

        # Pick ports: prefer tcp, fall back to udp
        tcp_sp, tcp_dp = _pick_port(
            row.get("tcp.srcport", ""), row.get("tcp.dstport", "")
        )
        udp_sp, udp_dp = _pick_port(
            row.get("udp.srcport", ""), row.get("udp.dstport", "")
        )

        if tcp_sp or tcp_dp:
            src_port, dst_port = tcp_sp, tcp_dp
        else:
            src_port, dst_port = udp_sp, udp_dp

        proto = _infer_proto(row.get("frame.protocols", ""))

        length_str = row.get("frame.len", "0")
        length = int(length_str) if length_str.strip() else 0

        ts_str = row.get("frame.time_epoch", "0")
        timestamp = float(ts_str) if ts_str.strip() else 0.0

        direction = infer_direction(
            src=src,
            dst=dst,
            src_port=src_port,
            dst_port=dst_port,
            client_host=client_host,
            server_host=server_host,
            server_port=server_port,
            client_port=client_port,
        )

        normalized.append(
            {
                "timestamp": timestamp,
                "src": src,
                "dst": dst,
                "src_port": src_port,
                "dst_port": dst_port,
                "proto": proto,
                "length": length,
                "direction": direction,
            }
        )

    return normalized


def rows_to_csv_text(rows: list[dict[str, Any]]) -> str:
    """Convert normalized trace rows to CSV text (with header)."""
    if not rows:
        return "timestamp,src,dst,src_port,dst_port,proto,length,direction\n"
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "timestamp", "src", "dst", "src_port", "dst_port",
            "proto", "length", "direction",
        ],
    )
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Tshark raw-row parser
# ---------------------------------------------------------------------------


_TSHARK_FIELDNAMES = [
    "frame.time_epoch",
    "ip.src",
    "ip.dst",
    "tcp.srcport",
    "tcp.dstport",
    "udp.srcport",
    "udp.dstport",
    "frame.protocols",
    "frame.len",
]


def parse_tshark_text(text: str) -> list[dict[str, str]]:
    """Parse tshark ``-T fields`` output into a list of dicts."""
    reader = csv.DictReader(
        io.StringIO(text),
        fieldnames=_TSHARK_FIELDNAMES,
    )
    rows: list[dict[str, str]] = []
    header_skipped = False
    for row in reader:
        # Skip the tshark header line (first row repeats field names)
        first_val = row.get("frame.time_epoch", "")
        if not header_skipped and first_val.strip() == "frame.time_epoch":
            header_skipped = True
            continue
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Subcommand: convert
# ---------------------------------------------------------------------------


def _run_convert(args: argparse.Namespace) -> None:
    """Convert a pcap to CSV, optionally using a raw-text fallback."""
    if not args.dry_run and not detect_tools().get("tshark", False):
        print(
            "Error: tshark not found. Install tshark or use --dry-run.",
            file=sys.stderr,
        )
        sys.exit(4)

    cmd = build_tshark_csv_command(
        input_pcap=args.input_pcap,
        output_csv=args.output_csv,
        client_host=args.client_host,
        server_host=args.server_host,
        server_port=args.server_port,
    )

    if args.dry_run:
        print(f"[dry-run] Would run: {' '.join(cmd)}")
        if args.text_input:
            print("[dry-run] Using --text-input fallback instead of tshark")
            raw_rows = parse_tshark_text(args.text_input)
            rows = normalize_trace_rows(
                raw_rows,
                client_host=args.client_host,
                server_host=args.server_host,
                server_port=args.server_port,
            )
            csv_text = rows_to_csv_text(rows)
            print(f"[dry-run] Would write {len(rows)} rows to {args.output_csv}")
            print("[dry-run] CSV preview:")
            for line in csv_text.splitlines()[:10]:
                print(f"  {line}")
            if len(csv_text.splitlines()) > 10:
                print(f"  ... ({len(csv_text.splitlines()) - 1} data rows total)")
        return

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            print(f"Error: tshark failed: {proc.stderr}", file=sys.stderr)
            sys.exit(5)

        raw_rows = parse_tshark_text(proc.stdout)
        rows = normalize_trace_rows(
            raw_rows,
            client_host=args.client_host,
            server_host=args.server_host,
            server_port=args.server_port,
        )
        csv_text = rows_to_csv_text(rows)
        Path(args.output_csv).write_text(csv_text, encoding="utf-8")
        print(f"Wrote {len(rows)} rows to {args.output_csv}")

    except subprocess.TimeoutExpired:
        print("Error: tshark timed out after 60s", file=sys.stderr)
        sys.exit(6)


# ---------------------------------------------------------------------------
# Subcommand: capture
# ---------------------------------------------------------------------------


def _run_capture(args: argparse.Namespace) -> None:
    """Run or dry-run a tcpdump capture."""
    tools = detect_tools()

    if not args.dry_run:
        if not tools.get("tcpdump", False):
            print(
                "Error: tcpdump not found. Install tcpdump or use --dry-run.",
                file=sys.stderr,
            )
            sys.exit(4)
        if not tools.get("timeout", False):
            print(
                "Error: timeout not found. Install coreutils or use --dry-run.",
                file=sys.stderr,
            )
            sys.exit(4)

    cmd = build_tcpdump_command(
        interface=args.interface,
        output_pcap=args.output_pcap,
        host=args.host,
        port=args.port,
        duration=args.duration,
        extra_filter=args.extra_filter or "",
        sudo=args.sudo,
    )

    if args.dry_run:
        print(f"[dry-run] Would run: {' '.join(cmd)}")
        return

    print(f"Running: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True, timeout=args.duration + 5)
        print(f"Capture complete: {args.output_pcap}")
    except subprocess.CalledProcessError as exc:
        print(f"Error: tcpdump exited with code {exc.returncode}", file=sys.stderr)
        sys.exit(5)
    except subprocess.TimeoutExpired:
        print("Error: capture timed out", file=sys.stderr)
        sys.exit(6)


# ---------------------------------------------------------------------------
# Subcommand: tools
# ---------------------------------------------------------------------------


def _run_tools(_args: argparse.Namespace) -> None:
    """Print tool availability as JSON."""
    print(json.dumps(detect_tools(), indent=2))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="VPN-LLM local offline trace capture tool (tcpdump/tshark wrapper).",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # ---- capture -----------------------------------------------------------
    cap = sub.add_parser("capture", help="Capture traffic with tcpdump.")
    cap.add_argument("--interface", required=True, help="Network interface (e.g. lo, eth0).")
    cap.add_argument("--host", default=None, help="Host filter (BPF).")
    cap.add_argument("--port", type=int, default=None, help="Port filter (BPF).")
    cap.add_argument("--duration", type=int, default=10, help="Capture duration in seconds.")
    cap.add_argument("--output-pcap", required=True, help="Path to output .pcap file.")
    cap.add_argument("--extra-filter", default="", help="Additional BPF filter expression.")
    cap.add_argument("--sudo", action="store_true", default=False, help="Prefix command with sudo.")
    cap.add_argument("--dry-run", action="store_true", default=False, help="Print command only, do not execute.")

    # ---- convert -----------------------------------------------------------
    conv = sub.add_parser("convert", help="Convert pcap to fingerprint CSV.")
    conv.add_argument("--input-pcap", required=True, help="Path to input .pcap file.")
    conv.add_argument("--output-csv", required=True, help="Path to output .csv file.")
    conv.add_argument("--client-host", required=True, help="Client IP address.")
    conv.add_argument("--server-host", required=True, help="Server IP address.")
    conv.add_argument("--server-port", type=int, default=None, help="Server port for direction disambiguation.")
    conv.add_argument("--client-port", type=int, default=None, help="Client port for direction disambiguation.")
    conv.add_argument("--text-input", default=None, help="Raw tshark text input for testing (dry-run only).")
    conv.add_argument("--dry-run", action="store_true", default=False, help="Print command only, do not execute.")

    # ---- tools -------------------------------------------------------------
    sub.add_parser("tools", help="Detect and print available tools as JSON.")

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "capture":
        _run_capture(args)
    elif args.subcommand == "convert":
        _run_convert(args)
    elif args.subcommand == "tools":
        _run_tools(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
