"""Tests for SOCKS5 transport skeleton."""

import pytest

from src.common.errors import TransportError
from src.transport.socks5_transport import Socks5Transport
from src.transport.factory import create_transport


class MockSocks5Config:
    """Minimal config object for transport factory testing."""

    class transport:
        type = "socks5"


def test_socks5_transport_creation():
    """Test that the skeleton can be constructed."""
    t = Socks5Transport()
    assert t is not None
    assert not t.is_connected()
    assert repr(t) == "Socks5Transport(skeleton)"


def test_socks5_transport_connect_raises_transport_error():
    """Test that connect() raises TransportError."""
    t = Socks5Transport()
    with pytest.raises(TransportError, match="not fully implemented yet"):

        t.connect()


def test_socks5_transport_send_raises_transport_error():
    """Test that send() raises TransportError."""
    t = Socks5Transport()
    with pytest.raises(TransportError, match="not fully implemented yet"):

        t.send(b"data")


def test_socks5_transport_recv_raises_transport_error():
    """Test that recv() raises TransportError."""
    t = Socks5Transport()
    with pytest.raises(TransportError, match="not fully implemented yet"):

        t.recv()


def test_socks5_transport_close_no_error():
    """Test that close() does not raise."""
    t = Socks5Transport()
    t.close()  # should not raise


def test_factory_creates_socks5_transport():
    """Test that the factory returns a Socks5Transport for type 'socks5'."""
    config = MockSocks5Config()
    t = create_transport(config)
    assert isinstance(t, Socks5Transport)
