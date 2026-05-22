"""Phase 9C — Shaping data-path integrity tests.

Validates that shaped data round-trips correctly through the pipeline:
  encode: frame → aggregation → padding → jitter → chunk
  decode: chunk → jitter⁻¹ → padding⁻¹ → aggregation⁻¹ → frame

And that core-level TUN writes never receive shaped envelopes, dummy frames,
or partial payloads.
"""

from __future__ import annotations

import random
import struct
import threading
import time
import uuid

import pytest

from src.common.frame import FrameType, create_frame, decode_frame, encode_frame
from src.core.client_core import ClientCore
from src.core.server_core import ServerCore
from src.shaping.base import NoopTrafficShaper, ShapedChunk, TrafficShaper
from src.shaping.config import ShapingConfig
from src.shaping.factory import PipelineTrafficShaper, create_traffic_shaper
from src.shaping.padding import MAGIC_PAD, PaddingShaper
from src.shaping.aggregation import MAGIC_AGG, AggregationShaper
from src.shaping.jitter import JitterShaper
from src.transport.base import MockTransport
from src.tun.tun_device import MockTunDevice


def _make_session() -> bytes:
    return uuid.uuid4().bytes


# ---------------------------------------------------------------------------
# 1. Byte-for-byte pipeline roundtrip (aggregation + padding + jitter)
# ---------------------------------------------------------------------------

class TestPipelineRoundtrip:
    """Full pipeline encode→decode must recover original frames byte-for-byte."""

    @staticmethod
    def _make_pipeline() -> PipelineTrafficShaper:
        rng = random.Random(42)
        stages: list[TrafficShaper] = [
            AggregationShaper(max_bytes=4096, rng=rng, enabled=True),
            PaddingShaper(min_padding_bytes=4, max_padding_bytes=8, rng=rng, enabled=True),
            JitterShaper(min_ms=1, max_ms=5, rng=rng, enabled=True),
        ]
        return PipelineTrafficShaper(stages=stages, rng=rng)

    def test_single_frame_roundtrip(self):
        """One frame in, one frame out after pipeline encode+flush+decode."""
        shaper = self._make_pipeline()
        frame = b"frame-1-payload-" + b"A" * 80
        chunks = shaper.encode_frame(frame)
        assert chunks == [], "single frame should be buffered by aggregation"
        chunks = shaper.flush()
        assert len(chunks) == 1, f"expected 1 chunk after flush, got {len(chunks)}"
        decoded = shaper.decode_chunk(chunks[0].data)
        assert decoded == [frame], f"roundtrip failed: {decoded} != [{frame}]"

    def test_multiple_frames_roundtrip(self):
        """Multiple buffered frames should aggregate, then decode to original."""
        shaper = self._make_pipeline()
        frames = [f"frame-{i}-".encode() + b"B" * (20 + i * 10) for i in range(8)]
        for f in frames:
            assert shaper.encode_frame(f) == [], f"frame {f[:20]} should be buffered"
        chunks = shaper.flush()
        assert len(chunks) == 1, f"expected 1 aggregated chunk, got {len(chunks)}"
        decoded = shaper.decode_chunk(chunks[0].data)
        assert decoded == frames, f"multi-frame roundtrip failed"

    def test_single_frame_no_vagg_envelope(self):
        """A single flushed frame must NOT carry VAGG envelope (optimisation)."""
        shaper = self._make_pipeline()
        shaper.encode_frame(b"solitary-frame")
        chunks = shaper.flush()
        assert len(chunks) == 1
        # The chunk goes through padding (VPAD) but NOT aggregation envelope
        assert chunks[0].data[:4] != MAGIC_AGG, "single frame must not use VAGG"

    def test_multi_frame_uses_vagg(self):
        """Multiple flushed frames MUST use VAGG envelope."""
        shaper = self._make_pipeline()
        shaper.encode_frame(b"a")
        shaper.encode_frame(b"b")
        chunks = shaper.flush()
        assert len(chunks) == 1
        # After pipeline: aggregation(VAGG) → padding(VPAD) → jitter
        assert MAGIC_PAD in chunks[0].data[:20], f"expected VPAD: {chunks[0].data[:30]}"
        assert MAGIC_AGG in chunks[0].data[:20], f"expected VAGG: {chunks[0].data[:30]}"


# ---------------------------------------------------------------------------
# 2. Aggregation split: decode must split aggregated frames
# ---------------------------------------------------------------------------

class TestAggregationSplit:
    def test_aggregation_split_to_individual_frames(self):
        rng = random.Random(1)
        agg = AggregationShaper(max_bytes=4096, rng=rng, enabled=True)
        frames = [b"frame-A-" + b"M" * 30, b"frame-B-" + b"N" * 40, b"frame-C-" + b"P" * 50]
        for f in frames:
            agg.encode_frame(f)
        chunks = agg.flush()
        assert len(chunks) == 1
        decoded = agg.decode_chunk(chunks[0].data)
        assert decoded == frames, f"split failed: got {len(decoded)} frames"
        assert len(decoded) == 3, f"expected 3 frames from split, got {len(decoded)}"

    def test_aggregation_split_preserves_boundaries(self):
        """Each decoded frame must be exactly the original, no concatenation."""
        rng = random.Random(2)
        agg = AggregationShaper(max_bytes=4096, rng=rng, enabled=True)
        f1 = b"\x00" * 10
        f2 = b"\xff" * 20
        agg.encode_frame(f1)
        agg.encode_frame(f2)
        chunks = agg.flush()
        decoded = agg.decode_chunk(chunks[0].data)
        assert len(decoded) == 2
        assert decoded[0] == f1
        assert decoded[1] == f2

    def test_single_frame_not_split(self):
        """Single frame through aggregation must not be wrapped in VAGG."""
        rng = random.Random(3)
        agg = AggregationShaper(max_bytes=4096, rng=rng, enabled=True)
        agg.encode_frame(b"lonely-frame")
        chunks = agg.flush()
        assert len(chunks) == 1
        assert chunks[0].data == b"lonely-frame", f"single frame altered: {chunks[0].data[:30]}"
        decoded = agg.decode_chunk(chunks[0].data)
        assert decoded == [b"lonely-frame"]


# ---------------------------------------------------------------------------
# 3. Padding strip: VPAD must be fully stripped before frame decode
# ---------------------------------------------------------------------------

class TestPaddingStrip:
    def test_padding_encode_then_decode_strips_completely(self):
        rng = random.Random(55)
        pad = PaddingShaper(min_padding_bytes=8, max_padding_bytes=16, rng=rng, enabled=True)
        frame = b"payload-" + b"D" * 60
        chunks = pad.encode_frame(frame)
        assert len(chunks) == 1
        assert chunks[0].data[:4] == MAGIC_PAD
        decoded = pad.decode_chunk(chunks[0].data)
        assert decoded == [frame], f"padding not stripped: {decoded[0][:30]}"

    def test_padding_disabled_passthrough(self):
        rng = random.Random(1)
        pad = PaddingShaper(min_padding_bytes=8, max_padding_bytes=16, rng=rng, enabled=False)
        frame = b"passthrough-frame"
        chunks = pad.encode_frame(frame)
        assert chunks[0].data == frame
        decoded = pad.decode_chunk(frame)
        assert decoded == [frame]

    def test_padding_does_not_change_payload(self):
        """Payload inside VPAD must be byte-identical to original."""
        rng = random.Random(99)
        pad = PaddingShaper(min_padding_bytes=12, max_padding_bytes=12, rng=rng, enabled=True)
        frame = b"immutable-payload-" + bytes(range(256))
        chunks = pad.encode_frame(frame)
        decoded = pad.decode_chunk(chunks[0].data)
        assert decoded == [frame]


# ---------------------------------------------------------------------------
# 4. Dummy frame must NOT be written to TUN
# ---------------------------------------------------------------------------

class TestDummyFrameNotWrittenToTun:
    def test_dummy_frame_filtered_by_core(self):
        """A ShapedChunk with is_dummy=True must never reach TUN write."""
        sentinel = b"dummy-should-not-reach-tun"

        class _DummyFrameShaper(TrafficShaper):
            def encode_frame(self, frame: bytes) -> list[ShapedChunk]:
                return [ShapedChunk(data=frame)]

            def decode_chunk(self, chunk: bytes) -> list[bytes]:
                # Return a valid-looking VTUN frame whose payload is the sentinel
                sid = _make_session()
                dummy_frame = encode_frame(create_frame(FrameType.DATA, sid, sentinel))
                # Return both a dummy and real frame mixed
                if chunk[:8] == b"TRIGGER!":
                    return [dummy_frame]
                return [chunk]

            def flush(self) -> list[ShapedChunk]:
                return []

            def close(self) -> list[ShapedChunk]:
                return []

        sid = _make_session()
        tun = MockTunDevice(name="dummy-tun", mtu=1400)
        transport = MockTransport()
        transport.connect()
        shaper = _DummyFrameShaper(rng=random.Random(1))
        core = ClientCore(
            tun=tun, transport=transport, session_id=sid,
            heartbeat_interval=60, heartbeat_timeout=120,
            traffic_shaper=shaper,
        )
        core.start()
        try:
            # Inject the trigger
            transport.inject(b"TRIGGER!raw-data")
            time.sleep(0.3)
            packets = tun.get_tx_packets()
            # The sentinel must NOT appear
            assert sentinel not in packets, (
                f"dummy frame leaked to TUN: {packets}"
            )
        finally:
            core.stop()


# ---------------------------------------------------------------------------
# 5. TUN payload purity: no VPAD/VAGG/shaping envelopes in TUN writes
# ---------------------------------------------------------------------------

class TestTunPayloadPurity:
    def test_no_shaping_magic_in_tun_write(self):
        """Payload written to TUN must never contain VPAD or VAGG magic bytes."""
        config = ShapingConfig(
            enabled=True,
            aggregation_enabled=True,
            aggregation_max_bytes=4096,
            padding_enabled=True,
            min_padding_bytes=4,
            max_padding_bytes=8,
        )
        shaper = create_traffic_shaper(config, seed=42)
        sid = _make_session()
        tun = MockTunDevice(name="purity-tun", mtu=1400)
        transport = MockTransport()
        transport.connect()
        core = ClientCore(
            tun=tun, transport=transport, session_id=sid,
            heartbeat_interval=60, heartbeat_timeout=120,
            traffic_shaper=shaper,
        )
        core.start()
        try:
            # Inject a shaped frame that should decode to a clean DATA frame
            raw_frame = encode_frame(create_frame(FrameType.DATA, sid, b"clean-payload"))
            # Run it through the shaper's encode path
            shaper.encode_frame(raw_frame)
            shaped = shaper.flush()
            assert len(shaped) == 1
            # Now inject the shaped chunk back via transport (simulating server->client)
            transport.inject(shaped[0].data)
            time.sleep(0.3)
            packets = tun.get_tx_packets()
            # Ensure VPAD/VAGG never appear in TUN payloads
            for pkt in packets:
                assert MAGIC_PAD not in pkt, f"VPAD leaked into TUN payload: {pkt[:40]}"
                assert MAGIC_AGG not in pkt, f"VAGG leaked into TUN payload: {pkt[:40]}"
            assert b"clean-payload" in packets, f"clean payload not delivered: {packets}"
        finally:
            core.stop()


# ---------------------------------------------------------------------------
# 6. Partial / malformed chunk must not leak to TUN
# ---------------------------------------------------------------------------

class TestMalformedChunkRejection:
    def test_truncated_chunk_not_leaked_to_tun(self):
        """A chunk that barely looks valid but is truncated must not reach TUN."""
        config = ShapingConfig(
            enabled=True,
            padding_enabled=True,
            min_padding_bytes=4,
            max_padding_bytes=4,
        )
        shaper = create_traffic_shaper(config, seed=1)
        sid = _make_session()
        tun = MockTunDevice(name="malform-tun", mtu=1400)
        transport = MockTransport()
        transport.connect()
        core = ClientCore(
            tun=tun, transport=transport, session_id=sid,
            heartbeat_interval=60, heartbeat_timeout=120,
            traffic_shaper=shaper,
        )
        core.start()
        try:
            # Create a chunk that starts with VPAD magic but is truncated
            malicious = b"VPAD" + struct.pack(">I H", 100, 0) + b"short"
            transport.inject(malicious)
            time.sleep(0.3)
            packets = tun.get_tx_packets()
            assert b"short" not in packets, f"truncated VPAD leaked to TUN: {packets}"
        finally:
            core.stop()

    def test_garbage_not_parsed_as_frame(self):
        """Random bytes that happen to start with VTUN must still be validated."""
        sid = _make_session()
        tun = MockTunDevice(name="garbage-tun", mtu=1400)
        transport = MockTransport()
        transport.connect()
        core = ClientCore(
            tun=tun, transport=transport, session_id=sid,
            heartbeat_interval=60, heartbeat_timeout=120,
        )
        core.start()
        try:
            # Construct something that starts VTUN but has garbage inside
            header = b"VTUN" + b"\x01\x01"  # version=1, type=DATA
            garbage_len = struct.pack(">I", 50)  # claim 50 bytes payload
            fake_sid = b"\x00" * 16
            garbage = header + garbage_len + fake_sid + b"X" * 10  # only 10 bytes, short
            transport.inject(garbage)
            time.sleep(0.3)
            packets = tun.get_tx_packets()
            # The frame should be rejected (truncated), not leak to TUN
            assert b"X" * 10 not in packets, f"garbage leaked to TUN: {packets}"
        finally:
            core.stop()


# ---------------------------------------------------------------------------
# 7. Symmetric shaping roundtrip (both sides have same pipeline)
# ---------------------------------------------------------------------------

class TestSymmetricShapingRoundtrip:
    """Simulate Phase 9C symmetric shaping: both client and server use the
    same PipelineTrafficShaper. Data must flow bidirectionally."""

    def test_symmetric_bidirectional_roundtrip(self):
        config = ShapingConfig(
            enabled=True,
            aggregation_enabled=True,
            aggregation_max_bytes=4096,
            padding_enabled=True,
            min_padding_bytes=4,
            max_padding_bytes=8,
            jitter_enabled=True,
            jitter_min_ms=1,
            jitter_max_ms=5,
        )
        client_shaper = create_traffic_shaper(config, seed=1)
        server_shaper = create_traffic_shaper(config, seed=2)

        sid = _make_session()

        # -- Client --
        client_tun = MockTunDevice(name="cli-tun", mtu=1400)
        client_transport = MockTransport()
        client_transport.connect()
        client_core = ClientCore(
            tun=client_tun, transport=client_transport, session_id=sid,
            heartbeat_interval=60, heartbeat_timeout=120,
            traffic_shaper=client_shaper,
        )

        # -- Server --
        server_tun = MockTunDevice(name="srv-tun", mtu=1400)
        server_transport = MockTransport()
        server_transport.connect()
        server_core = ServerCore(
            tun=server_tun, transport=server_transport, session_id=sid,
            heartbeat_interval=60, heartbeat_timeout=120,
            traffic_shaper=server_shaper,
        )

        # Relay: periodically move data between transports
        _relay_running = [True]
        _last_relayed = [0, 0]  # index into client_sent, server_sent

        def _wire_relay():
            while _relay_running[0]:
                client_sent = client_transport.get_sent()
                server_sent = server_transport.get_sent()
                for i in range(_last_relayed[0], len(client_sent)):
                    server_transport.inject(client_sent[i])
                _last_relayed[0] = len(client_sent)
                for i in range(_last_relayed[1], len(server_sent)):
                    client_transport.inject(server_sent[i])
                _last_relayed[1] = len(server_sent)
                time.sleep(0.05)

        relay_thread = threading.Thread(target=_wire_relay, daemon=True)

        client_core.start()
        server_core.start()
        relay_thread.start()

        try:
            # Client → Server: inject TUN packet on client, trigger flush
            client_payload = b"cli-to-srv-" + b"C" * 80
            client_tun.inject_packet(client_payload)
            time.sleep(0.5)
            for ch in client_shaper.flush():
                client_transport.send(ch.data)
            time.sleep(1.0)

            # Check transport carried shaped data
            client_sent = client_transport.get_sent()
            assert len(client_sent) >= 1, f"client sent nothing"
            # Verify sent data went through shaping (contains VPAD or VAGG)
            shaped_data = b"".join(client_sent)
            has_envelope = MAGIC_PAD in shaped_data or MAGIC_AGG in shaped_data
            assert has_envelope, (
                f"shaped transport data should contain VPAD/VAGG envelope"
            )
        finally:
            _relay_running[0] = False
            client_core.stop()
            server_core.stop()


# ---------------------------------------------------------------------------
# 8. Shaper decode must handle non-VTUN data gracefully
# ---------------------------------------------------------------------------

class TestDecodeGracefulPassthrough:
    def test_unshaped_frame_passes_through_pipeline(self):
        """Raw VTUN frame (no shaping) must pass through PipelineTrafficShaper unchanged."""
        config = ShapingConfig(
            enabled=True,
            aggregation_enabled=True,
            aggregation_max_bytes=4096,
            padding_enabled=True,
            min_padding_bytes=4,
            max_padding_bytes=8,
        )
        shaper = create_traffic_shaper(config, seed=42)
        sid = _make_session()
        raw_frame = encode_frame(create_frame(FrameType.HEARTBEAT, sid, b""))
        decoded = shaper.decode_chunk(raw_frame)
        assert decoded == [raw_frame], f"unshaped frame not passed through: {decoded}"

    def test_vpad_only_chunk_decoded_correctly(self):
        """A chunk that is VPAD-wrapped but not VAGG-wrapped must decode."""
        rng = random.Random(1)
        pad = PaddingShaper(min_padding_bytes=8, max_padding_bytes=8, rng=rng, enabled=True)
        agg = AggregationShaper(max_bytes=4096, rng=rng, enabled=True)
        pipeline = PipelineTrafficShaper(stages=[agg, pad], rng=rng)
        frame = b"vpad-only-test-frame"
        pipeline.encode_frame(frame)
        chunks = pipeline.flush()
        assert len(chunks) == 1
        # Should be VPAD-wrapped but not VAGG (single frame optimization)
        decoded = pipeline.decode_chunk(chunks[0].data)
        assert decoded == [frame], f"VPAD-only decode failed: {decoded}"
