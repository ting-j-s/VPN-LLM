"""Session ID parsing and masking utilities.

Session IDs are 16-byte opaque identifiers used for session isolation.
They are NOT authentication secrets or cryptographic keys.
"""

import binascii


def parse_session_id(value: str | None) -> bytes | None:
    """Parse a 32-char hex string into a 16-byte session ID.

    Args:
        value: 32-char hex string, or None/empty to indicate "not set".

    Returns:
        16-byte session ID, or None if value is None/empty.

    Raises:
        ValueError: If value is not a valid 32-char hex string.
    """
    if value is None:
        return None
    if not value:
        return None

    stripped = value.strip()
    if not stripped:
        return None

    if len(stripped) != 32:
        raise ValueError(
            f"session_id must be 32 hex characters (16 bytes), got {len(stripped)} characters"
        )

    try:
        return binascii.unhexlify(stripped)
    except (binascii.Error, ValueError) as e:
        raise ValueError(
            f"session_id must be a valid 32-char hex string, got: {stripped[:16]}..."
        ) from e


def mask_session_id(session_id: bytes | None) -> str:
    """Return a safe-for-logging representation of a session ID.

    Only the first 8 hex characters are shown; the rest is masked.

    Args:
        session_id: 16-byte session ID, or None.

    Returns:
        Masked string like "a1b2c3d4..." or "none".
    """
    if session_id is None:
        return "none"
    if len(session_id) != 16:
        return "invalid"
    return session_id.hex()[:8] + "..."
