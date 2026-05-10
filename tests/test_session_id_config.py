"""Tests for session_id parsing, masking, config, and integration."""

import pytest
import uuid

from src.common.session import parse_session_id, mask_session_id
from src.common.config import SessionConfig, ConfigError, load_client_config, load_server_config


# ---------------------------------------------------------------------------
# parse_session_id
# ---------------------------------------------------------------------------


class TestParseSessionId:
    """Test parse_session_id validation."""

    def test_none_returns_none(self):
        assert parse_session_id(None) is None

    def test_empty_string_returns_none(self):
        assert parse_session_id("") is None

    def test_whitespace_only_returns_none(self):
        assert parse_session_id("   ") is None

    def test_valid_lowercase_hex(self):
        result = parse_session_id("00112233445566778899aabbccddeeff")
        assert isinstance(result, bytes)
        assert len(result) == 16
        assert result == bytes.fromhex("00112233445566778899aabbccddeeff")

    def test_valid_uppercase_hex(self):
        result = parse_session_id("00112233445566778899AABBCCDDEEFF")
        assert isinstance(result, bytes)
        assert len(result) == 16

    def test_valid_mixed_case_hex(self):
        result = parse_session_id("00112233445566778899AaBbCcDdEeFf")
        assert isinstance(result, bytes)
        assert len(result) == 16

    def test_non_hex_raises_value_error(self):
        with pytest.raises(ValueError, match="session_id"):
            parse_session_id("not-a-hex-string-gggggggg!!")

    def test_too_short_raises_value_error(self):
        with pytest.raises(ValueError, match="32 hex"):
            parse_session_id("0011223344556677")

    def test_too_long_raises_value_error(self):
        with pytest.raises(ValueError, match="32 hex"):
            parse_session_id("00112233445566778899aabbccddeeff00")

    def test_single_char_raises_value_error(self):
        with pytest.raises(ValueError, match="32 hex"):
            parse_session_id("a")

    def test_odd_length_hex_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_session_id("abc")

    def test_strips_whitespace(self):
        result = parse_session_id("  00112233445566778899aabbccddeeff  ")
        assert result == bytes.fromhex("00112233445566778899aabbccddeeff")

    def test_non_hex_chars_in_hex_string(self):
        with pytest.raises(ValueError):
            parse_session_id("ghijklmnopqrstuvwxyz0123456789ab")


# ---------------------------------------------------------------------------
# mask_session_id
# ---------------------------------------------------------------------------


class TestMaskSessionId:
    """Test mask_session_id does not leak full session ID."""

    def test_none_returns_none(self):
        assert mask_session_id(None) == "none"

    def test_short_bytes_returns_invalid(self):
        assert mask_session_id(b"short") == "invalid"

    def test_long_bytes_returns_invalid(self):
        assert mask_session_id(b"x" * 20) == "invalid"

    def test_shows_only_first_8_hex_chars(self):
        sid = uuid.uuid4().bytes
        masked = mask_session_id(sid)
        assert len(masked) == 11  # 8 hex + "..."
        assert masked.endswith("...")
        assert masked[:-3] == sid.hex()[:8]

    def test_does_not_contain_full_session_id(self):
        sid = uuid.uuid4().bytes
        masked = mask_session_id(sid)
        full_hex = sid.hex()
        assert full_hex not in masked
        assert full_hex[8:] not in masked


# ---------------------------------------------------------------------------
# SessionConfig defaults
# ---------------------------------------------------------------------------


class TestSessionConfigDefaults:
    """Test SessionConfig default values including session_id."""

    def test_default_session_id_is_none(self):
        config = SessionConfig()
        assert config.session_id is None

    def test_explicit_session_id(self):
        config = SessionConfig(session_id="00112233445566778899aabbccddeeff")
        assert config.session_id == "00112233445566778899aabbccddeeff"

    def test_explicit_session_id_is_stored_as_string(self):
        config = SessionConfig(session_id="00112233445566778899aabbccddeeff")
        assert isinstance(config.session_id, str)


# ---------------------------------------------------------------------------
# Config file loading — session_id field
# ---------------------------------------------------------------------------


class TestClientConfigSessionId:
    """Test session_id is loaded from client YAML config."""

    def test_session_id_from_config_file(self, tmp_path):
        config_file = tmp_path / "client.yaml"
        config_file.write_text("""
server:
  host: 127.0.0.1
  username: testuser
  ssh_key_path: /path/to/key

session:
  session_id: "00112233445566778899aabbccddeeff"
""")
        config = load_client_config(str(config_file))
        assert config.session.session_id == "00112233445566778899aabbccddeeff"

    def test_no_session_id_defaults_to_none(self, tmp_path):
        config_file = tmp_path / "client.yaml"
        config_file.write_text("""
server:
  host: 127.0.0.1
  username: testuser
  ssh_key_path: /path/to/key
""")
        config = load_client_config(str(config_file))
        assert config.session.session_id is None


class TestServerConfigSessionId:
    """Test session_id is loaded from server YAML config."""

    def test_session_id_from_config_file(self, tmp_path):
        config_file = tmp_path / "server.yaml"
        config_file.write_text("""
session:
  session_id: "aabbccdd11223344556677889900eeff"
""")
        config = load_server_config(str(config_file))
        assert config.session.session_id == "aabbccdd11223344556677889900eeff"

    def test_no_session_id_defaults_to_none(self, tmp_path):
        config_file = tmp_path / "server.yaml"
        config_file.write_text("""
forwarding:
  enable_nat: false
""")
        config = load_server_config(str(config_file))
        assert config.session.session_id is None


# ---------------------------------------------------------------------------
# CLI priority integration test (via entry point args)
# ---------------------------------------------------------------------------


class TestSessionIdPriority:
    """Test CLI --session-id > config session_id > random (None)."""

    def test_parse_session_id_accepts_valid_hex(self):
        """parse_session_id is the gate for both CLI and config values."""
        sid = "deadbeef00000000cafebabe12345678"
        result = parse_session_id(sid)
        assert result == bytes.fromhex(sid)

    def test_parse_session_id_none_passthrough(self):
        """None passes through for random generation in Core."""
        assert parse_session_id(None) is None

    def test_config_can_hold_session_id_string(self):
        """SessionConfig.session_id is a plain string, not parsed."""
        config = SessionConfig(session_id="feedface000000001234567890abcdef")
        assert config.session_id == "feedface000000001234567890abcdef"


# ---------------------------------------------------------------------------
# Core integration — same session_id → no mismatch drops
# ---------------------------------------------------------------------------


class TestSharedSessionIdIntegration:
    """Verify Core classes work with shared session IDs."""

    def test_server_core_accepts_explicit_session_id(self):
        from src.core.server_core import ServerCore
        from src.tun.tun_device import MockTunDevice
        from src.transport.base import MockTransport

        sid = uuid.uuid4().bytes
        core = ServerCore(
            tun=MockTunDevice(),
            transport=MockTransport(),
            session_id=sid,
        )
        assert core.session_id == sid

    def test_client_core_accepts_explicit_session_id(self):
        from src.core.client_core import ClientCore
        from src.tun.tun_device import MockTunDevice
        from src.transport.base import MockTransport

        sid = uuid.uuid4().bytes
        core = ClientCore(
            tun=MockTunDevice(),
            transport=MockTransport(),
            session_id=sid,
        )
        assert core.session_id == sid

    def test_same_session_id_no_drops(self):
        """Client DATA frame with matching session_id is written to server TUN."""
        from src.core.server_core import ServerCore
        from src.tun.tun_device import MockTunDevice
        from src.transport.base import MockTransport
        from src.common.frame import create_frame, encode_frame, FrameType

        sid = uuid.uuid4().bytes
        server_tun = MockTunDevice()
        server_transport = MockTransport()

        server = ServerCore(
            tun=server_tun,
            transport=server_transport,
            session_id=sid,
        )

        server_transport.connect()
        server.start()

        # Inject a DATA frame with matching session ID
        frame = create_frame(FrameType.DATA, sid, b"hello")
        server_transport.inject(encode_frame(frame))

        import time
        time.sleep(0.3)

        packets = server_tun.get_tx_packets()
        assert len(packets) == 1
        assert packets[0] == b"hello"

        server.stop()

    def test_different_session_id_still_drops(self):
        """Mismatch drop behavior is preserved."""
        from src.core.server_core import ServerCore
        from src.tun.tun_device import MockTunDevice
        from src.transport.base import MockTransport
        from src.common.frame import create_frame, encode_frame, FrameType

        sid = uuid.uuid4().bytes
        wrong_sid = uuid.uuid4().bytes
        server_tun = MockTunDevice()
        server_transport = MockTransport()

        server = ServerCore(
            tun=server_tun,
            transport=server_transport,
            session_id=sid,
        )

        server_transport.connect()
        server.start()

        frame = create_frame(FrameType.DATA, wrong_sid, b"evil")
        server_transport.inject(encode_frame(frame))

        import time
        time.sleep(0.3)

        packets = server_tun.get_tx_packets()
        assert len(packets) == 0  # dropped

        server.stop()


# ---------------------------------------------------------------------------
# Auto-generated session ID when none provided
# ---------------------------------------------------------------------------


class TestAutoGeneratedSessionId:
    """Verify auto-generation still works when session_id is None."""

    def test_server_core_auto_generates_when_none(self):
        from src.core.server_core import ServerCore
        from src.tun.tun_device import MockTunDevice
        from src.transport.base import MockTransport

        core = ServerCore(
            tun=MockTunDevice(),
            transport=MockTransport(),
            session_id=None,
        )
        assert isinstance(core.session_id, bytes)
        assert len(core.session_id) == 16

    def test_client_core_auto_generates_when_none(self):
        from src.core.client_core import ClientCore
        from src.tun.tun_device import MockTunDevice
        from src.transport.base import MockTransport

        core = ClientCore(
            tun=MockTunDevice(),
            transport=MockTransport(),
            session_id=None,
        )
        assert isinstance(core.session_id, bytes)
        assert len(core.session_id) == 16

    def test_auto_generated_ids_are_different(self):
        """Without explicit session_id, each Core gets a random one."""
        from src.core.server_core import ServerCore
        from src.core.client_core import ClientCore
        from src.tun.tun_device import MockTunDevice
        from src.transport.base import MockTransport

        server = ServerCore(
            tun=MockTunDevice(name="s"),
            transport=MockTransport(),
            session_id=None,
        )
        client = ClientCore(
            tun=MockTunDevice(name="c"),
            transport=MockTransport(),
            session_id=None,
        )
        assert server.session_id != client.session_id
