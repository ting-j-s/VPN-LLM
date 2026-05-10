"""Tests for WebSocket Transport module."""

import pytest
import threading
import time

from src.transport.websocket_transport import WebSocketTransport
from src.common.errors import TransportError


class TestWebSocketTransportClient:
    """Test WebSocketTransport client mode."""

    def test_create_client_transport(self):
        """Test creating a client transport."""
        transport = WebSocketTransport(
            mode="client",
            host="127.0.0.1",
            port=9224,
            path="/test",
        )
        assert transport.mode == "client"
        assert transport.host == "127.0.0.1"
        assert transport.port == 9224
        assert transport.path == "/test"
        assert not transport.is_connected()

    def test_invalid_mode(self):
        """Test that invalid mode raises error."""
        with pytest.raises(TransportError, match="Invalid mode"):
            WebSocketTransport(mode="invalid")

    def test_default_path(self):
        """Test that default path is /."""
        transport = WebSocketTransport(mode="client", host="127.0.0.1", port=9224)
        assert transport.path == "/"


class TestWebSocketTransportServer:
    """Test WebSocketTransport server mode."""

    def test_create_server_transport(self):
        """Test creating a server transport."""
        transport = WebSocketTransport(
            mode="server",
            host="127.0.0.1",
            port=9225,
            path="/vpn",
        )
        assert transport.mode == "server"
        assert transport.host == "127.0.0.1"
        assert transport.port == 9225
        assert transport.path == "/vpn"
        assert not transport.is_connected()


class TestWebSocketTransportDisconnect:
    """Test handling of disconnection."""

    def test_send_without_connect(self):
        """Test that send without connect raises error."""
        transport = WebSocketTransport(
            mode="client",
            host="127.0.0.1",
            port=19195,
        )

        with pytest.raises(TransportError, match="Not connected"):
            transport.send(b"test")

    def test_recv_without_connect(self):
        """Test that recv without connect raises error."""
        transport = WebSocketTransport(
            mode="client",
            host="127.0.0.1",
            port=19194,
        )

        with pytest.raises(TransportError, match="Not connected"):
            transport.recv(timeout=1.0)


class TestWebSocketTransportBasic:
    """Test basic transport operations."""

    def test_repr(self):
        """Test string representation."""
        transport = WebSocketTransport(
            mode="client",
            host="127.0.0.1",
            port=9224,
            path="/test",
        )
        r = repr(transport)
        assert "WebSocketTransport" in r
        assert "client" in r
        assert "127.0.0.1" in r
        assert "9224" in r
        assert "/test" in r