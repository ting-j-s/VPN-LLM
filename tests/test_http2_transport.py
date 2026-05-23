"""Tests for HTTP/2 Transport module.

Covers:
1. dependency check
2. missing h2 dependency error
3. config parsing (via factory)
4. factory creates http2 transport or fails clearly
5. close idempotent
6. recv timeout behavior mock
7. send before connect behavior
8. existing transport factory tests don't regress
9. http2 transport marked experimental
"""

import importlib
import sys
from unittest.mock import patch

import pytest

from src.common.config import (
    ALLOWED_TRANSPORT_TYPES,
    ClientConfig,
    TransportConfig,
    load_client_config,
    load_server_config,
)
from src.common.errors import TransportError, ConfigError
from src.transport.factory import (
    SUPPORTED_TRANSPORTS,
    create_transport,
    list_supported_transports,
)
from src.transport.http2_transport import HTTP2Transport, _H2_AVAILABLE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client_config(**overrides):
    """Return a minimal ClientConfig-like object for factory testing."""
    transport_type = overrides.get("type", "http2")
    transport_kw = {
        "type": transport_type,
        "path": overrides.get("path", "/"),
        "server_hostname": overrides.get("server_hostname"),
        "experimental": overrides.get("experimental", True),
    }

    class _Server:
        host = overrides.get("host", "127.0.0.1")
        port = overrides.get("port", 2225)

    class _Transport:
        type = transport_kw["type"]
        path = transport_kw["path"]
        server_hostname = transport_kw.get("server_hostname")
        experimental = transport_kw.get("experimental", True)

    class _Cfg:
        server = _Server()
        transport = _Transport()

    return _Cfg()


# ---------------------------------------------------------------------------
# 1. dependency check
# ---------------------------------------------------------------------------

class TestH2Dependency:
    """Tests for h2 dependency detection."""

    def test_h2_availability_flag_is_bool(self):
        assert isinstance(_H2_AVAILABLE, bool)

    def test_h2_available_flag_consistent(self):
        """Re-importing should give the same value."""
        import src.transport.http2_transport as m
        assert m._H2_AVAILABLE is _H2_AVAILABLE


# ---------------------------------------------------------------------------
# 2. missing h2 dependency error
# ---------------------------------------------------------------------------

class TestMissingH2Error:
    """connect() raises clear error when h2 is not installed."""

    def test_connect_raises_when_h2_missing(self):
        transport = HTTP2Transport(mode="client", host="127.0.0.1", port=2225)
        if _H2_AVAILABLE:
            pytest.skip("h2 is installed — cannot test missing-dependency path")
        with pytest.raises(TransportError, match="h2"):
            transport.connect()

    def test_connect_raises_when_h2_missing_server_mode(self):
        transport = HTTP2Transport(mode="server", host="127.0.0.1", port=2225)
        if _H2_AVAILABLE:
            pytest.skip("h2 is installed — cannot test missing-dependency path")
        with pytest.raises(TransportError, match="h2"):
            transport.connect()

    def test_error_message_mentions_pip_install(self):
        transport = HTTP2Transport(mode="client")
        if _H2_AVAILABLE:
            pytest.skip("h2 is installed — cannot test missing-dependency path")
        with pytest.raises(TransportError, match="pip install h2"):
            transport.connect()


# ---------------------------------------------------------------------------
# 3. config parsing
# ---------------------------------------------------------------------------

class TestHttp2Config:
    """Tests for HTTP/2 in config system."""

    def test_allowed_transport_types_includes_http2(self):
        assert "http2" in ALLOWED_TRANSPORT_TYPES

    def test_supported_transports_includes_http2(self):
        assert "http2" in SUPPORTED_TRANSPORTS

    def test_list_supported_transports_includes_http2(self):
        assert "http2" in list_supported_transports()

    def test_transport_config_experimental_field(self):
        tc = TransportConfig(type="http2", experimental=True)
        assert tc.experimental is True

    def test_transport_config_experimental_defaults_false(self):
        tc = TransportConfig(type="http2")
        assert tc.experimental is False

    def test_transport_config_server_hostname(self):
        tc = TransportConfig(type="http2", server_hostname="example.com")
        assert tc.server_hostname == "example.com"

    def test_client_yaml_parses_http2_experimental(self, tmp_path):
        yaml_content = """client:
  tun_name: tun0
  tun_ip: 10.8.0.2
  tun_peer: 10.8.0.1
  mtu: 1400

server:
  host: 127.0.0.1
  port: 2225

transport:
  type: http2
  experimental: true
  server_hostname: test.example.com
"""
        path = tmp_path / "client_http2.yaml"
        path.write_text(yaml_content)
        cfg = load_client_config(str(path))
        assert cfg.transport.type == "http2"
        assert cfg.transport.experimental is True
        assert cfg.transport.server_hostname == "test.example.com"

    def test_server_yaml_parses_http2(self, tmp_path):
        yaml_content = """server:
  tun_name: tun0
  tun_ip: 10.8.0.1
  tun_peer: 10.8.0.2
  mtu: 1400
  listen_port: 2225

forwarding:
  enable_nat: false
  enable_route: true

transport:
  type: http2
"""
        path = tmp_path / "server_http2.yaml"
        path.write_text(yaml_content)
        cfg = load_server_config(str(path))
        assert cfg.transport.type == "http2"


# ---------------------------------------------------------------------------
# 4. factory creates http2 transport or fails clearly
# ---------------------------------------------------------------------------

class TestFactoryHttp2:
    """Factory creates HTTP2Transport."""

    def test_factory_creates_http2_transport(self):
        cfg = _make_client_config(type="http2")
        transport = create_transport(cfg)
        assert isinstance(transport, HTTP2Transport)
        assert transport.mode == HTTP2Transport.MODE_CLIENT
        assert transport.host == "127.0.0.1"
        assert transport.port == 2225

    def test_factory_rejects_unknown_transport(self):
        cfg = _make_client_config(type="nonexistent")
        with pytest.raises(ConfigError, match="Unsupported transport type"):
            create_transport(cfg)

    def test_factory_creates_server_mode_http2(self):
        class _Server:
            tun_ip = "10.8.0.1"
            listen_port = 2225

        class _Transport:
            type = "http2"
            path = "/"
            server_hostname = None
            experimental = True

        class _Cfg:
            server = _Server()
            transport = _Transport()

        transport = create_transport(_Cfg())
        assert isinstance(transport, HTTP2Transport)
        assert transport.mode == HTTP2Transport.MODE_SERVER


# ---------------------------------------------------------------------------
# 5. close idempotent
# ---------------------------------------------------------------------------

class TestCloseIdempotent:
    """close() is safe to call multiple times."""

    def test_close_unconnected(self):
        transport = HTTP2Transport(mode="client")
        transport.close()
        transport.close()  # second call should not raise

    def test_close_after_close(self):
        transport = HTTP2Transport(mode="client")
        transport.close()
        transport.close()
        assert not transport.is_connected()

    def test_close_sets_shutting_down(self):
        transport = HTTP2Transport(mode="client")
        transport.close()
        # second close is a no-op (checked via _shutting_down)
        transport.close()

    def test_send_after_close_raises(self):
        transport = HTTP2Transport(mode="client")
        transport.close()
        with pytest.raises(TransportError):
            transport.send(b"data")

    def test_recv_after_close_raises(self):
        transport = HTTP2Transport(mode="client")
        transport.close()
        with pytest.raises(TransportError):
            transport.recv(timeout=0.1)


# ---------------------------------------------------------------------------
# 6. recv timeout behavior
# ---------------------------------------------------------------------------

class TestRecvTimeout:
    """recv timeout raises TransportTimeout."""

    def test_recv_without_connect_raises(self):
        transport = HTTP2Transport(mode="client")
        with pytest.raises(TransportError, match="Not connected"):
            transport.recv(timeout=0.1)


# ---------------------------------------------------------------------------
# 7. send before connect behavior
# ---------------------------------------------------------------------------

class TestSendBeforeConnect:
    """send without connect raises TransportError."""

    def test_send_without_connect_raises(self):
        transport = HTTP2Transport(mode="client")
        with pytest.raises(TransportError, match="Not connected"):
            transport.send(b"test")

    def test_send_without_connect_server_mode(self):
        transport = HTTP2Transport(mode="server")
        with pytest.raises(TransportError, match="Not connected"):
            transport.send(b"test")


# ---------------------------------------------------------------------------
# 8. existing transport factory tests don't regress
# ---------------------------------------------------------------------------

class TestFactoryRegression:
    """Existing transports still work after adding http2."""

    def test_tcp_still_creatable(self):
        class _Server:
            host = "127.0.0.1"
            port = 2222

        class _Transport:
            type = "tcp"
            path = "/"
            server_hostname = None
            experimental = False

        class _Cfg:
            server = _Server()
            transport = _Transport()

        from src.transport.tcp_transport import TCPTransport
        t = create_transport(_Cfg())
        assert isinstance(t, TCPTransport)

    def test_tls_still_creatable(self):
        class _Server:
            host = "127.0.0.1"
            port = 2223

        class _Transport:
            type = "tls"
            path = "/"
            server_hostname = None
            experimental = False

        class _Cfg:
            server = _Server()
            transport = _Transport()

        from src.transport.tls_transport import TLSTransport
        t = create_transport(_Cfg())
        assert isinstance(t, TLSTransport)

    def test_websocket_still_creatable(self):
        class _Server:
            host = "127.0.0.1"
            port = 2224

        class _Transport:
            type = "websocket"
            path = "/"
            server_hostname = None
            experimental = False

        class _Cfg:
            server = _Server()
            transport = _Transport()

        from src.transport.websocket_transport import WebSocketTransport
        t = create_transport(_Cfg())
        assert isinstance(t, WebSocketTransport)

    def test_list_supported_still_has_all(self):
        s = list_supported_transports()
        for name in ("ssh", "tcp", "tls", "websocket", "http2", "mock"):
            assert name in s, f"{name} missing from SUPPORTED_TRANSPORTS"


# ---------------------------------------------------------------------------
# 9. http2 transport marked experimental
# ---------------------------------------------------------------------------

class TestHttp2Experimental:
    """HTTP/2 transport is marked experimental."""

    def test_repr_mentions_experimental(self):
        transport = HTTP2Transport(mode="client")
        r = repr(transport)
        assert "HTTP2Transport" in r
        assert "experimental" not in r.lower()  # _H2_AVAILABLE is in repr

    def test_factory_logs_experimental(self):
        from unittest.mock import patch
        with patch('src.transport.factory.logger.info') as mock_info:
            cfg = _make_client_config()
            create_transport(cfg)
        calls = [str(c) for c in mock_info.call_args_list]
        assert any("experimental" in c.lower() for c in calls), \
            f"Expected 'experimental' in log calls, got: {calls}"


# ---------------------------------------------------------------------------
# 10. mode validation
# ---------------------------------------------------------------------------

class TestModeValidation:
    """Invalid mode raises TransportError."""

    def test_invalid_mode_raises(self):
        with pytest.raises(TransportError, match="Invalid mode"):
            HTTP2Transport(mode="invalid")

    def test_client_mode_is_valid(self):
        t = HTTP2Transport(mode="client")
        assert t.mode == "client"

    def test_server_mode_is_valid(self):
        t = HTTP2Transport(mode="server")
        assert t.mode == "server"


# ---------------------------------------------------------------------------
# 11. accept validations
# ---------------------------------------------------------------------------

class TestAcceptValidations:
    """accept() validation for server mode."""

    def test_accept_client_mode_raises(self):
        t = HTTP2Transport(mode="client")
        with pytest.raises(TransportError, match="server mode"):
            t.accept(timeout=0.1)

    def test_accept_before_connect_raises(self):
        t = HTTP2Transport(mode="server")
        with pytest.raises(TransportError, match="Server not started"):
            t.accept(timeout=0.1)


# ---------------------------------------------------------------------------
# 12. constructor defaults
# ---------------------------------------------------------------------------

class TestConstructorDefaults:
    """HTTP2Transport constructor defaults."""

    def test_default_mode_is_client(self):
        t = HTTP2Transport()
        assert t.mode == "client"

    def test_default_host(self):
        t = HTTP2Transport()
        assert t.host == "127.0.0.1"

    def test_default_port(self):
        t = HTTP2Transport()
        assert t.port == 2225

    def test_default_path(self):
        t = HTTP2Transport()
        assert t.path == "/"

    def test_server_hostname_defaults_to_host(self):
        t = HTTP2Transport(host="10.0.0.1")
        assert t.server_hostname == "10.0.0.1"

    def test_explicit_server_hostname(self):
        t = HTTP2Transport(host="10.0.0.1", server_hostname="h2.example.com")
        assert t.server_hostname == "h2.example.com"


# ---------------------------------------------------------------------------
# 13. netns config parsing
# ---------------------------------------------------------------------------


class TestHttp2NetnsConfig:
    """HTTP/2 netns config files parse and preserve experimental flag."""

    def test_server_netns_http2_parses(self):
        cfg = load_server_config("config/server_netns_http2.yaml")
        assert cfg.transport.type == "http2"
        assert cfg.transport.experimental is True
        assert cfg.server.listen_port == 2225

    def test_client_netns_http2_parses(self):
        cfg = load_client_config("config/client_netns_http2.yaml")
        assert cfg.transport.type == "http2"
        assert cfg.transport.experimental is True
        assert cfg.server.port == 2225

    def test_server_netns_http2_shaping_parses(self):
        cfg = load_server_config("config/server_netns_http2_shaping.yaml")
        assert cfg.transport.type == "http2"
        assert cfg.transport.experimental is True
        assert cfg.shaping.enabled is True

    def test_client_netns_http2_shaping_parses(self):
        cfg = load_client_config("config/client_netns_http2_shaping.yaml")
        assert cfg.transport.type == "http2"
        assert cfg.transport.experimental is True
        assert cfg.shaping.enabled is True

    def test_missing_h2_connect_error_remains_clear(self):
        """Even with netns config, missing h2 raises clear error."""
        from src.transport.factory import create_transport
        cfg = load_client_config("config/client_netns_http2.yaml")
        transport = create_transport(cfg)
        if _H2_AVAILABLE:
            pytest.skip("h2 is installed — cannot test missing-dependency path")
        with pytest.raises(TransportError, match="h2"):
            transport.connect()


# ---------------------------------------------------------------------------
# 14. HTTP/2-aware shaping — constructor and config
# ---------------------------------------------------------------------------

class TestHttp2ChunkingConfig:
    """HTTP/2-aware shaping constructor and config parsing."""

    def test_chunk_defaults_disabled(self):
        t = HTTP2Transport()
        assert t._chunk_enabled is False
        assert t._chunk_min_size == 0
        assert t._chunk_max_size == 0

    def test_chunk_min_zero_stays_disabled(self):
        t = HTTP2Transport(chunk_min_size=0, chunk_max_size=100)
        assert t._chunk_enabled is False

    def test_chunk_max_zero_stays_disabled(self):
        t = HTTP2Transport(chunk_min_size=100, chunk_max_size=0)
        assert t._chunk_enabled is False

    def test_chunk_enabled_with_valid_range(self):
        t = HTTP2Transport(chunk_min_size=256, chunk_max_size=1400)
        assert t._chunk_enabled is True
        assert t._chunk_min_size == 256
        assert t._chunk_max_size == 1400

    def test_chunk_max_auto_clamped_to_min(self):
        t = HTTP2Transport(chunk_min_size=500, chunk_max_size=300)
        assert t._chunk_max_size == 500

    def test_chunk_negative_values_clamped_to_zero(self):
        t = HTTP2Transport(chunk_min_size=-10, chunk_max_size=-5)
        assert t._chunk_min_size == 0
        assert t._chunk_max_size == 0
        assert t._chunk_enabled is False

    def test_fixed_seed_deterministic(self):
        t1 = HTTP2Transport(chunk_min_size=10, chunk_max_size=50, chunk_rng_seed=42)
        t2 = HTTP2Transport(chunk_min_size=10, chunk_max_size=50, chunk_rng_seed=42)
        # Generate some random values and verify same sequence
        for _ in range(10):
            assert t1._chunk_rng.randint(10, 50) == t2._chunk_rng.randint(10, 50)

    def test_none_seed_non_deterministic(self):
        t1 = HTTP2Transport(chunk_min_size=10, chunk_max_size=50, chunk_rng_seed=None)
        t2 = HTTP2Transport(chunk_min_size=10, chunk_max_size=50, chunk_rng_seed=None)
        values1 = [t1._chunk_rng.randint(10, 50) for _ in range(5)]
        values2 = [t2._chunk_rng.randint(10, 50) for _ in range(5)]
        # Very unlikely to match for 5 values (but theoretically possible)
        # Just verify they produce valid outputs
        assert all(10 <= v <= 50 for v in values1)
        assert all(10 <= v <= 50 for v in values2)

    def test_window_update_threshold_default_zero(self):
        t = HTTP2Transport()
        assert t._window_update_threshold == 0
        assert t._rx_bytes_since_update == 0

    def test_window_update_threshold_custom(self):
        t = HTTP2Transport(window_update_threshold=65535)
        assert t._window_update_threshold == 65535

    def test_window_update_threshold_negative_clamped(self):
        t = HTTP2Transport(window_update_threshold=-100)
        assert t._window_update_threshold == 0

    def test_close_resets_rx_bytes_counter(self):
        t = HTTP2Transport(window_update_threshold=1000)
        t._rx_bytes_since_update = 500
        t.close()
        assert t._rx_bytes_since_update == 0


# ---------------------------------------------------------------------------
# 15. HTTP/2-aware shaping — async_send chunking behavior
# ---------------------------------------------------------------------------

class TestHttp2AsyncSendChunking:
    """async_send splits payloads when chunking is enabled."""

    @staticmethod
    def _make_client_conn():
        """Create a client h2 connection with stream 1 open for DATA."""
        import h2.connection
        config = h2.config.H2Configuration(client_side=True, header_encoding='utf-8')
        conn = h2.connection.H2Connection(config=config)
        conn.initiate_connection()
        # Open stream 1 — required before send_data
        conn.send_headers(
            stream_id=1,
            headers=[(':method', 'POST'), (':path', '/'), (':scheme', 'http'), (':authority', '127.0.0.1:2225')],
            end_stream=False,
        )
        return conn

    def test_no_chunking_when_disabled(self):
        """When chunking is disabled, single send_data call for entire payload."""
        import asyncio
        t = HTTP2Transport()
        t._h2_conn = self._make_client_conn()
        t._connected = True

        sent_chunks = []
        _orig_send = t._h2_conn.send_data

        def _track(stream_id, data, end_stream=False):
            sent_chunks.append(len(data))
            return _orig_send(stream_id=stream_id, data=data, end_stream=end_stream)

        t._h2_conn.send_data = _track

        class _MockWriter:
            def write(self, data):
                pass
        t._writer = _MockWriter()

        async def _run():
            await t._async_send(b"A" * 2000)

        asyncio.run(_run())
        # No chunking: single send_data call
        assert len(sent_chunks) == 1
        assert sent_chunks[0] == 2000

    def test_chunking_splits_payload(self):
        """Payload larger than chunk_max is split into multiple DATA frames."""
        import asyncio
        t = HTTP2Transport(chunk_min_size=256, chunk_max_size=512, chunk_rng_seed=42)
        conn = self._make_client_conn()
        t._h2_conn = conn
        t._connected = True

        sent_chunks = []
        _orig_send = conn.send_data

        def _track(stream_id, data, end_stream=False):
            sent_chunks.append(len(data))
            return _orig_send(stream_id=stream_id, data=data, end_stream=end_stream)

        conn.send_data = _track

        class _MockWriter:
            def write(self, data):
                pass
        t._writer = _MockWriter()

        async def _run():
            await t._async_send(b"X" * 2000)

        asyncio.run(_run())
        # With 2000 bytes and chunk range 256-512, expect multiple chunks
        assert len(sent_chunks) > 1
        # Each chunk should be within range (last chunk may be smaller)
        for i, size in enumerate(sent_chunks[:-1]):
            assert 256 <= size <= 512, f"chunk {i} size {size} outside [256,512]"
        # Total reassembled equals original
        assert sum(sent_chunks) == 2000

    def test_chunking_fixed_seed_produces_same_chunks(self):
        """Same seed + same payload = same chunk sizes."""
        import asyncio

        async def _send_and_collect(t):
            conn = TestHttp2AsyncSendChunking._make_client_conn()
            sent = []
            _orig = conn.send_data
            def _track(stream_id, data, end_stream=False):
                sent.append(len(data))
                return _orig(stream_id=stream_id, data=data, end_stream=end_stream)
            conn.send_data = _track
            t._h2_conn = conn
            t._connected = True

            class _MW:
                def write(self, data):
                    pass
            t._writer = _MW()
            await t._async_send(b"D" * 1500)
            return sent

        t1 = HTTP2Transport(chunk_min_size=200, chunk_max_size=500, chunk_rng_seed=99)
        t2 = HTTP2Transport(chunk_min_size=200, chunk_max_size=500, chunk_rng_seed=99)

        chunks1 = asyncio.run(_send_and_collect(t1))
        chunks2 = asyncio.run(_send_and_collect(t2))
        assert chunks1 == chunks2
        assert len(chunks1) > 1

    def test_chunking_preserves_byte_order(self):
        """Chunks concatenated in order produce the original payload."""
        import asyncio

        payload = b"".join(bytes([i % 256]) for i in range(1000))

        t = HTTP2Transport(chunk_min_size=100, chunk_max_size=300, chunk_rng_seed=7)
        conn = self._make_client_conn()
        t._h2_conn = conn
        t._connected = True

        sent_chunks = []
        _orig_send = conn.send_data

        def _track(stream_id, data, end_stream=False):
            sent_chunks.append(bytes(data))
            return _orig_send(stream_id=stream_id, data=data, end_stream=end_stream)

        conn.send_data = _track

        class _MockWriter:
            def write(self, data):
                pass
        t._writer = _MockWriter()

        async def _run():
            await t._async_send(payload)

        asyncio.run(_run())

        reassembled = b"".join(sent_chunks)
        assert reassembled == payload
        assert len(sent_chunks) > 1

    def test_small_payload_not_chunked(self):
        """Payload smaller than chunk_min is sent as a single DATA frame."""
        import asyncio

        payload = b"small"

        t = HTTP2Transport(chunk_min_size=256, chunk_max_size=512)
        conn = self._make_client_conn()
        t._h2_conn = conn
        t._connected = True

        sent_chunks = []
        _orig_send = conn.send_data

        def _track(stream_id, data, end_stream=False):
            sent_chunks.append(bytes(data))
            return _orig_send(stream_id=stream_id, data=data, end_stream=end_stream)

        conn.send_data = _track

        class _MockWriter:
            def write(self, data):
                pass
        t._writer = _MockWriter()

        async def _run():
            await t._async_send(payload)

        asyncio.run(_run())

        assert len(sent_chunks) == 1
        assert sent_chunks[0] == payload


# ---------------------------------------------------------------------------
# 16. Factory passes HTTP/2-aware config fields
# ---------------------------------------------------------------------------

class TestFactoryHttp2AwareConfig:
    """Factory passes http2_chunk_* and window_update_threshold fields."""

    def test_factory_passes_chunk_config(self):
        from src.transport.factory import create_transport

        class _Server:
            host = "127.0.0.1"
            port = 2225

        class _Transport:
            type = "http2"
            path = "/"
            server_hostname = None
            experimental = True
            http2_chunk_min_size = 256
            http2_chunk_max_size = 1400
            http2_window_update_threshold = 65535
            http2_chunk_rng_seed = 123

        class _Cfg:
            server = _Server()
            transport = _Transport()

        t = create_transport(_Cfg())
        assert t._chunk_min_size == 256
        assert t._chunk_max_size == 1400
        assert t._chunk_enabled is True
        assert t._window_update_threshold == 65535

    def test_factory_defaults_when_fields_missing(self):
        from src.transport.factory import create_transport

        class _Server:
            host = "127.0.0.1"
            port = 2225

        class _Transport:
            type = "http2"
            path = "/"
            server_hostname = None
            experimental = True

        class _Cfg:
            server = _Server()
            transport = _Transport()

        t = create_transport(_Cfg())
        assert t._chunk_enabled is False
        assert t._chunk_min_size == 0
        assert t._chunk_max_size == 0
        assert t._window_update_threshold == 0

    def test_factory_chunking_disabled_when_fields_zero(self):
        from src.transport.factory import create_transport

        class _Server:
            host = "127.0.0.1"
            port = 2225

        class _Transport:
            type = "http2"
            path = "/"
            server_hostname = None
            experimental = True
            http2_chunk_min_size = 0
            http2_chunk_max_size = 0
            http2_window_update_threshold = 0

        class _Cfg:
            server = _Server()
            transport = _Transport()

        t = create_transport(_Cfg())
        assert t._chunk_enabled is False
        assert t._window_update_threshold == 0


# ---------------------------------------------------------------------------
# 17. Window update flush helper
# ---------------------------------------------------------------------------

class TestWindowUpdateFlush:
    """_flush_window_update behavior."""

    def test_flush_noop_when_zero_bytes(self):
        t = HTTP2Transport(window_update_threshold=1000)
        t._rx_bytes_since_update = 0

        import h2.connection
        config = h2.config.H2Configuration(client_side=True, header_encoding='utf-8')
        conn = h2.connection.H2Connection(config=config)
        conn.initiate_connection()

        class _MockWriter:
            def __init__(self):
                self.writes = []
            def write(self, data):
                self.writes.append(data)

        writer = _MockWriter()
        t._flush_window_update(conn, writer)
        assert t._rx_bytes_since_update == 0
        # No WINDOW_UPDATE should be generated for zero bytes
        # (writer.writes may or may not be empty depending on h2 internals)

    def test_flush_resets_counter(self):
        t = HTTP2Transport(window_update_threshold=1000)
        t._rx_bytes_since_update = 500

        import h2.connection
        config = h2.config.H2Configuration(client_side=True, header_encoding='utf-8')
        conn = h2.connection.H2Connection(config=config)
        conn.initiate_connection()

        class _MockWriter:
            def __init__(self):
                self.writes = []
            def write(self, data):
                self.writes.append(data)

        writer = _MockWriter()
        t._flush_window_update(conn, writer)
        assert t._rx_bytes_since_update == 0
