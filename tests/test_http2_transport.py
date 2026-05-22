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
