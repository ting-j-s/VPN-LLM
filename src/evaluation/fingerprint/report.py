"""CLI for offline fingerprintability report generation.

Usage::

    python3 -m src.evaluation.fingerprint.report --input trace.csv
    python3 -m src.evaluation.fingerprint.report --input trace.csv --output report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .pcap_features import summarize_trace


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a local offline fingerprintability report from a CSV trace.",
    )
    parser.add_argument(
        "--input",
        required=True,
        type=str,
        help="Path to CSV trace file.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to output JSON file. If omitted, print to stdout.",
    )
    parser.add_argument(
        "--first-n",
        type=int,
        default=30,
        help="Number of leading packets for early-handshake feature extraction (default: 30).",
    )
    parser.add_argument(
        "--small-packet-threshold",
        type=int,
        default=128,
        help="Byte threshold below which a packet is considered 'small' (default: 128).",
    )
    parser.add_argument(
        "--ngram-n",
        type=int,
        default=3,
        help="n-gram size for signed-length-sequence analysis (default: 3).",
    )
    parser.add_argument(
        "--burst-gap-ms",
        type=float,
        default=10.0,
        help="Max inter-arrival gap (ms) to merge packets into one burst (default: 10.0).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Number of top n-grams to report (default: 10).",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    try:
        summary: dict[str, Any] = summarize_trace(
            input_path,
            first_n=args.first_n,
            small_packet_threshold=args.small_packet_threshold,
            ngram_n=args.ngram_n,
            burst_gap_ms=args.burst_gap_ms,
        )
    except Exception as exc:
        print(f"Error: failed to process trace: {exc}", file=sys.stderr)
        sys.exit(2)

    # Trim top_ngrams to requested k
    if "top_ngrams" in summary and args.top_k < len(summary["top_ngrams"]):
        summary["top_ngrams"] = summary["top_ngrams"][: args.top_k]

    json_text = json.dumps(summary, indent=2, ensure_ascii=False)

    if args.output:
        output_path = Path(args.output)
        try:
            output_path.write_text(json_text, encoding="utf-8")
        except OSError as exc:
            print(f"Error: cannot write output file: {exc}", file=sys.stderr)
            sys.exit(3)
    else:
        print(json_text)


if __name__ == "__main__":
    main()
