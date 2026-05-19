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

    @classmethod
    def from_dict(cls, d: dict) -> ShapingConfig:
        """Create ShapingConfig from a dictionary."""
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
        }
