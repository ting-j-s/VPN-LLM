"""Probe scenario definitions for active probe resistance testing.

Each scenario represents a malformed or unexpected input that an active
prober might send to fingerprint the VPN server's error behavior.

All payloads are for local controlled testing only.
"""

from dataclasses import dataclass, field


@dataclass
class ProbeScenario:
    """A single probe scenario for active probe resistance testing.

    Attributes:
        name: Short unique name (e.g. "empty_connection").
        description: Human-readable description of the probe.
        payload: Raw bytes to send. None means connect and send nothing.
        expected_policy: The desired server behavior under unified-error policy.
        timeout_s: Max time to wait for response/close after sending.
        tags: Categorization tags (e.g. ["malformed", "short"]).
    """

    name: str
    description: str
    payload: bytes | None
    expected_policy: str
    timeout_s: float = 2.0
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Built-in probe scenarios (local testing only)
# ---------------------------------------------------------------------------

PROBE_SCENARIOS: list[ProbeScenario] = [
    ProbeScenario(
        name="empty_connection",
        description="Connect then send nothing — wait for server timeout/close",
        payload=None,
        expected_policy="unified close after timeout; no response data",
        timeout_s=4.0,
        tags=["idle", "empty"],
    ),
    ProbeScenario(
        name="one_zero",
        description="Send single zero byte",
        payload=b"\x00",
        expected_policy="silent close; no response data",
        timeout_s=2.0,
        tags=["malformed", "short"],
    ),
    ProbeScenario(
        name="two_zero",
        description="Send two zero bytes",
        payload=b"\x00\x00",
        expected_policy="silent close; no response data",
        timeout_s=2.0,
        tags=["malformed", "short"],
    ),
    ProbeScenario(
        name="random_2k",
        description="Send 2048 random bytes (not a valid frame)",
        payload=bytes(
            [0x7f, 0x3c, 0x91, 0x4e, 0xa2, 0xbb, 0x55, 0x18]
            + [0x00] * 2040
        ),
        expected_policy="silent close; no response data",
        timeout_s=2.0,
        tags=["malformed", "noise", "large"],
    ),
    ProbeScenario(
        name="short_frame_header",
        description="Send incomplete VPN-LLM frame header (< 26 bytes)",
        payload=b"VTUN\x01",
        expected_policy="silent close; no response data",
        timeout_s=2.0,
        tags=["malformed", "short", "protocol"],
    ),
    ProbeScenario(
        name="bad_magic",
        description="Send valid-length frame header with wrong magic bytes",
        payload=b"XXXX\x01\x01\x00\x00\x00\x00" + b"\x00" * 16,
        expected_policy="silent close; no response data",
        timeout_s=2.0,
        tags=["malformed", "protocol"],
    ),
    ProbeScenario(
        name="invalid_frame_type",
        description="Send valid magic/length but invalid frame type (0xFF)",
        payload=b"VTUN\x01\xFF\x00\x00\x00\x00" + b"\x00" * 16,
        expected_policy="silent close; no response data",
        timeout_s=2.0,
        tags=["malformed", "protocol"],
    ),
    ProbeScenario(
        name="bad_session_id",
        description="Send valid DATA frame with wrong session_id (all zeros)",
        payload=(
            b"VTUN"           # magic
            b"\x01"           # version
            b"\x01"           # type = DATA
            b"\x00\x00\x00\x04"  # payload length = 4
            + b"\x00" * 16    # session_id = zeros
            + b"abcd"         # payload
        ),
        expected_policy="silent drop; no response; keep connection",
        timeout_s=2.0,
        tags=["malformed", "protocol", "session"],
    ),
    ProbeScenario(
        name="declared_length_too_large",
        description="Valid header declaring 1MB payload but only 10 bytes follow",
        payload=(
            b"VTUN"
            b"\x01"
            b"\x01"
            b"\x00\x0f\x42\x40"  # length = 1,000,000
            + b"\x00" * 16
            + b"shortdata"        # only 9 bytes actual
        ),
        expected_policy="silent close; no response data",
        timeout_s=2.0,
        tags=["malformed", "protocol", "truncated"],
    ),
    ProbeScenario(
        name="http_get_to_vpn_port",
        description="Send minimal HTTP GET request to VPN port",
        payload=b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n",
        expected_policy="silent close; no application response",
        timeout_s=2.0,
        tags=["malformed", "http", "noise"],
    ),
    ProbeScenario(
        name="tls_clienthello_like",
        description="Send static TLS ClientHello-like bytes (local test payload only)",
        payload=(
            b"\x16\x03\x01\x00\x54"   # TLS record header
            b"\x01\x00\x00\x50"       # Handshake: ClientHello
            b"\x03\x03" + b"\x00" * 32  # random
            + b"\x00\x04" + b"\x00\x02" + b"\x00\x00"  # no cipher suites
            + b"\x00\x01\x00"         # no compression
        ),
        expected_policy="silent close; no TLS-level response",
        timeout_s=2.0,
        tags=["malformed", "tls", "noise"],
    ),
    ProbeScenario(
        name="valid_heartbeat_wrong_session",
        description="Send structurally valid HEARTBEAT with wrong session_id",
        payload=(
            b"VTUN"
            b"\x01"
            b"\x02"                     # type = HEARTBEAT
            b"\x00\x00\x00\x00"         # length = 0
            + b"\xAB" * 16              # wrong session_id
        ),
        expected_policy="silent drop; no response; keep connection",
        timeout_s=2.0,
        tags=["malformed", "protocol", "session"],
    ),
]


def get_scenario_by_name(name: str) -> ProbeScenario | None:
    """Look up a built-in scenario by name."""
    for s in PROBE_SCENARIOS:
        if s.name == name:
            return s
    return None
