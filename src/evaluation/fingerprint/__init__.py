"""Local offline fingerprintability evaluation for VPN-LLM.

This module provides trace-based feature extraction and risk scoring
for controlled, offline analysis of VPN tunnel traffic patterns.
It does NOT implement real DPI, does NOT scan third parties, and
does NOT modify VPN core, transport, or frame codec runtime logic.

Exports:
    FlowPacket, FlowFeatures — core data structures
    load_trace_csv              — CSV trace loader
    extract_features            — feature extraction from packets
    summarize_trace             — high-level trace summary dict
    Burst                       — burst data structure
    extract_bursts              — burst partitioning
    extract_ngram_features      — 3-gram feature extraction
"""

from .burst_features import Burst, extract_bursts, summarize_bursts
from .ngram_features import (
    extract_ngram_features,
    extract_ngrams,
    length_bucket,
    ngram_counts,
    ngram_entropy,
    signed_bucket,
    signed_size,
    top_ngrams,
)
from .pcap_features import (
    FlowFeatures,
    FlowPacket,
    extract_features,
    load_trace_csv,
    summarize_trace,
)

__all__ = [
    "FlowPacket",
    "FlowFeatures",
    "Burst",
    "load_trace_csv",
    "extract_features",
    "summarize_trace",
    "extract_ngram_features",
    "extract_ngrams",
    "length_bucket",
    "signed_bucket",
    "signed_size",
    "ngram_counts",
    "ngram_entropy",
    "top_ngrams",
    "extract_bursts",
    "summarize_bursts",
]
