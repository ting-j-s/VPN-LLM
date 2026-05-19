"""Fragmentation skeleton for burst smoothing.

Splits large frames into variable-size fragments.
Envelope format (big-endian):
  MAGIC_FRAG(4) + frag_id(2) + frag_count(2) + original_len(4) + fragment_data(N)

Current status: SKELETON — split_frame() / reassemble_fragments() are
standalone pure functions. Not yet integrated into the default pipeline.
"""

from __future__ import annotations

import struct

from .base import ShapedChunk

MAGIC_FRAG = b"VFRG"
FRAG_HEADER_SIZE = 12  # MAGIC(4) + frag_id(2) + frag_count(2) + original_len(4)


def split_frame(frame: bytes, max_chunk_size: int, min_size: int = 0) -> list[bytes]:
    """Split a frame into fragment envelope chunks.

    Args:
        frame: Original frame bytes.
        max_chunk_size: Maximum size of each fragment's data portion.
        min_size: Minimum frame size to trigger fragmentation.

    Returns:
        List of fragment envelope bytes, or [frame] if below min_size.
    """
    if max_chunk_size <= 0:
        raise ValueError(f"max_chunk_size must be > 0, got {max_chunk_size}")
    if min_size < 0:
        raise ValueError(f"min_size must be >= 0, got {min_size}")
    if max_chunk_size < FRAG_HEADER_SIZE + 1:
        raise ValueError(
            f"max_chunk_size must be at least {FRAG_HEADER_SIZE + 1} "
            f"(header + 1 byte payload)"
        )

    if len(frame) < min_size:
        return [frame]

    data_per_frag = max_chunk_size - FRAG_HEADER_SIZE
    fragments: list[bytes] = []
    offset = 0
    total = len(frame)
    # Calculate fragment count (ceiling division)
    frag_count = (total + data_per_frag - 1) // data_per_frag

    for i in range(frag_count):
        frag_data = frame[offset:offset + data_per_frag]
        header = struct.pack(">4s H H I", MAGIC_FRAG, i, frag_count, total)
        fragments.append(header + frag_data)
        offset += data_per_frag

    return fragments


def reassemble_fragments(fragments: list[bytes]) -> bytes:
    """Reassemble fragment envelope bytes into the original frame.

    Args:
        fragments: Fragment envelope bytes (order-independent; sorted by frag_id).

    Returns:
        Reassembled original frame bytes.
    """
    if not fragments:
        raise ValueError("No fragments to reassemble")

    parsed: list[tuple[int, int, int, bytes]] = []  # (frag_id, count, total, data)
    for frag in fragments:
        if len(frag) < FRAG_HEADER_SIZE:
            raise ValueError(f"Fragment too short: {len(frag)} < {FRAG_HEADER_SIZE}")
        magic, fid, count, total = struct.unpack(">4s H H I", frag[:FRAG_HEADER_SIZE])
        if magic != MAGIC_FRAG:
            raise ValueError(f"Invalid fragment magic: {magic!r}, expected {MAGIC_FRAG!r}")
        parsed.append((fid, count, total, frag[FRAG_HEADER_SIZE:]))

    # Sort by fragment id
    parsed.sort(key=lambda x: x[0])

    # Validate
    expected_count = parsed[0][1]
    expected_total = parsed[0][2]
    if len(parsed) != expected_count:
        raise ValueError(f"Fragment count mismatch: got {len(parsed)}, expected {expected_count}")

    result = b"".join(p[3] for p in parsed)
    if len(result) != expected_total:
        raise ValueError(f"Reassembled length mismatch: got {len(result)}, expected {expected_total}")

    return result


def fragments_to_chunks(fragments: list[bytes]) -> list[ShapedChunk]:
    """Convert fragment envelope bytes to ShapedChunk list."""
    chunks: list[ShapedChunk] = []
    for frag in fragments:
        if len(frag) >= FRAG_HEADER_SIZE and frag[:4] == MAGIC_FRAG:
            _, fid, count, total = struct.unpack(">4s H H I", frag[:FRAG_HEADER_SIZE])
            chunks.append(ShapedChunk(
                data=frag,
                metadata={"fragment_id": fid, "fragment_count": count, "original_len": total},
            ))
        else:
            chunks.append(ShapedChunk(data=frag))
    return chunks


def chunks_to_fragments(chunks: list[ShapedChunk]) -> bytes:
    """Reassemble original frame from a list of ShapedChunk (fragments)."""
    frag_bytes = [ch.data for ch in chunks]
    return reassemble_fragments(frag_bytes)
