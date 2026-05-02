"""Tests for TCP Transport module."""

import pytest
import socket
import threading
import time
from src.transport.tcp_transport import TCPTransport
from src.common.errors import TransportError


class TestTCPTransportClient:
    """Test TCPTransport client mode."""

    def test_create_client_transport(self):
        """Test creating a client transport."""
        transport = TCPTransport(
            mode="client",
            host="127.0.0.1",
            port=9999,
        )
        assert transport.mode == "client"
        assert transport.host == "127.0.0.1"
        assert transport.port == 9999
        assert not transport.is_connected()

    def test_invalid_mode(self):
        """Test that invalid mode raises error."""
        with pytest.raises(TransportError, match="Invalid mode"):
            TCPTransport(mode="invalid")


class TestTCPTransportServer:
    """Test TCPTransport server mode."""

    def test_create_server_transport(self):
        """Test creating a server transport."""
        transport = TCPTransport(
            mode="server",
            host="127.0.0.1",
            port=9998,
        )
        assert transport.mode == "server"
        assert transport.host == "127.0.0.1"
        assert transport.port == 9998
        assert not transport.is_connected()


class TestTCPTransportRoundtrip:
    """Test TCP transport send/recv roundtrip."""

    def test_client_server_roundtrip(self):
        """Test data roundtrip between client and server."""
        # Use a free port
        port = 19999

        # Server transport
        server_transport = TCPTransport(mode="server", host="127.0.0.1", port=port)

        # Start server
        server_transport.connect()

        # Client transport
        client_transport = TCPTransport(mode="client", host="127.0.0.1", port=port)

        # Connect client
        client_transport.connect()

        # Server accepts connection
        server_transport.accept(timeout=5.0)

        assert client_transport.is_connected()
        assert server_transport.is_connected()

        # Client sends data
        test_data = b"Hello, TCP!"
        client_transport.send(test_data)

        # Server receives
        received = server_transport.recv(timeout=2.0)
        assert received == test_data

        # Server sends response
        response = b"Hello back!"
        server_transport.send(response)

        # Client receives
        received = client_transport.recv(timeout=2.0)
        assert received == response

        # Cleanup
        client_transport.close()
        server_transport.close()

        assert not client_transport.is_connected()
        assert not server_transport.is_connected()

    def test_multiple_frames(self):
        """Test sending multiple frames."""
        port = 19998

        server_transport = TCPTransport(mode="server", host="127.0.0.1", port=port)
        server_transport.connect()

        client_transport = TCPTransport(mode="client", host="127.0.0.1", port=port)
        client_transport.connect()

        server_transport.accept(timeout=5.0)

        # Send multiple frames
        for i in range(10):
            data = f"Frame {i}".encode()
            client_transport.send(data)
            received = server_transport.recv(timeout=2.0)
            assert received == data

        client_transport.close()
        server_transport.close()

    def test_large_payload(self):
        """Test sending large payload."""
        port = 19997

        server_transport = TCPTransport(mode="server", host="127.0.0.1", port=port)
        server_transport.connect()

        client_transport = TCPTransport(mode="client", host="127.0.0.1", port=port)
        client_transport.connect()

        server_transport.accept(timeout=5.0)

        # Large payload (100KB)
        large_data = b"X" * 100000
        client_transport.send(large_data)

        received = server_transport.recv(timeout=5.0)
        assert received == large_data
        assert len(received) == 100000

        client_transport.close()
        server_transport.close()

    def test_empty_payload(self):
        """Test sending empty payload."""
        port = 19996

        server_transport = TCPTransport(mode="server", host="127.0.0.1", port=port)
        server_transport.connect()

        client_transport = TCPTransport(mode="client", host="127.0.0.1", port=port)
        client_transport.connect()

        server_transport.accept(timeout=5.0)

        # Empty payload
        client_transport.send(b"")
        received = server_transport.recv(timeout=2.0)
        assert received == b""

        client_transport.close()
        server_transport.close()


class TestTCPTransportDisconnect:
    """Test handling of disconnection."""

    def test_send_without_connect(self):
        """Test that send without connect raises error."""
        transport = TCPTransport(mode="client", host="127.0.0.1", port=19995)

        with pytest.raises(TransportError, match="Not connected"):
            transport.send(b"test")

    def test_recv_without_connect(self):
        """Test that recv without connect raises error."""
        transport = TCPTransport(mode="client", host="127.0.0.1", port=19994)

        with pytest.raises(TransportError, match="Not connected"):
            transport.recv(timeout=1.0)

    def test_server_accept_timeout(self):
        """Test server accept timeout."""
        port = 19993
        server_transport = TCPTransport(mode="server", host="127.0.0.1", port=port)
        server_transport.connect()

        # Accept with very short timeout
        with pytest.raises(TransportError, match="Accept timeout"):
            server_transport.accept(timeout=0.1)

        server_transport.close()
