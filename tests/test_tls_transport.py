"""Tests for TLS Transport module."""

import pytest
import socket
import threading
import time

from src.transport.tls_transport import TLSTransport
from src.common.errors import TransportError, TransportTimeout


def _generate_test_certs(tmp_path):
    """Generate self-signed cert/key pair for testing."""
    from subprocess import Popen, PIPE

    cert_file = tmp_path / "test.crt"
    key_file = tmp_path / "test.key"

    key_cmd = ["openssl", "genrsa", "-out", str(key_file), "2048"]
    cert_cmd = [
        "openssl", "req", "-new", "-x509",
        "-key", str(key_file),
        "-out", str(cert_file),
        "-days", "365",
        "-subj", "/CN=localhost/O=Test/C=US"
    ]

    try:
        Popen(key_cmd, stdout=PIPE, stderr=PIPE).wait()
        Popen(cert_cmd, stdout=PIPE, stderr=PIPE).wait()
        return str(cert_file), str(key_file)
    except Exception:
        return None, None


class TestTLSTransportClient:
    """Test TLSTransport client mode."""

    def test_create_client_transport(self, tmp_path):
        """Test creating a client transport."""
        certs = _generate_test_certs(tmp_path)
        if certs is None:
            pytest.skip("openssl not available")

        cert, key = certs
        transport = TLSTransport(
            mode="client",
            host="127.0.0.1",
            port=9223,
            certfile=cert,
            keyfile=key,
        )
        assert transport.mode == "client"
        assert transport.host == "127.0.0.1"
        assert transport.port == 9223
        assert not transport.is_connected()

    def test_invalid_mode(self):
        """Test that invalid mode raises error."""
        with pytest.raises(TransportError, match="Invalid mode"):
            TLSTransport(mode="invalid")

    def test_client_without_cert(self):
        """Test client transport without cert works (anonymous TLS)."""
        transport = TLSTransport(
            mode="client",
            host="127.0.0.1",
            port=9223,
            verify_server=False,
        )
        assert transport.mode == "client"
        assert transport.verify_server is False


class TestTLSTransportServer:
    """Test TLSTransport server mode."""

    def test_create_server_transport(self, tmp_path):
        """Test creating a server transport."""
        certs = _generate_test_certs(tmp_path)
        if certs is None:
            pytest.skip("openssl not available")

        cert, key = certs
        transport = TLSTransport(
            mode="server",
            host="127.0.0.1",
            port=9222,
            certfile=cert,
            keyfile=key,
        )
        assert transport.mode == "server"
        assert transport.host == "127.0.0.1"
        assert transport.port == 9222
        assert not transport.is_connected()


class TestTLSTransportTimeout:
    """Test TLS transport timeout handling (mirrors TCPTransport behavior)."""

    def test_recv_timeout_raises_transport_timeout(self, tmp_path):
        """Test that connected recv with short timeout raises TransportTimeout."""
        certs = _generate_test_certs(tmp_path)
        if certs is None:
            pytest.skip("openssl not available")

        cert, key = certs
        port = 19182

        server_transport = TLSTransport(
            mode="server",
            host="127.0.0.1",
            port=port,
            certfile=cert,
            keyfile=key,
        )
        server_transport.connect()

        client_transport = TLSTransport(
            mode="client",
            host="127.0.0.1",
            port=port,
            verify_server=False,
        )

        # TLS connect does handshake, so client connect and server accept
        # must run concurrently (mirrors real-world usage with threads).
        client_connect_error = []

        def connect_client():
            try:
                client_transport.connect()
            except Exception as e:
                client_connect_error.append(e)

        client_thread = threading.Thread(target=connect_client)
        client_thread.start()

        try:
            server_transport.accept(timeout=5.0)
            client_thread.join(timeout=5.0)

            if client_connect_error:
                pytest.fail(f"Client connect failed: {client_connect_error[0]}")

            assert server_transport.is_connected()
            assert client_transport.is_connected()

            # Send data first so server has something to receive
            client_transport.send(b"Hello")
            received = server_transport.recv(timeout=2.0)
            assert received == b"Hello"

            # Now recv with very short timeout - should raise TransportTimeout
            with pytest.raises(TransportTimeout):
                server_transport.recv(timeout=0.1)

            # Transport should still be connected after timeout
            assert server_transport.is_connected()
            assert client_transport.is_connected()
        finally:
            client_transport.close()
            server_transport.close()

    def test_multiple_timeouts_dont_disconnect(self, tmp_path):
        """Test that multiple recv timeouts don't disconnect the transport."""
        certs = _generate_test_certs(tmp_path)
        if certs is None:
            pytest.skip("openssl not available")

        cert, key = certs
        port = 19181

        server_transport = TLSTransport(
            mode="server",
            host="127.0.0.1",
            port=port,
            certfile=cert,
            keyfile=key,
        )
        server_transport.connect()

        client_transport = TLSTransport(
            mode="client",
            host="127.0.0.1",
            port=port,
            verify_server=False,
        )

        client_connect_error = []

        def connect_client():
            try:
                client_transport.connect()
            except Exception as e:
                client_connect_error.append(e)

        client_thread = threading.Thread(target=connect_client)
        client_thread.start()

        try:
            server_transport.accept(timeout=5.0)
            client_thread.join(timeout=5.0)

            if client_connect_error:
                pytest.fail(f"Client connect failed: {client_connect_error[0]}")

            # Multiple timeouts should not disconnect
            for i in range(5):
                with pytest.raises(TransportTimeout):
                    server_transport.recv(timeout=0.1)
                assert server_transport.is_connected()

            # Send data after timeouts - should still work
            client_transport.send(b"After timeouts")
            received = server_transport.recv(timeout=2.0)
            assert received == b"After timeouts"
        finally:
            client_transport.close()
            server_transport.close()


class TestTLSTransportDisconnect:
    """Test handling of disconnection."""

    def test_send_without_connect(self):
        """Test that send without connect raises error."""
        transport = TLSTransport(
            mode="client",
            host="127.0.0.1",
            port=19195,
            verify_server=False,
        )

        with pytest.raises(TransportError, match="Not connected"):
            transport.send(b"test")

    def test_recv_without_connect(self):
        """Test that recv without connect raises error."""
        transport = TLSTransport(
            mode="client",
            host="127.0.0.1",
            port=19194,
            verify_server=False,
        )

        with pytest.raises(TransportError, match="Not connected"):
            transport.recv(timeout=1.0)

    def test_server_accept_timeout(self, tmp_path):
        """Test server accept timeout."""
        certs = _generate_test_certs(tmp_path)
        if certs is None:
            pytest.skip("openssl not available")

        cert, key = certs
        port = 19193
        server_transport = TLSTransport(
            mode="server",
            host="127.0.0.1",
            port=port,
            certfile=cert,
            keyfile=key,
        )
        server_transport.connect()

        with pytest.raises(TransportError, match="Accept timeout"):
            server_transport.accept(timeout=0.1)

        server_transport.close()