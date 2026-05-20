"""ShapingConfig — configuration for traffic shaping strategies.

All strategies default to disabled so existing transport behavior is preserved.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ShapingConfig:
    """Traffic shaping configuration.

    All fields default to disabled. Enable individual strategies as needed.

    Validation rules:
    - max_padding_bytes >= min_padding_bytes
    - aggregation_max_bytes >= 0
    - jitter_max_ms >= jitter_min_ms
    - fragmentation_max_chunk_size >= fragmentation_min_size
    - timing_max_delay_ms >= timing_min_delay_ms
    - timing_mode in {"metadata_only", "runtime_sleep"}
    """

    enabled: bool = False

    padding_enabled: bool = False
    min_padding_bytes: int = 0
    max_padding_bytes: int = 0

    aggregation_enabled: bool = False
    aggregation_max_delay_ms: float = 0.0
    aggregation_max_bytes: int = 0

    fragmentation_enabled: bool = False
    fragmentation_min_size: int = 0
    fragmentation_max_chunk_size: int = 0

    jitter_enabled: bool = False
    jitter_min_ms: float = 0.0
    jitter_max_ms: float = 0.0

    dummy_enabled: bool = False

    # Phase 7B: RTT-aware timing countermeasures (default disabled)
    timing_enabled: bool = False
    timing_mode: str = "metadata_only"
    timing_min_delay_ms: float = 0.0
    timing_max_delay_ms: float = 0.0
    timing_target_min_rtt_ms: float | None = None
    timing_randomize_intervals: bool = False

    _VALID_TIMING_MODES = frozenset({"metadata_only", "runtime_sleep"})

    def validate(self) -> None:
        """Validate configuration. Raises ValueError on invalid values."""
        if self.padding_enabled:
            if self.min_padding_bytes < 0:
                raise ValueError("min_padding_bytes must be >= 0")
            if self.max_padding_bytes < self.min_padding_bytes:
                raise ValueError(
                    f"max_padding_bytes ({self.max_padding_bytes}) must be >= "
                    f"min_padding_bytes ({self.min_padding_bytes})"
                )

        if self.aggregation_enabled:
            if self.aggregation_max_bytes < 0:
                raise ValueError("aggregation_max_bytes must be >= 0")
            if self.aggregation_max_delay_ms < 0:
                raise ValueError("aggregation_max_delay_ms must be >= 0")

        if self.fragmentation_enabled:
            if self.fragmentation_min_size < 0:
                raise ValueError("fragmentation_min_size must be >= 0")
            if self.fragmentation_max_chunk_size < self.fragmentation_min_size:
                raise ValueError(
                    f"fragmentation_max_chunk_size ({self.fragmentation_max_chunk_size}) "
                    f"must be >= fragmentation_min_size ({self.fragmentation_min_size})"
                )

        if self.jitter_enabled:
            if self.jitter_min_ms < 0:
                raise ValueError("jitter_min_ms must be >= 0")
            if self.jitter_max_ms < self.jitter_min_ms:
                raise ValueError(
                    f"jitter_max_ms ({self.jitter_max_ms}) must be >= "
                    f"jitter_min_ms ({self.jitter_min_ms})"
                )

        if self.dummy_enabled:
            # dummy_enabled reserves the field for future use; no validation needed
            pass

        if self.timing_enabled:
            if self.timing_mode not in self._VALID_TIMING_MODES:
                raise ValueError(
                    f"Invalid timing_mode {self.timing_mode!r}; "
                    f"must be one of {sorted(self._VALID_TIMING_MODES)}"
                )
            if self.timing_min_delay_ms < 0:
                raise ValueError("timing_min_delay_ms must be >= 0")
            if self.timing_max_delay_ms < self.timing_min_delay_ms:
                raise ValueError(
                    f"timing_max_delay_ms ({self.timing_max_delay_ms}) must be >= "
                    f"timing_min_delay_ms ({self.timing_min_delay_ms})"
                )
            if self.timing_target_min_rtt_ms is not None and self.timing_target_min_rtt_ms < 0:
                raise ValueError("timing_target_min_rtt_ms must be >= 0")

    @classmethod
    def from_dict(cls, d: dict) -> ShapingConfig:
        """Create ShapingConfig from a dictionary. Backward-compatible with old configs."""
        return cls(
            enabled=d.get("enabled", False),
            padding_enabled=d.get("padding_enabled", False),
            min_padding_bytes=d.get("min_padding_bytes", 0),
            max_padding_bytes=d.get("max_padding_bytes", 0),
            aggregation_enabled=d.get("aggregation_enabled", False),
            aggregation_max_delay_ms=d.get("aggregation_max_delay_ms", 0.0),
            aggregation_max_bytes=d.get("aggregation_max_bytes", 0),
            fragmentation_enabled=d.get("fragmentation_enabled", False),
            fragmentation_min_size=d.get("fragmentation_min_size", 0),
            fragmentation_max_chunk_size=d.get("fragmentation_max_chunk_size", 0),
            jitter_enabled=d.get("jitter_enabled", False),
            jitter_min_ms=d.get("jitter_min_ms", 0.0),
            jitter_max_ms=d.get("jitter_max_ms", 0.0),
            dummy_enabled=d.get("dummy_enabled", False),
            # Phase 7B timing fields
            timing_enabled=d.get("timing_enabled", False),
            timing_mode=d.get("timing_mode", "metadata_only"),
            timing_min_delay_ms=d.get("timing_min_delay_ms", 0.0),
            timing_max_delay_ms=d.get("timing_max_delay_ms", 0.0),
            timing_target_min_rtt_ms=d.get("timing_target_min_rtt_ms", None),
            timing_randomize_intervals=d.get("timing_randomize_intervals", False),
        )

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "enabled": self.enabled,
            "padding_enabled": self.padding_enabled,
            "min_padding_bytes": self.min_padding_bytes,
            "max_padding_bytes": self.max_padding_bytes,
            "aggregation_enabled": self.aggregation_enabled,
            "aggregation_max_delay_ms": self.aggregation_max_delay_ms,
            "aggregation_max_bytes": self.aggregation_max_bytes,
            "fragmentation_enabled": self.fragmentation_enabled,
            "fragmentation_min_size": self.fragmentation_min_size,
            "fragmentation_max_chunk_size": self.fragmentation_max_chunk_size,
            "jitter_enabled": self.jitter_enabled,
            "jitter_min_ms": self.jitter_min_ms,
            "jitter_max_ms": self.jitter_max_ms,
            "dummy_enabled": self.dummy_enabled,
            # Phase 7B timing fields
            "timing_enabled": self.timing_enabled,
            "timing_mode": self.timing_mode,
            "timing_min_delay_ms": self.timing_min_delay_ms,
            "timing_max_delay_ms": self.timing_max_delay_ms,
            "timing_target_min_rtt_ms": self.timing_target_min_rtt_ms,
            "timing_randomize_intervals": self.timing_randomize_intervals,
        }
