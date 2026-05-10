"""Tests for WebSocket Transport module."""

import pytest
import socket
import threading
import time

from src.transport.websocket_transport import WebSocketTransport
from src.common.errors import TransportError, TransportTimeout


def _get_free_port():
    """Get a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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


class TestWebSocketTransportRoundtrip:
    """Real client/server communication tests over localhost loopback."""

    def test_client_connect_to_server(self):
        """Client connects to server successfully."""
        port = _get_free_port()

        server = WebSocketTransport(
            mode="server", host="127.0.0.1", port=port,
        )
        client = WebSocketTransport(
            mode="client", host="127.0.0.1", port=port,
        )

        try:
            server.connect()
            client.connect()
            server.accept(timeout=5.0)

            assert server.is_connected()
            assert client.is_connected()
        finally:
            client.close()
            server.close()

    def test_client_to_server_data(self):
        """client.send(b'hello') → server.recv() receives b'hello'."""
        port = _get_free_port()

        server = WebSocketTransport(
            mode="server", host="127.0.0.1", port=port,
        )
        client = WebSocketTransport(
            mode="client", host="127.0.0.1", port=port,
        )

        try:
            server.connect()
            client.connect()
            server.accept(timeout=5.0)

            client.send(b"hello")
            received = server.recv(timeout=2.0)
            assert received == b"hello"
        finally:
            client.close()
            server.close()

    def test_server_to_client_data(self):
        """server.send(b'world') → client.recv() receives b'world'."""
        port = _get_free_port()

        server = WebSocketTransport(
            mode="server", host="127.0.0.1", port=port,
        )
        client = WebSocketTransport(
            mode="client", host="127.0.0.1", port=port,
        )

        try:
            server.connect()
            client.connect()
            server.accept(timeout=5.0)

            server.send(b"world")
            received = client.recv(timeout=2.0)
            assert received == b"world"
        finally:
            client.close()
            server.close()

    def test_multiple_messages_both_directions(self):
        """Multiple messages in both directions work correctly."""
        port = _get_free_port()

        server = WebSocketTransport(
            mode="server", host="127.0.0.1", port=port,
        )
        client = WebSocketTransport(
            mode="client", host="127.0.0.1", port=port,
        )

        try:
            server.connect()
            client.connect()
            server.accept(timeout=5.0)

            for i in range(3):
                client.send(f"ping{i}".encode())
                reply = server.recv(timeout=2.0)
                assert reply == f"ping{i}".encode()
                server.send(f"pong{i}".encode())
                reply = client.recv(timeout=2.0)
                assert reply == f"pong{i}".encode()
        finally:
            client.close()
            server.close()

    def test_client_to_server_multiple_messages_preserve_order(self):
        """Client sends multiple messages sequentially; server receives them in the same order."""
        port = _get_free_port()

        server = WebSocketTransport(
            mode="server", host="127.0.0.1", port=port,
        )
        client = WebSocketTransport(
            mode="client", host="127.0.0.1", port=port,
        )

        try:
            server.connect()
            client.connect()
            server.accept(timeout=5.0)

            messages = [f"msg-{i}".encode() for i in range(5)]
            for msg in messages:
                client.send(msg)

            received = []
            for _ in range(len(messages)):
                received.append(server.recv(timeout=2.0))

            assert received == messages
        finally:
            client.close()
            server.close()

    def test_recv_timeout_raises_transport_timeout(self):
        """recv with short timeout raises TransportTimeout when no data."""
        port = _get_free_port()

        server = WebSocketTransport(
            mode="server", host="127.0.0.1", port=port,
        )
        client = WebSocketTransport(
            mode="client", host="127.0.0.1", port=port,
        )

        try:
            server.connect()
            client.connect()
            server.accept(timeout=5.0)

            # Send one message so we know the connection is working
            client.send(b"hello")
            received = server.recv(timeout=2.0)
            assert received == b"hello"

            # Now recv with short timeout — should raise TransportTimeout
            with pytest.raises(TransportTimeout):
                server.recv(timeout=0.1)

            # Connection should still be alive after timeout
            assert server.is_connected()
            assert client.is_connected()
        finally:
            client.close()
            server.close()

    def test_close_idempotent(self):
        """close() called multiple times should not raise errors."""
        port = _get_free_port()

        server = WebSocketTransport(
            mode="server", host="127.0.0.1", port=port,
        )
        client = WebSocketTransport(
            mode="client", host="127.0.0.1", port=port,
        )

        server.connect()
        client.connect()
        server.accept(timeout=5.0)

        client.close()
        client.close()  # second close should be no-op
        server.close()
        server.close()  # second close should be no-op

        assert not client.is_connected()
        assert not server.is_connected()

    def test_client_close_signals_server_recv(self):
        """When client closes, server recv() returns None (connection closed)."""
        port = _get_free_port()

        server = WebSocketTransport(
            mode="server", host="127.0.0.1", port=port,
        )
        client = WebSocketTransport(
            mode="client", host="127.0.0.1", port=port,
        )

        try:
            server.connect()
            client.connect()
            server.accept(timeout=5.0)

            assert server.is_connected()
            assert client.is_connected()

            client.close()

            # Server recv should get None (connection closed)
            result = server.recv(timeout=2.0)
            assert result is None
        finally:
            server.close()

    def test_server_accept_timeout(self):
        """accept() with timeout raises TransportError when no client connects."""
        port = _get_free_port()

        server = WebSocketTransport(
            mode="server", host="127.0.0.1", port=port,
        )
        server.connect()

        try:
            with pytest.raises(TransportError, match="Accept timeout"):
                server.accept(timeout=0.1)
        finally:
            server.close()

    def test_background_threads_exit_after_close(self):
        """After close(), background threads should not linger."""
        port = _get_free_port()

        server = WebSocketTransport(
            mode="server", host="127.0.0.1", port=port,
        )
        client = WebSocketTransport(
            mode="client", host="127.0.0.1", port=port,
        )

        server.connect()
        client.connect()
        server.accept(timeout=5.0)

        client.close()
        server.close()

        # Allow brief time for threads to finish
        time.sleep(0.1)

        # After close, the loop thread should have stopped
        assert server._loop_thread is None or not server._loop_thread.is_alive()
        assert client._loop_thread is None or not client._loop_thread.is_alive()
