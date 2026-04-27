"""Tests for Frame module."""

import pytest
from src.common.frame import (
    Frame,
    FRAME_HEADER_SIZE,
    FRAME_TYPE_DATA,
    FRAME_TYPE_HELLO,
    FRAME_TYPE_HELLO_ACK,
    FRAME_TYPE_KEEPALIVE,
    FRAME_TYPE_DISCONNECT,
    FrameError,
)


class TestFrameCreation:
    """Test frame creation methods."""

    def test_create_data_frame(self):
        """Test creating a data frame."""
        payload = b"\x00\x01\x02\x03"
        frame = Frame.create_data_frame(session_id=42, payload=payload)

        assert frame.frame_type == FRAME_TYPE_DATA
        assert frame.session_id == 42
        assert frame.payload == payload

    def test_create_hello_frame(self):
        """Test creating a hello frame."""
        frame = Frame.create_hello_frame(session_id=1)

        assert frame.frame_type == FRAME_TYPE_HELLO
        assert frame.session_id == 1
        assert frame.payload == b"HELLO"

    def test_create_hello_ack_frame(self):
        """Test creating a hello ack frame."""
        frame = Frame.create_hello_ack_frame(session_id=1)

        assert frame.frame_type == FRAME_TYPE_HELLO_ACK
        assert frame.session_id == 1
        assert frame.payload == b"ACK"

    def test_create_keepalive_frame(self):
        """Test creating a keepalive frame."""
        frame = Frame.create_keepalive_frame(session_id=1)

        assert frame.frame_type == FRAME_TYPE_KEEPALIVE
        assert frame.session_id == 1
        assert frame.payload == b""

    def test_create_disconnect_frame(self):
        """Test creating a disconnect frame."""
        frame = Frame.create_disconnect_frame(session_id=1)

        assert frame.frame_type == FRAME_TYPE_DISCONNECT
        assert frame.session_id == 1
        assert frame.payload == b""


class TestFrameSerialization:
    """Test frame serialization and deserialization."""

    def test_data_frame_roundtrip(self):
        """Test data frame serialize/deserialize roundtrip."""
        payload = b"\x00\x01\x02\x03\x04\x05\x06\x07"
        frame = Frame.create_data_frame(session_id=123, payload=payload)

        data = frame.to_bytes()
        assert len(data) == FRAME_HEADER_SIZE + len(payload)

        decoded = Frame.from_bytes(data)
        assert decoded.frame_type == frame.frame_type
        assert decoded.session_id == frame.session_id
        assert decoded.payload == frame.payload

    def test_empty_payload_roundtrip(self):
        """Test frame with empty payload roundtrip."""
        frame = Frame.create_keepalive_frame(session_id=456)

        data = frame.to_bytes()
        decoded = Frame.from_bytes(data)

        assert decoded.frame_type == frame.frame_type
        assert decoded.session_id == frame.session_id
        assert decoded.payload == b""

    def test_large_payload_roundtrip(self):
        """Test frame with large payload roundtrip."""
        payload = b"\xff" * 10000
        frame = Frame.create_data_frame(session_id=1, payload=payload)

        data = frame.to_bytes()
        decoded = Frame.from_bytes(data)

        assert decoded.payload == payload
        assert len(decoded.payload) == 10000

    def test_invalid_magic_bytes(self):
        """Test deserialization fails with invalid magic."""
        data = b"INVALID" + b"\x00" * (FRAME_HEADER_SIZE - 8) + b"test"

        with pytest.raises(FrameError, match="Invalid magic"):
            Frame.from_bytes(data)

    def test_truncated_frame(self):
        """Test deserialization fails with truncated frame."""
        data = b"\x00" * 8  # Too short

        with pytest.raises(FrameError, match="Frame too short"):
            Frame.from_bytes(data)

    def test_truncated_payload(self):
        """Test deserialization fails when payload is truncated."""
        # Create valid header but short payload
        frame = Frame.create_data_frame(session_id=1, payload=b"test")
        data = frame.to_bytes()
        # Truncate last 2 bytes
        short_data = data[:-2]

        with pytest.raises(FrameError, match="Payload truncated"):
            Frame.from_bytes(short_data)


class TestFrameRepr:
    """Test frame string representation."""

    def test_repr_format(self):
        """Test repr output format."""
        frame = Frame.create_data_frame(session_id=42, payload=b"\x01\x02")
        r = repr(frame)

        assert "Frame" in r
        assert "session_id=42" in r
        assert "payload_len=2" in r
