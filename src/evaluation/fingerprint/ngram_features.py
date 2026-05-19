"""3-gram signed-packet-length-sequence features for VPN fingerprintability.

Inspired by "Fingerprinting Obfuscated Proxy Traffic with Encapsulated
TLS Handshakes" — the idea is that tunnel-internal TLS handshakes produce
stable patterns in (signed size, direction) n-gram distributions even when
the outer transport layer varies.  High dominant-ngram ratios and low entropy
are fingerprintability indicators, not classifiers.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Sequence

from .pcap_features import FlowPacket

_L0 = "L0"


def length_bucket(length: int) -> str:
    """Map a packet length to a bucket label.

    Buckets follow the paper-style 4-bin mapping:
    - ``L0``: length <= 0 (invalid / runt)
    - ``L1``: 1 – 160
    - ``L2``: 161 – 600
    - ``L3``: 601 – 1210
    - ``L4``: 1211+

    Raises:
        ValueError: if *length* is negative.
    """
    if length < 0:
        raise ValueError(f"length must be >= 0, got {length}")
    if length == 0:
        return _L0
    if length <= 160:
        return "L1"
    if length <= 600:
        return "L2"
    if length <= 1210:
        return "L3"
    return "L4"


def signed_bucket(packet: FlowPacket) -> str:
    """Return a signed bucket label: ``+Lx`` for C2S, ``-Lx`` for S2C."""
    bucket = length_bucket(packet.length)
    sign = "+" if packet.direction == "C2S" else "-"
    return f"{sign}{bucket}"


def signed_size(packet: FlowPacket) -> int:
    """Return positive length for C2S, negative for S2C."""
    return packet.length if packet.direction == "C2S" else -packet.length


def extract_ngrams(items: Sequence[str], n: int = 3) -> list[tuple[str, ...]]:
    """Sliding-window n-gram extraction.

    Returns:
        List of n-tuples.  Empty list when ``len(items) < n``.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    if len(items) < n:
        return []
    return [tuple(items[i : i + n]) for i in range(len(items) - n + 1)]


def ngram_counts(ngrams: Sequence[tuple[str, ...]]) -> dict[str, int]:
    """Count n-gram occurrences, keyed by readable strings like ``"+L2|-L4|-L4"``."""
    counts: dict[str, int] = {}
    for ng in ngrams:
        key = "|".join(ng)
        counts[key] = counts.get(key, 0) + 1
    return counts


def ngram_entropy(counts: dict[str, int]) -> float:
    """Shannon entropy (bits) of an n-gram distribution.

    Returns 0.0 for empty input.
    """
    total = sum(counts.values())
    if total == 0:
        return 0.0
    entropy = 0.0
    for c in counts.values():
        if c > 0:
            p = c / total
            entropy -= p * math.log2(p)
    return entropy


def top_ngrams(counts: dict[str, int], k: int = 10) -> list[dict[str, Any]]:
    """Return the top-*k* most frequent n-grams with count and ratio."""
    total = sum(counts.values())
    if total == 0:
        return []
    sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:k]
    return [
        {"ngram": key, "count": cnt, "ratio": round(cnt / total, 4)}
        for key, cnt in sorted_items
    ]


def extract_ngram_features(
    packets: Sequence[FlowPacket],
    n: int = 3,
) -> dict[str, Any]:
    """Compute n-gram features from a packet sequence.

    Packets are sorted by timestamp internally.

    Returns a dict with keys:
        signed_sizes, bucket_sequence, ngram_n, ngram_count,
        unique_ngram_count, ngram_entropy, top_ngrams, dominant_ngram_ratio.
    """
    sorted_packets = sorted(packets, key=lambda p: p.timestamp)

    sizes = [signed_size(p) for p in sorted_packets]
    buckets = [signed_bucket(p) for p in sorted_packets]

    ngrams = extract_ngrams(buckets, n=n)
    counts = ngram_counts(ngrams)

    top = top_ngrams(counts, k=10)
    dominant_ratio = top[0]["ratio"] if top else 0.0

    return {
        "signed_sizes": sizes,
        "bucket_sequence": buckets,
        "ngram_n": n,
        "ngram_count": len(ngrams),
        "unique_ngram_count": len(counts),
        "ngram_entropy": round(ngram_entropy(counts), 4),
        "top_ngrams": top[:10],
        "dominant_ngram_ratio": round(dominant_ratio, 4),
    }
