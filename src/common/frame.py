"""VPN Tunnel Unified Frame Format.

Defines the standard frame format used for all transport protocols.
Format: Magic + Version + Type + Length + SessionID + Payload
"""

import struct
from dataclasses import dataclass
from typing import Optional

from .errors import FrameError
from .logger import setup_logger


logger = setup_logger(__name__)

# Frame constants
MAGIC = b"VPN\x00"  # 4 bytes magic header
VERSION = 1  # 1 byte version
FRAME_HEADER_SIZE = 16  # Magic(4) + Version(1) + Type(1) + Length(4) + SessionID(4) + Reserved(2)

# Frame types
FRAME_TYPE_DATA = 0x01
FRAME_TYPE_HELLO = 0x02
FRAME_TYPE_HELLO_ACK = 0x03
FRAME_TYPE_KEEPALIVE = 0x04
FRAME_TYPE_DISCONNECT = 0x05


@dataclass
class Frame:
    """Unified frame structure for VPN tunnel.

    Attributes:
        magic: Protocol identifier (4 bytes, must be b"VPN\\x00")
        version: Protocol version (1 byte)
        frame_type: Frame type (1 byte)
        length: Payload length (4 bytes, big-endian)
        session_id: Session identifier (4 bytes, big-endian)
        payload: Frame payload (variable length)
    """
    frame_type: int
    session_id: int
    payload: bytes

    @staticmethod
    def from_bytes(data: bytes) -> "Frame":
        """Deserialize bytes into a Frame object.

        Args:
            data: Raw frame bytes.

        Returns:
            Frame object.

        Raises:
            FrameError: If frame format is invalid.
        """
        if len(data) < FRAME_HEADER_SIZE:
            raise FrameError(f"Frame too short: {len(data)} < {FRAME_HEADER_SIZE}")

        magic = data[0:4]
        if magic != MAGIC:
            raise FrameError(f"Invalid magic bytes: {magic!r}")

        version = data[4]
        if version != VERSION:
            raise FrameError(f"Unsupported frame version: {version}")

        frame_type = data[5]
        length = struct.unpack(">I", data[6:10])[0]
        session_id = struct.unpack(">I", data[10:14])[0]
        payload = data[FRAME_HEADER_SIZE:FRAME_HEADER_SIZE + length]

        if len(payload) < length:
            raise FrameError(f"Payload truncated: {len(payload)} < {length}")

        return Frame(frame_type=frame_type, session_id=session_id, payload=payload)

    def to_bytes(self) -> bytes:
        """Serialize Frame object to bytes.

        Returns:
            Raw frame bytes.
        """
        header = struct.pack(
            ">4s B B I I 2x",  # All fields big-endian (network byte order)
            MAGIC,
            VERSION,
            self.frame_type,
            len(self.payload),
            self.session_id,
        )
        return header + self.payload

    @classmethod
    def create_data_frame(cls, session_id: int, payload: bytes) -> "Frame":
        """Create a data frame.

        Args:
            session_id: Session identifier.
            payload: Raw IP packet data.

        Returns:
            Frame object.
        """
        return cls(frame_type=FRAME_TYPE_DATA, session_id=session_id, payload=payload)

    @classmethod
    def create_hello_frame(cls, session_id: int) -> "Frame":
        """Create a hello frame for handshake.

        Args:
            session_id: Session identifier.

        Returns:
            Frame object.
        """
        return cls(frame_type=FRAME_TYPE_HELLO, session_id=session_id, payload=b"HELLO")

    @classmethod
    def create_hello_ack_frame(cls, session_id: int) -> "Frame":
        """Create a hello acknowledgment frame.

        Args:
            session_id: Session identifier.

        Returns:
            Frame object.
        """
        return cls(frame_type=FRAME_TYPE_HELLO_ACK, session_id=session_id, payload=b"ACK")

    @classmethod
    def create_keepalive_frame(cls, session_id: int) -> "Frame":
        """Create a keepalive frame.

        Args:
            session_id: Session identifier.

        Returns:
            Frame object.
        """
        return cls(frame_type=FRAME_TYPE_KEEPALIVE, session_id=session_id, payload=b"")

    @classmethod
    def create_disconnect_frame(cls, session_id: int) -> "Frame":
        """Create a disconnect frame.

        Args:
            session_id: Session identifier.

        Returns:
            Frame object.
        """
        return cls(frame_type=FRAME_TYPE_DISCONNECT, session_id=session_id, payload=b"")

    def __repr__(self) -> str:
        return (
            f"Frame(type={self.frame_type:#04x}, session_id={self.session_id}, "
            f"payload_len={len(self.payload)})"
        )
