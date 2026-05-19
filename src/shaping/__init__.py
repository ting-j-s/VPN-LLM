"""Traffic shaping layer for VPN-LLM fingerprint mitigation.

Provides configurable traffic shaping strategies:
- Random padding to reduce repeated_length_ratio
- Small-frame aggregation to reduce small_packet_ratio
- Fragmentation skeleton for burst smoothing
- Jitter skeleton for timing gate

Default: NoopTrafficShaper (all shaping disabled).
"""

from .base import NoopTrafficShaper, ShapedChunk, TrafficShaper
from .config import ShapingConfig
from .factory import create_traffic_shaper
from .scheduler import SendScheduler

__all__ = [
    "ShapingConfig",
    "ShapedChunk",
    "TrafficShaper",
    "NoopTrafficShaper",
    "SendScheduler",
    "create_traffic_shaper",
]
