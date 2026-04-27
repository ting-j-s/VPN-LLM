"""VPN Tunnel Unified Frame Format.

Defines the standard frame format for encapsulating IP packets in the tunnel.
Format: Magic(4) + Version(1) + Type(1) + Length(4) + SessionID(16) + Payload

SessionID is stored as 16-byte UUID bytes (not string).
"""

import struct
import uuid
from dataclasses import dataclass
from enum import IntEnum

from .errors import TunnelError


# Frame constants
MAGIC = b"VTUN"
VERSION = 1
HEADER_SIZE = 26  # Magic(4) + Version(1) + Type(1) + Length(4) + SessionID(16)


class FrameType(IntEnum):
    """Frame type enumeration."""
    DATA = 0x01
    HEARTBEAT = 0x02
    AUTH = 0x03
    CLOSE = 0x04


class FrameDecodeError(TunnelError):
    """Raised when frame decoding fails."""
    pass


@dataclass
class Frame:
    """Unified frame structure for VPN tunnel.

    Attributes:
        frame_type: Frame type (0x01-0x04).
        session_id: 16-byte UUID bytes.
        payload: Frame payload (variable length).
    """
    frame_type: FrameType
    session_id: bytes
    payload: bytes = b""

    def __post_init__(self):
        """Validate session_id is 16 bytes."""
        if len(self.session_id) != 16:
            raise ValueError(f"session_id must be 16 bytes, got {len(self.session_id)}")

    def __repr__(self) -> str:
        return (
            f"Frame(type={self.frame_type.name}(0x{self.frame_type:02x}), "
            f"session_id={uuid.UUID(bytes=self.session_id).hex}, "
            f"payload_len={len(self.payload)})"
        )


def encode_frame(frame: Frame) -> bytes:
    """Encode a Frame object into bytes.

    Args:
        frame: Frame object to encode.

    Returns:
        Raw frame bytes (header + payload).
    """
    header = struct.pack(
        ">4s B B I 16s",
        MAGIC,
        VERSION,
        frame.frame_type,
        len(frame.payload),
        frame.session_id,
    )
    return header + frame.payload


def decode_frame(data: bytes) -> Frame:
    """Decode bytes into a Frame object.

    Args:
        data: Raw frame bytes.

    Returns:
        Decoded Frame object.

    Raises:
        FrameDecodeError: If frame format is invalid.
    """
    if len(data) < HEADER_SIZE:
        raise FrameDecodeError(f"Frame too short: {len(data)} < {HEADER_SIZE}")

    magic, version, frame_type, length, session_id = struct.unpack(
        ">4s B B I 16s", data[:HEADER_SIZE]
    )

    if magic != MAGIC:
        raise FrameDecodeError(f"Invalid magic: {magic!r}, expected {MAGIC!r}")

    if version != VERSION:
        raise FrameDecodeError(f"Unsupported version: {version}, expected {VERSION}")

    try:
        frame_type_enum = FrameType(frame_type)
    except ValueError:
        raise FrameDecodeError(f"Unknown frame type: 0x{frame_type:02x}")

    if len(session_id) != 16:
        raise FrameDecodeError(f"Invalid session_id length: {len(session_id)}")

    payload = data[HEADER_SIZE:HEADER_SIZE + length]

    if len(payload) != length:
        raise FrameDecodeError(
            f"Payload length mismatch: declared={length}, actual={len(payload)}"
        )

    return Frame(frame_type=frame_type_enum, session_id=session_id, payload=payload)
