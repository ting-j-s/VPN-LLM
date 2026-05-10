"""Tests for Transport module."""

import pytest
import uuid
from src.common.frame import Frame, FrameType, create_frame, encode_frame, decode_frame
from src.transport.base import Transport, MockTransport
from src.common.errors import TransportError


class TestMockTransport:
    """Test MockTransport implementation."""

    def test_connect(self):
        """Test connect method."""
        transport = MockTransport()
        assert not transport.is_connected()

        transport.connect()
        assert transport.is_connected()

    def test_disconnect(self):
        """Test disconnect method."""
        transport = MockTransport()
        transport.connect()
        assert transport.is_connected()

        transport.close()
        assert not transport.is_connected()

    def test_send_and_recv(self):
        """Test basic send and recv."""
        transport = MockTransport()
        transport.connect()

        data = b"\x00\x01\x02\x03"
        transport.send(data)

        received = transport.recv(timeout=0.1)
        assert received is None  # Queue is empty

        transport.inject(data)
        received = transport.recv(timeout=0.1)
        assert received == data

    def test_send_not_connected(self):
        """Test send raises error when not connected."""
        transport = MockTransport()

        with pytest.raises(TransportError, match="Not connected"):
            transport.send(b"\x00\x01")

    def test_recv_not_connected(self):
        """Test recv raises error when not connected."""
        transport = MockTransport()

        with pytest.raises(TransportError, match="Not connected"):
            transport.recv(timeout=0.1)

    def test_get_sent(self):
        """Test get_sent returns all sent data."""
        transport = MockTransport()
        transport.connect()

        transport.send(b"packet1")
        transport.send(b"packet2")

        sent = transport.get_sent()
        assert len(sent) == 2
        assert sent[0] == b"packet1"
        assert sent[1] == b"packet2"

    def test_inject_multiple(self):
        """Test injecting multiple frames."""
        transport = MockTransport()
        transport.connect()

        transport.inject(b"frame1")
        transport.inject(b"frame2")
        transport.inject(b"frame3")

        assert transport.recv(timeout=0.1) == b"frame1"
        assert transport.recv(timeout=0.1) == b"frame2"
        assert transport.recv(timeout=0.1) == b"frame3"
        assert transport.recv(timeout=0.1) is None

    def test_set_send_error(self):
        """Test send error simulation."""
        transport = MockTransport()
        transport.connect()

        transport.set_send_error(TransportError("mock send error"))

        with pytest.raises(TransportError, match="mock send error"):
            transport.send(b"data")

    def test_set_recv_error(self):
        """Test recv error simulation."""
        transport = MockTransport()
        transport.connect()

        transport.set_recv_error(TransportError("mock recv error"))

        with pytest.raises(TransportError, match="mock recv error"):
            transport.recv(timeout=0.1)


class TestTransportInterface:
    """Test that Transport defines required interface."""

    def test_transport_is_abstract(self):
        """Test Transport cannot be instantiated directly."""
        with pytest.raises(TypeError):
            Transport()

    def test_required_methods_exist(self):
        """Test all required methods exist in interface."""
        required = ['connect', 'send', 'recv', 'close', 'is_connected']
        for method in required:
            assert hasattr(Transport, method)


class TestFrameRoundtripWithMockTransport:
    """Test frame encode/decode with MockTransport."""

    def test_encode_decode_frame_roundtrip(self):
        """Test that frames can be encoded, sent, received, and decoded."""
        transport = MockTransport()
        transport.connect()

        session_id = uuid.uuid4().bytes
        frame = create_frame(FrameType.DATA, session_id, b"test payload")

        # Encode and send
        data = encode_frame(frame)
        transport.send(data)

        # Inject encoded data and receive
        transport.inject(data)
        received_data = transport.recv(timeout=0.1)

        assert received_data == data

        # Decode
        decoded = decode_frame(received_data)
        assert decoded.frame_type == FrameType.DATA
        assert decoded.session_id == session_id
        assert decoded.payload == b"test payload"

    def test_multiple_frame_roundtrip(self):
        """Test multiple frames through transport."""
        transport = MockTransport()
        transport.connect()

        session_id = uuid.uuid4().bytes

        for i in range(5):
            frame = create_frame(FrameType.DATA, session_id, f"packet{i}".encode())
            data = encode_frame(frame)
            transport.send(data)

        assert len(transport.get_sent()) == 5
