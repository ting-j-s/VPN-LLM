"""Tests for Transport module with mocks."""

import pytest
from unittest.mock import Mock, MagicMock, patch
from src.common.frame import Frame, FRAME_TYPE_DATA, FRAME_TYPE_HELLO, FRAME_TYPE_HELLO_ACK, FRAME_TYPE_KEEPALIVE
from src.transport.base import BaseTransport
from src.common.errors import TransportError


class MockTransport(BaseTransport):
    """Mock transport implementation for testing."""

    def __init__(self):
        self._connected = False
        self._sent_frames: list[Frame] = []
        self._recv_queue: list[Frame] = []
        self.connect_called = False
        self.disconnect_called = False

    def connect(self) -> None:
        self.connect_called = True
        self._connected = True

    def disconnect(self) -> None:
        self.disconnect_called = True
        self._connected = False

    def send_frame(self, frame: Frame) -> None:
        if not self._connected:
            raise TransportError("Not connected")
        self._sent_frames.append(frame)

    def recv_frame(self, timeout=None):
        if not self._connected:
            raise TransportError("Not connected")
        if self._recv_queue:
            return self._recv_queue.pop(0)
        return None

    def is_connected(self) -> bool:
        return self._connected

    # Test helper methods
    def queue_frame(self, frame: Frame) -> None:
        """Queue a frame for recv_frame to return."""
        self._recv_queue.append(frame)

    def get_sent_frames(self) -> list[Frame]:
        """Get all frames that were sent."""
        return list(self._sent_frames)


class TestMockTransport:
    """Test mock transport implementation."""

    def test_connect(self):
        """Test connect method."""
        transport = MockTransport()
        assert not transport.connect_called
        assert not transport.is_connected()

        transport.connect()
        assert transport.connect_called
        assert transport.is_connected()

    def test_disconnect(self):
        """Test disconnect method."""
        transport = MockTransport()
        transport.connect()
        assert transport.is_connected()

        transport.disconnect()
        assert transport.disconnect_called
        assert not transport.is_connected()

    def test_send_frame(self):
        """Test sending frames."""
        transport = MockTransport()
        transport.connect()

        frame = Frame.create_hello_frame(session_id=1)
        transport.send_frame(frame)

        assert len(transport._sent_frames) == 1
        assert transport._sent_frames[0] == frame

    def test_send_frame_not_connected(self):
        """Test sending frame when not connected raises error."""
        transport = MockTransport()

        frame = Frame.create_hello_frame(session_id=1)
        with pytest.raises(TransportError, match="Not connected"):
            transport.send_frame(frame)

    def test_recv_frame(self):
        """Test receiving frames."""
        transport = MockTransport()
        transport.connect()

        frame = Frame.create_data_frame(session_id=1, payload=b"test")
        transport.queue_frame(frame)

        received = transport.recv_frame(timeout=1.0)
        assert received == frame

    def test_recv_frame_empty(self):
        """Test recv returns None when queue empty."""
        transport = MockTransport()
        transport.connect()

        received = transport.recv_frame(timeout=0.1)
        assert received is None

    def test_recv_frame_not_connected(self):
        """Test recv when not connected raises error."""
        transport = MockTransport()

        with pytest.raises(TransportError, match="Not connected"):
            transport.recv_frame()


class TestBaseTransportInterface:
    """Test that BaseTransport defines required interface."""

    def test_base_transport_is_abstract(self):
        """Test BaseTransport cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseTransport()

    def test_required_methods_exist(self):
        """Test all required methods exist in interface."""
        required = ['connect', 'disconnect', 'send_frame', 'recv_frame', 'is_connected']
        for method in required:
            assert hasattr(BaseTransport, method)


class TestFrameRoundtrip:
    """Test frame serialization with transport."""

    def test_frame_hello_roundtrip(self):
        """Test hello frame goes through mock transport."""
        transport = MockTransport()
        transport.connect()

        hello = Frame.create_hello_frame(session_id=42)
        transport.send_frame(hello)

        sent = transport.get_sent_frames()
        assert len(sent) == 1
        assert sent[0].frame_type == FRAME_TYPE_HELLO
        assert sent[0].session_id == 42
        assert sent[0].payload == b"HELLO"

    def test_frame_data_roundtrip(self):
        """Test data frame roundtrip."""
        transport = MockTransport()
        transport.connect()

        payload = b"\x00\x01\x02\x03\x04\x05"
        frame = Frame.create_data_frame(session_id=1, payload=payload)
        transport.send_frame(frame)

        # Simulate server returning ack
        ack = Frame.create_hello_ack_frame(session_id=1)
        transport.queue_frame(ack)

        received = transport.recv_frame(timeout=1.0)
        assert received is not None
        assert received.frame_type == FRAME_TYPE_HELLO_ACK

    def test_multiple_frames(self):
        """Test sending and receiving multiple frames."""
        transport = MockTransport()
        transport.connect()

        for i in range(5):
            frame = Frame.create_data_frame(session_id=1, payload=f"packet{i}".encode())
            transport.send_frame(frame)

        assert len(transport.get_sent_frames()) == 5

        # Queue responses
        for _ in range(5):
            transport.queue_frame(Frame.create_keepalive_frame(session_id=1))

        for _ in range(5):
            received = transport.recv_frame(timeout=1.0)
            assert received is not None
            assert received.frame_type == FRAME_TYPE_KEEPALIVE
