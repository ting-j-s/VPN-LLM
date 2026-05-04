"""Tests for Frame module."""

import struct
import pytest
import uuid

from src.common.frame import (
    Frame,
    FrameType,
    FrameDecodeError,
    encode_frame,
    decode_frame,
    MAGIC,
    VERSION,
    HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
)


class TestFrameType:
    """Test FrameType enumeration."""

    def test_frame_type_values(self):
        """Test frame type values."""
        assert FrameType.DATA == 0x01
        assert FrameType.HEARTBEAT == 0x02
        assert FrameType.AUTH == 0x03
        assert FrameType.CLOSE == 0x04


class TestFrameCreation:
    """Test Frame dataclass creation."""

    def test_create_data_frame(self):
        """Test creating a DATA frame."""
        session_id = uuid.uuid4().bytes
        frame = Frame(frame_type=FrameType.DATA, session_id=session_id, payload=b"\x00\x01\x02\x03")

        assert frame.frame_type == FrameType.DATA
        assert frame.session_id == session_id
        assert frame.payload == b"\x00\x01\x02\x03"

    def test_create_heartbeat_frame(self):
        """Test creating a HEARTBEAT frame."""
        session_id = uuid.uuid4().bytes
        frame = Frame(frame_type=FrameType.HEARTBEAT, session_id=session_id, payload=b"")

        assert frame.frame_type == FrameType.HEARTBEAT
        assert frame.payload == b""

    def test_invalid_session_id_length(self):
        """Test that invalid session_id length raises error."""
        with pytest.raises(ValueError, match="session_id must be 16 bytes"):
            Frame(frame_type=FrameType.DATA, session_id=b"short", payload=b"")


class TestEncodeDecode:
    """Test encode and decode functions."""

    def test_data_frame_roundtrip(self):
        """Test DATA frame encode/decode roundtrip."""
        session_id = uuid.uuid4().bytes
        payload = b"\x00\x01\x02\x03\x04\x05\x06\x07"
        frame = Frame(frame_type=FrameType.DATA, session_id=session_id, payload=payload)

        data = encode_frame(frame)
        decoded = decode_frame(data)

        assert decoded.frame_type == frame.frame_type
        assert decoded.session_id == frame.session_id
        assert decoded.payload == frame.payload

    def test_empty_payload(self):
        """Test frame with empty payload."""
        session_id = uuid.uuid4().bytes
        frame = Frame(frame_type=FrameType.CLOSE, session_id=session_id, payload=b"")

        data = encode_frame(frame)
        decoded = decode_frame(data)

        assert decoded.frame_type == frame.frame_type
        assert decoded.session_id == frame.session_id
        assert decoded.payload == b""

    def test_large_payload_roundtrip(self):
        """Test frame with large payload roundtrip."""
        session_id = uuid.uuid4().bytes
        payload = b"\xff" * 10000
        frame = Frame(frame_type=FrameType.DATA, session_id=session_id, payload=payload)

        data = encode_frame(frame)
        decoded = decode_frame(data)

        assert decoded.payload == payload
        assert len(decoded.payload) == 10000

    def test_auth_frame_roundtrip(self):
        """Test AUTH frame encode/decode."""
        session_id = uuid.uuid4().bytes
        payload = b"username:password"
        frame = Frame(frame_type=FrameType.AUTH, session_id=session_id, payload=payload)

        data = encode_frame(frame)
        decoded = decode_frame(data)

        assert decoded.frame_type == FrameType.AUTH
        assert decoded.payload == payload


class TestDecodeErrors:
    """Test decode error handling."""

    def test_invalid_magic(self):
        """Test that invalid magic bytes raises error."""
        session_id = uuid.uuid4().bytes
        frame = Frame(frame_type=FrameType.DATA, session_id=session_id, payload=b"test")
        data = encode_frame(frame)

        # Corrupt magic bytes
        corrupted = b"XXXX" + data[4:]

        with pytest.raises(FrameDecodeError, match="Invalid magic"):
            decode_frame(corrupted)

    def test_invalid_version(self):
        """Test that invalid version raises error."""
        session_id = uuid.uuid4().bytes
        frame = Frame(frame_type=FrameType.DATA, session_id=session_id, payload=b"test")
        data = encode_frame(frame)

        # Corrupt version byte
        corrupted = data[:4] + b"\x99" + data[5:]

        with pytest.raises(FrameDecodeError, match="Unsupported version"):
            decode_frame(corrupted)

    def test_length_mismatch(self):
        """Test that length mismatch raises error."""
        session_id = uuid.uuid4().bytes
        frame = Frame(frame_type=FrameType.DATA, session_id=session_id, payload=b"test")
        data = encode_frame(frame)

        # Truncate payload - now raises "Frame data truncated"
        truncated = data[:-2]

        with pytest.raises(FrameDecodeError, match="Frame data truncated"):
            decode_frame(truncated)

    def test_frame_too_short(self):
        """Test that truncated header raises error."""
        with pytest.raises(FrameDecodeError, match="Frame too short"):
            decode_frame(b"\x00\x01\x02")

    def test_unknown_frame_type(self):
        """Test that unknown frame type raises error."""
        session_id = uuid.uuid4().bytes
        frame = Frame(frame_type=FrameType.DATA, session_id=session_id, payload=b"test")
        data = encode_frame(frame)

        # Change frame type to unknown value
        corrupted = data[:5] + b"\xff" + data[6:]

        with pytest.raises(FrameDecodeError, match="Unknown frame type"):
            decode_frame(corrupted)

    def test_oversized_payload(self):
        """Test that oversized payload raises error."""
        session_id = uuid.uuid4().bytes
        # Craft a frame with length > MAX_PAYLOAD_SIZE
        from src.common.frame import MAX_PAYLOAD_SIZE
        oversized_length = MAX_PAYLOAD_SIZE + 1

        header = struct.pack(
            ">4s B B I 16s",
            MAGIC,
            VERSION,
            FrameType.DATA,
            oversized_length,
            session_id,
        )

        with pytest.raises(FrameDecodeError, match="Payload too large"):
            decode_frame(header + b"\x00" * 100)

    def test_trailing_data(self):
        """Test that trailing data after declared payload raises error."""
        session_id = uuid.uuid4().bytes
        frame = Frame(frame_type=FrameType.DATA, session_id=session_id, payload=b"test")
        data = encode_frame(frame)

        # Add trailing garbage bytes
        with_trailing = data + b"TRAILING"

        with pytest.raises(FrameDecodeError, match="trailing data"):
            decode_frame(with_trailing)


class TestConstants:
    """Test module constants."""

    def test_magic_value(self):
        """Test MAGIC constant."""
        assert MAGIC == b"VTUN"

    def test_version_value(self):
        """Test VERSION constant."""
        assert VERSION == 1

    def test_header_size(self):
        """Test HEADER_SIZE constant."""
        assert HEADER_SIZE == 26  # 4 + 1 + 1 + 4 + 16
