#!/usr/bin/env python3
"""Summarize VPN-LLM fingerprint evaluation reports across transport x scenario.

Scans ``<input-dir>/<transport>/<scenario>.report.json`` files and produces
a combined ``summary.json`` and ``summary.csv``.

Usage::

    python3 scripts/summarize_fingerprint_reports.py \\
        --input-dir traces \\
        --output-json traces/summary.json \\
        --output-csv traces/summary.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Optional

_VALID_TRANSPORTS = frozenset({"tcp", "tls", "websocket", "ssh"})
_VALID_SCENARIOS = frozenset({"idle", "ping", "curl", "bulk", "reconnect"})

_SUMMARY_FIELDNAMES = [
    "transport",
    "scenario",
    "trace_type",
    "packet_count",
    "risk_level",
    "fingerprint_risk_score",
    "small_packet_ratio",
    "repeated_length_ratio",
    "ngram_entropy",
    "dominant_ngram_ratio",
    "burst_count",
    "max_burst_size",
    "dominant_burst_direction_ratio",
    "avg_inter_arrival_ms",
    "notes",
]


def _load_report(path: Path) -> Optional[dict[str, Any]]:
    """Load a single fingerprint report JSON file.

    Returns ``None`` when the file is missing or unreadable.
    """
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _extract_row(
    transport: str,
    scenario: str,
    trace_type: str,
    report: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Build a single summary row from a report dict (or None)."""
    if report is None:
        return {
            "transport": transport,
            "scenario": scenario,
            "trace_type": trace_type,
            "packet_count": 0,
            "risk_level": "skipped",
            "fingerprint_risk_score": "",
            "small_packet_ratio": "",
            "repeated_length_ratio": "",
            "ngram_entropy": "",
            "dominant_ngram_ratio": "",
            "burst_count": "",
            "max_burst_size": "",
            "dominant_burst_direction_ratio": "",
            "avg_inter_arrival_ms": "",
            "notes": "report not found",
        }

    notes_raw = report.get("notes", [])
    notes_str = "; ".join(notes_raw) if isinstance(notes_raw, list) else str(notes_raw)

    return {
        "transport": transport,
        "scenario": scenario,
        "trace_type": trace_type,
        "packet_count": report.get("packet_count", 0),
        "risk_level": report.get("risk_level", "unknown"),
        "fingerprint_risk_score": report.get("fingerprint_risk_score", ""),
        "small_packet_ratio": report.get("small_packet_ratio", ""),
        "repeated_length_ratio": report.get("repeated_length_ratio", ""),
        "ngram_entropy": report.get("ngram_entropy", ""),
        "dominant_ngram_ratio": report.get("dominant_ngram_ratio", ""),
        "burst_count": report.get("burst_count", ""),
        "max_burst_size": report.get("max_burst_size", ""),
        "dominant_burst_direction_ratio": report.get(
            "dominant_burst_direction_ratio", ""
        ),
        "avg_inter_arrival_ms": report.get("avg_inter_arrival_ms", ""),
        "notes": notes_str,
    }


def scan_reports(
    input_dir: str | Path,
    *,
    transports: Optional[list[str]] = None,
    scenarios: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Scan a traces directory and collect fingerprint report rows.

    For each transport × scenario combination, reads the corresponding
    ``report.json`` and builds a summary row.  Missing reports produce a
    ``skipped`` row.

    Args:
        input_dir: Root directory containing per-transport subdirectories.
        transports: Transports to scan (default: all valid).
        scenarios: Scenarios to scan (default: all valid).

    Returns:
        A list of summary dicts ready for JSON / CSV output.
    """
    base = Path(input_dir)
    _transports = transports or sorted(_VALID_TRANSPORTS)
    _scenarios = scenarios or sorted(_VALID_SCENARIOS)

    rows: list[dict[str, Any]] = []

    for transport in _transports:
        transport_dir = base / transport
        for scenario in _scenarios:
            report_path = transport_dir / f"{scenario}.report.json"
            csv_path = transport_dir / f"{scenario}.csv"

            report = _load_report(report_path)
            if report is not None:
                trace_type = "real" if csv_path.is_file() else "synthetic"
            else:
                trace_type = "skipped"

            rows.append(_extract_row(transport, scenario, trace_type, report))

    return rows


def write_summary_json(rows: list[dict[str, Any]], output_path: str | Path) -> None:
    """Write summary rows as a JSON file."""
    output = {
        "entry_count": len(rows),
        "generated_by": "scripts/summarize_fingerprint_reports.py",
        "entries": rows,
    }
    Path(output_path).write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_summary_csv(rows: list[dict[str, Any]], output_path: str | Path) -> None:
    """Write summary rows as a CSV file."""
    path = Path(output_path)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_SUMMARY_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize VPN-LLM fingerprint evaluation reports.",
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        help="Root traces directory (e.g. traces/).",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Write summary JSON to this path.",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Write summary CSV to this path.",
    )
    parser.add_argument(
        "--transports",
        default=None,
        help="Comma-separated transports (default: all valid).",
    )
    parser.add_argument(
        "--scenarios",
        default=None,
        help="Comma-separated scenarios (default: all valid).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    transports = (
        [t.strip() for t in args.transports.split(",") if t.strip()]
        if args.transports
        else None
    )
    scenarios = (
        [s.strip() for s in args.scenarios.split(",") if s.strip()]
        if args.scenarios
        else None
    )

    rows = scan_reports(args.input_dir, transports=transports, scenarios=scenarios)

    if args.output_json:
        write_summary_json(rows, args.output_json)
        print(f"Summary JSON written to {args.output_json}")

    if args.output_csv:
        write_summary_csv(rows, args.output_csv)
        print(f"Summary CSV written to {args.output_csv}")

    if not args.output_json and not args.output_csv:
        # Print to stdout
        print(json.dumps(rows, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
