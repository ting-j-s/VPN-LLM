"""Tests for SOCKS5 transport runtime implementation."""

import threading
import time

import pytest

from src.common.errors import TransportError, TransportTimeout
from src.transport.socks5_transport import Socks5Transport
from src.transport.factory import create_transport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def free_port():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class MockSocks5Config:
    """Minimal config object for transport factory testing."""

    class transport:
        type = "socks5"

    class server:
        host = "127.0.0.1"
        port = free_port()


# ---------------------------------------------------------------------------
# Basic construction
# ---------------------------------------------------------------------------

def test_socks5_transport_creation():
    """Test that a Socks5Transport can be constructed."""
    t = Socks5Transport()
    assert t is not None
    assert not t.is_connected()
    assert "Socks5Transport(mode=client" in repr(t)


def test_socks5_transport_connect_not_connected():
    """send/recv before connect raises TransportError."""
    t = Socks5Transport()
    with pytest.raises(TransportError, match="Not connected"):
        t.send(b"data")
    with pytest.raises(TransportError, match="Not connected"):
        t.recv()


def test_socks5_transport_close_idempotent():
    """close() can be called multiple times without error."""
    t = Socks5Transport()
    t.close()
    t.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_factory_creates_socks5_transport():
    """Factory returns a Socks5Transport for type 'socks5'."""
    config = MockSocks5Config()
    t = create_transport(config)
    assert isinstance(t, Socks5Transport)


# ---------------------------------------------------------------------------
# Round-trip (server + client, localhost)
# ---------------------------------------------------------------------------

def test_socks5_roundtrip():
    """
    End-to-end round-trip test using a local SOCKS5 server and client.
    """
    port = free_port()

    # --- server ---
    server = Socks5Transport(
        mode=Socks5Transport.MODE_SERVER,
        listen_host="127.0.0.1",
        listen_port=port,
    )
    server.connect()  # bind + listen

    accept_result = {"error": None}

    def do_accept():
        try:
            server.accept(timeout=5)
        except Exception as e:
            accept_result["error"] = e

    t_accept = threading.Thread(target=do_accept, daemon=True)
    t_accept.start()

    # --- client ---
    client = Socks5Transport(
        mode=Socks5Transport.MODE_CLIENT,
        proxy_host="127.0.0.1",
        proxy_port=port,
        target_host="127.0.0.1",
        target_port=0,  # any port is fine; server ignores
    )
    client.connect()

    t_accept.join(timeout=5)
    assert accept_result["error"] is None, f"server accept failed: {accept_result['error']}"

    # --- exchange data ---
    payload = b"hello SOCKS5 tunnel"
    client.send(payload)

    # server recv
    received = server.recv(timeout=5)
    assert received == payload

    # server reply
    reply = b"ack from server"
    server.send(reply)
    echoed = client.recv(timeout=5)
    assert echoed == reply

    client.close()
    server.close()


def test_socks5_client_send_recv_timeout():
    """recv() raises TransportTimeout when no data is available."""
    port = free_port()
    server = Socks5Transport(
        mode=Socks5Transport.MODE_SERVER,
        listen_host="127.0.0.1",
        listen_port=port,
    )
    server.connect()

    accept_done = threading.Event()

    def accept_and_hold():
        server.accept(timeout=5)
        accept_done.set()
        # hold connection open but send nothing

    t = threading.Thread(target=accept_and_hold, daemon=True)
    t.start()

    client = Socks5Transport(
        mode=Socks5Transport.MODE_CLIENT,
        proxy_host="127.0.0.1",
        proxy_port=port,
        target_host="127.0.0.1",
        target_port=0,
    )
    client.connect()
    assert accept_done.wait(timeout=5)

    # recv with short timeout should time out
    with pytest.raises(TransportTimeout):
        client.recv(timeout=0.1)

    client.close()
    server.close()


def test_socks5_client_connect_failure():
    """Client raises TransportError when proxy is unreachable."""
    # use a port that nothing listens on
    dead_port = free_port()
    client = Socks5Transport(
        proxy_host="127.0.0.1",
        proxy_port=dead_port,
        target_host="127.0.0.1",
        target_port=0,
    )
    with pytest.raises(TransportError):
        client.connect()
