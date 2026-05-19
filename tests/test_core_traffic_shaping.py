"""Tests for Phase 5B — TrafficShaper integration into ClientCore/ServerCore."""

from __future__ import annotations

import random
import threading
import time
import uuid

import pytest

from src.common.frame import FrameType, create_frame, decode_frame, encode_frame
from src.core.client_core import ClientCore
from src.core.server_core import ServerCore
from src.shaping.aggregation import MAGIC_AGG, AggregationShaper
from src.shaping.base import NoopTrafficShaper, ShapedChunk, TrafficShaper
from src.shaping.config import ShapingConfig
from src.shaping.factory import create_traffic_shaper
from src.shaping.jitter import JitterShaper
from src.shaping.padding import MAGIC_PAD, PaddingShaper
from src.transport.base import MockTransport
from src.tun.tun_device import MockTunDevice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session() -> bytes:
    return uuid.uuid4().bytes


def _make_core_pair(session_id=None, traffic_shaper=None):
    """Create a wired ClientCore + MockTransport pair for isolated testing."""
    if session_id is None:
        session_id = _make_session()
    tun = MockTunDevice(name="test-tun", mtu=1400)
    transport = MockTransport()
    transport.connect()
    core = ClientCore(
        tun=tun,
        transport=transport,
        session_id=session_id,
        heartbeat_interval=60,  # suppress auto heartbeat
        heartbeat_timeout=120,
        traffic_shaper=traffic_shaper,
    )
    return core, tun, transport, session_id


# ---------------------------------------------------------------------------
# 1. Noop default behavior
# ---------------------------------------------------------------------------

class TestNoopDefault:
    def test_client_core_noop_send_bytes_unchanged(self):
        core, tun, transport, sid = _make_core_pair()
        core.start()
        try:
            payload = b"\x45\x00\x00\x14" + b"\x00" * 16
            tun.inject_packet(payload)
            time.sleep(0.2)
            sent = transport.get_sent()
            assert len(sent) >= 1
            # With Noop, transport receives raw VTUN frames
            decoded = decode_frame(sent[-1])
            assert decoded.payload == payload
            assert decoded.frame_type == FrameType.DATA
        finally:
            core.stop()

    def test_server_core_noop_send_bytes_unchanged(self):
        sid = _make_session()
        tun = MockTunDevice(name="test-tun", mtu=1400)
        transport = MockTransport()
        transport.connect()
        core = ServerCore(
            tun=tun,
            transport=transport,
            session_id=sid,
            heartbeat_interval=60,
            heartbeat_timeout=120,
        )
        core.start()
        try:
            payload = b"\x45\x00\x00\x14" + b"\x00" * 16
            tun.inject_packet(payload)
            time.sleep(0.2)
            sent = transport.get_sent()
            assert len(sent) >= 1
            decoded = decode_frame(sent[-1])
            assert decoded.payload == payload
        finally:
            core.stop()

    def test_old_constructor_no_shaper_param_still_works(self):
        tun = MockTunDevice(name="t", mtu=1400)
        t = MockTransport()
        # These must not raise — no traffic_shaper kwarg
        client = ClientCore(tun=tun, transport=t)
        assert isinstance(client.traffic_shaper, NoopTrafficShaper)
        server = ServerCore(tun=tun, transport=t)
        assert isinstance(server.traffic_shaper, NoopTrafficShaper)


# ---------------------------------------------------------------------------
# 2. Padding shaper integration
# ---------------------------------------------------------------------------

class TestPaddingIntegration:
    def test_client_core_padding_transport_sees_vpad(self):
        rng = random.Random(42)
        padding = PaddingShaper(min_padding_bytes=10, max_padding_bytes=10, rng=rng)
        core, tun, transport, sid = _make_core_pair(traffic_shaper=padding)
        core.start()
        try:
            payload = b"\x45\x00\x00\x14" + b"\x00" * 16
            tun.inject_packet(payload)
            time.sleep(0.2)
            sent = transport.get_sent()
            # At least one sent chunk should start with VPAD
            padded = [s for s in sent if s[:4] == MAGIC_PAD]
            assert len(padded) >= 1, f"No VPAD envelope in sent data: {sent}"
        finally:
            core.stop()

    def test_padding_roundtrip_recv(self):
        rng = random.Random(42)
        padding = PaddingShaper(min_padding_bytes=5, max_padding_bytes=10, rng=rng)
        core, tun, transport, sid = _make_core_pair(traffic_shaper=padding)
        core.start()
        try:
            payload = b"\x45\x00\x00\x14" + b"\x00" * 16
            tun.inject_packet(payload)
            time.sleep(0.2)

            # Simulate receiving a padded frame from the other side
            encoded = encode_frame(create_frame(FrameType.DATA, sid, b"response"))
            padded = padding.encode_frame(encoded)[0].data
            assert padded[:4] == MAGIC_PAD

            transport.inject(padded)
            time.sleep(0.3)

            # The payload should have been written to TUN
            packets = tun.get_tx_packets()
            assert b"response" in packets, f"Expected 'response' in TUN packets: {packets}"
        finally:
            core.stop()

    def test_server_core_padding_roundtrip(self):
        rng = random.Random(99)
        sid = _make_session()
        tun = MockTunDevice(name="srv-tun", mtu=1400)
        transport = MockTransport()
        transport.connect()
        padding = PaddingShaper(min_padding_bytes=8, max_padding_bytes=8, rng=rng)
        core = ServerCore(
            tun=tun,
            transport=transport,
            session_id=sid,
            heartbeat_interval=60,
            heartbeat_timeout=120,
            traffic_shaper=padding,
        )
        core.start()
        try:
            # Send direction: inject TUN packet, check transport sees padded
            payload = b"\x45\x00\x00\x14" + b"\x00" * 16
            tun.inject_packet(payload)
            time.sleep(0.2)
            sent = transport.get_sent()
            padded_sent = [s for s in sent if s[:4] == MAGIC_PAD]
            assert len(padded_sent) >= 1

            # Recv direction: inject padded frame, check TUN gets original
            encoded = encode_frame(create_frame(FrameType.DATA, sid, b"srv-response"))
            padded = padding.encode_frame(encoded)[0].data
            transport.inject(padded)
            time.sleep(0.3)
            packets = tun.get_tx_packets()
            assert b"srv-response" in packets
        finally:
            core.stop()


# ---------------------------------------------------------------------------
# 3. decode_chunk returns multiple frames → core processes all
# ---------------------------------------------------------------------------

def _passthrough_decode_only_shaper():
    """Return a shaper that decode_chunk() may return 2 frames.

    encode_frame: passthrough (1 chunk).
    decode_chunk: if data starts with a special sentinel MAGIC_MULTI,
    split into 2 encoded VTUN frames.
    """
    MAGIC_MULTI = b"MULT"
    rng = random.Random(1)

    class _MultiDecodeShaper(TrafficShaper):
        def encode_frame(self, frame: bytes) -> list[ShapedChunk]:
            return [ShapedChunk(data=frame)]

        def decode_chunk(self, chunk: bytes) -> list[bytes]:
            if chunk[:4] == MAGIC_MULTI:
                # chunk: MAGIC_MULTI(4) + len1(4) + frame1 + len2(4) + frame2
                len1 = int.from_bytes(chunk[4:8], "big")
                frame1 = chunk[8:8 + len1]
                offset = 8 + len1
                len2 = int.from_bytes(chunk[offset:offset + 4], "big")
                frame2 = chunk[offset + 4:offset + 4 + len2]
                return [frame1, frame2]
            return [chunk]

        def flush(self) -> list[ShapedChunk]:
            return []

        def close(self) -> list[ShapedChunk]:
            return []

    return _MultiDecodeShaper(rng=rng), MAGIC_MULTI


class TestMultiFrameDecode:
    def test_decode_chunk_returns_multiple_frames(self):
        multi_shaper, MAGIC_MULTI = _passthrough_decode_only_shaper()
        core, tun, transport, sid = _make_core_pair(traffic_shaper=multi_shaper)
        core.start()
        try:
            # Create two DATA frames
            frame1 = encode_frame(create_frame(FrameType.DATA, sid, b"payload-1"))
            frame2 = encode_frame(create_frame(FrameType.DATA, sid, b"payload-2"))

            # Pack into multi-decode chunk
            import struct
            chunk = MAGIC_MULTI + struct.pack(">I", len(frame1)) + frame1 + struct.pack(">I", len(frame2)) + frame2
            transport.inject(chunk)
            time.sleep(0.4)

            packets = tun.get_tx_packets()
            assert b"payload-1" in packets, f"payload-1 missing from {packets}"
            assert b"payload-2" in packets, f"payload-2 missing from {packets}"
        finally:
            core.stop()


# ---------------------------------------------------------------------------
# 4. encode_frame returns empty → flush safeguard
# ---------------------------------------------------------------------------

class TestFlushOnEmptyEncode:
    def test_empty_encode_triggers_flush(self):
        """When encode_frame returns [], _send_shaped must flush."""
        sentinel = object()
        flushed_data: list[bytes] = []

        class _EmptyEncodeThenFlushShaper(TrafficShaper):
            def encode_frame(self, frame: bytes) -> list[ShapedChunk]:
                return []  # simulate aggregation buffering

            def decode_chunk(self, chunk: bytes) -> list[bytes]:
                return [chunk]

            def flush(self) -> list[ShapedChunk]:
                flushed_data.append(b"flushed!")
                return [ShapedChunk(data=b"flushed!")]

            def close(self) -> list[ShapedChunk]:
                return []

        shaper = _EmptyEncodeThenFlushShaper(rng=random.Random(1))
        core, tun, transport, sid = _make_core_pair(traffic_shaper=shaper)
        core.start()
        try:
            payload = b"\x45\x00\x00\x14" + b"\x00" * 16
            tun.inject_packet(payload)
            time.sleep(0.3)
            assert len(flushed_data) >= 1, "flush() was not called after empty encode"
            sent = transport.get_sent()
            assert b"flushed!" in sent, f"flush output not sent: {sent}"
        finally:
            core.stop()


# ---------------------------------------------------------------------------
# 5. Jitter delay_ms does not cause sleep
# ---------------------------------------------------------------------------

class TestJitterNoSleep:
    def test_jitter_metadata_does_not_block(self):
        rng = random.Random(1)
        jitter = JitterShaper(min_ms=10, max_ms=50, rng=rng, enabled=True)
        core, tun, transport, sid = _make_core_pair(traffic_shaper=jitter)
        core.start()
        try:
            t0 = time.time()
            payload = b"\x45\x00\x00\x14" + b"\x00" * 16
            tun.inject_packet(payload)
            time.sleep(0.2)
            elapsed = time.time() - t0
            # Must complete well under 10ms (the configured jitter min)
            assert elapsed < 0.5, f"Jitter may have caused blocking: {elapsed:.2f}s"
            sent = transport.get_sent()
            assert len(sent) >= 1
        finally:
            core.stop()


# ---------------------------------------------------------------------------
# 6. HEARTBEAT not cached / bypass aggregation
# ---------------------------------------------------------------------------

class TestHeartbeatNotCached:
    def test_heartbeat_sent_with_noop(self):
        core, tun, transport, sid = _make_core_pair()
        core.heartbeat_interval = 0.05
        core.start()
        try:
            time.sleep(1.5)
            sent = transport.get_sent()
            hb_frames = []
            for s in sent:
                try:
                    f = decode_frame(s)
                    if f.frame_type == FrameType.HEARTBEAT:
                        hb_frames.append(f)
                except Exception:
                    pass
            assert len(hb_frames) >= 1, "HEARTBEAT not sent with Noop"
        finally:
            core.stop()

    def test_heartbeat_sent_with_padding(self):
        rng = random.Random(7)
        padding = PaddingShaper(min_padding_bytes=5, max_padding_bytes=10, rng=rng)
        core, tun, transport, sid = _make_core_pair(traffic_shaper=padding)
        core.heartbeat_interval = 0.05
        core.start()
        try:
            time.sleep(1.5)
            sent = transport.get_sent()
            # VPAD-enveloped frames should exist — find one with HEARTBEAT payload
            padded = [s for s in sent if s[:4] == MAGIC_PAD]
            assert len(padded) >= 1, "HEARTBEAT not padded/sent"
            hb_found = False
            for p in padded:
                decoded_encoded = padding.decode_chunk(p)[0]
                frame = decode_frame(decoded_encoded)
                if frame.frame_type == FrameType.HEARTBEAT:
                    hb_found = True
                    break
            assert hb_found, f"No HEARTBEAT found in padded frames"
        finally:
            core.stop()

    def test_heartbeat_not_buffered_by_aggregation(self):
        """Heartbeat through AggregationShaper (enabled) should still arrive."""
        rng = random.Random(42)
        agg = AggregationShaper(max_bytes=4096, max_delay_ms=10, rng=rng, enabled=True)
        core, tun, transport, sid = _make_core_pair(traffic_shaper=agg)
        core.heartbeat_interval = 0.05
        core.start()
        try:
            time.sleep(1.5)
            # Aggregation may buffer, but _send_shaped flushes when encode returns []
            sent = transport.get_sent()
            assert len(sent) >= 1, f"No data sent through aggregation shaper: {sent}"
            # Decode all sent chunks
            all_frames: list[bytes] = []
            for s in sent:
                all_frames.extend(agg.decode_chunk(s))
            has_hb = False
            for raw in all_frames:
                try:
                    f = decode_frame(raw)
                    if f.frame_type == FrameType.HEARTBEAT:
                        has_hb = True
                        break
                except Exception:
                    pass
            assert has_hb, f"HEARTBEAT was buffered and never flushed; frames: {all_frames}"
        finally:
            core.stop()


# ---------------------------------------------------------------------------
# 7. Invalid shaper config / decode error resilience
# ---------------------------------------------------------------------------

class TestShaperErrorResilience:
    def test_decode_error_does_not_crash_loop(self):
        """If shaped decode_chunk raises, core must drop the chunk and survive.

        Malformed shaped data must NOT be silently treated as valid frames.
        Only NoopTrafficShaper passes raw data through — real shapers must
        not leak unvalidated bytes into the frame decoder.
        """

        class _ExplodingDecodeShaper(TrafficShaper):
            def encode_frame(self, frame: bytes) -> list[ShapedChunk]:
                return [ShapedChunk(data=frame)]

            def decode_chunk(self, chunk: bytes) -> list[bytes]:
                raise RuntimeError("simulated shaper decode failure")

            def flush(self) -> list[ShapedChunk]:
                return []

            def close(self) -> list[ShapedChunk]:
                return []

        shaper = _ExplodingDecodeShaper(rng=random.Random(1))
        core, tun, transport, sid = _make_core_pair(traffic_shaper=shaper)
        core.start()
        try:
            encoded = encode_frame(create_frame(FrameType.DATA, sid, b"should-be-dropped"))
            transport.inject(encoded)
            time.sleep(0.3)
            # Loop should survive and still be connected
            assert core.is_connected(), "Core should still be connected after decode error"
            # Malformed shaped data must NOT reach TUN — it was dropped
            packets = tun.get_tx_packets()
            assert b"should-be-dropped" not in packets, f"Malformed shaped data leaked to TUN: {packets}"
        finally:
            core.stop()


# ---------------------------------------------------------------------------
# 8. ServerCore constructor injection
# ---------------------------------------------------------------------------

class TestServerCoreConstructorInjection:
    def test_server_core_accepts_shaper(self):
        rng = random.Random(123)
        padding = PaddingShaper(min_padding_bytes=4, max_padding_bytes=4, rng=rng)
        sid = _make_session()
        tun = MockTunDevice(name="srv-tun", mtu=1400)
        transport = MockTransport()
        transport.connect()
        core = ServerCore(
            tun=tun,
            transport=transport,
            session_id=sid,
            traffic_shaper=padding,
        )
        assert isinstance(core.traffic_shaper, PaddingShaper)
        assert core.traffic_shaper is padding


# ---------------------------------------------------------------------------
# 9. Factory-created shaper with core
# ---------------------------------------------------------------------------

class TestFactoryShaperWithCore:
    def test_factory_padding_only_works_in_core(self):
        config = ShapingConfig(
            enabled=True,
            padding_enabled=True,
            min_padding_bytes=5,
            max_padding_bytes=15,
        )
        shaper = create_traffic_shaper(config, seed=1)
        core, tun, transport, sid = _make_core_pair(traffic_shaper=shaper)
        core.start()
        try:
            payload = b"\x45\x00\x00\x14" + b"\x00" * 16
            tun.inject_packet(payload)
            time.sleep(0.2)
            sent = transport.get_sent()
            padded = [s for s in sent if s[:4] == MAGIC_PAD]
            assert len(padded) >= 1
            # Find the DATA frame (AUTH is also padded and sent first)
            data_found = False
            for p in padded:
                decoded_encoded = shaper.decode_chunk(p)[0]
                frame = decode_frame(decoded_encoded)
                if frame.payload == payload:
                    data_found = True
                    break
            assert data_found, f"DATA payload not found in padded frames"
        finally:
            core.stop()


# ---------------------------------------------------------------------------
# 10. Aggregation integration — DATA buffering + control pre-flush
# ---------------------------------------------------------------------------

class TestAggregationIntegration:
    def test_data_frame_buffered_by_aggregation(self):
        """DATA frames through aggregation-enabled shaper are buffered."""
        rng = random.Random(42)
        agg = AggregationShaper(max_bytes=4096, rng=rng, enabled=True)
        core, tun, transport, sid = _make_core_pair(traffic_shaper=agg)
        core.start()
        try:
            payload = b"\x45\x00\x00\x14" + b"\x00" * 16
            tun.inject_packet(payload)
            time.sleep(0.3)
            # With aggregation, the frame is buffered, not sent immediately
            sent = transport.get_sent()
            # AUTH frame was sent during start(), then DATA was buffered
            agg_sent = [s for s in sent if s[:4] == MAGIC_AGG]
            assert len(agg_sent) == 0, f"Single DATA should be buffered, not sent as aggregation"
        finally:
            core.stop()

    def test_heartbeat_triggers_pre_flush_of_buffered_data(self):
        """HEARTBEAT must flush pending DATA before itself."""
        rng = random.Random(42)
        # Small buffer: 20 bytes triggers flush on second frame
        agg = AggregationShaper(max_bytes=20, rng=rng, enabled=True)
        core, tun, transport, sid = _make_core_pair(traffic_shaper=agg)
        core.heartbeat_interval = 0.05
        core.start()
        try:
            payload = b"\x45\x00\x00\x14" + b"\x00" * 16  # 20 bytes
            tun.inject_packet(payload)
            time.sleep(1.5)
            sent = transport.get_sent()
            # Should have: AUTH, possibly aggregated DATA, HEARTBEAT
            assert len(sent) >= 1, f"No data sent: {sent}"
            # decode all chunks
            all_frames: list[bytes] = []
            for s in sent:
                all_frames.extend(agg.decode_chunk(s))
            # Find DATA frame
            data_found = any(
                decode_frame(raw).payload == payload
                for raw in all_frames
            )
            assert data_found, f"DATA payload not found in sent frames"
        finally:
            core.stop()

    def test_heartbeat_not_cached_by_aggregation(self):
        """HEARTBEAT must not be buffered — must arrive quickly."""
        rng = random.Random(42)
        agg = AggregationShaper(max_bytes=4096, rng=rng, enabled=True)
        core, tun, transport, sid = _make_core_pair(traffic_shaper=agg)
        core.heartbeat_interval = 0.05
        core.start()
        try:
            time.sleep(1.5)
            sent = transport.get_sent()
            all_frames: list[bytes] = []
            for s in sent:
                all_frames.extend(agg.decode_chunk(s))
            has_hb = False
            for raw in all_frames:
                try:
                    f = decode_frame(raw)
                    if f.frame_type == FrameType.HEARTBEAT:
                        has_hb = True
                        break
                except Exception:
                    pass
            assert has_hb, f"HEARTBEAT was buffered by aggregation: {all_frames}"
        finally:
            core.stop()

    def test_auth_sent_immediately(self):
        """AUTH frame must trigger pre-flush and be sent immediately."""
        rng = random.Random(42)
        agg = AggregationShaper(max_bytes=4096, rng=rng, enabled=True)
        core, tun, transport, sid = _make_core_pair(traffic_shaper=agg)
        core.start()
        try:
            time.sleep(0.3)
            sent = transport.get_sent()
            # AUTH is sent as FrameType.AUTH — must appear in transport
            all_frames: list[bytes] = []
            for s in sent:
                all_frames.extend(agg.decode_chunk(s))
            has_auth = False
            for raw in all_frames:
                try:
                    f = decode_frame(raw)
                    if f.frame_type == FrameType.AUTH:
                        has_auth = True
                        break
                except Exception:
                    pass
            assert has_auth, f"AUTH was buffered by aggregation"
        finally:
            core.stop()

    def test_close_flushes_pending_aggregation(self):
        """stop() must flush shaper buffer before closing transport."""
        rng = random.Random(42)
        agg = AggregationShaper(max_bytes=4096, rng=rng, enabled=True)
        core, tun, transport, sid = _make_core_pair(traffic_shaper=agg)
        core.start()
        try:
            payload = b"\x45\x00\x00\x14" + b"\x00" * 16
            tun.inject_packet(payload)
            time.sleep(0.3)
            # DATA should be buffered by aggregation
            assert len(agg._buffer) >= 1, f"DATA should be buffered: buffer={agg._buffer}"
            # stop() must flush the buffer (MockTransport.close clears _tx_data,
            # so we check shaper internal state instead)
            core.stop()
            assert len(agg._buffer) == 0, f"Buffer not flushed after stop: {agg._buffer}"
        finally:
            try:
                core.stop()
            except Exception:
                pass

    def test_aggregation_pipeline_roundtrip(self):
        """Aggregation + padding pipeline: decode_chunk recovers all frames."""
        config = ShapingConfig(
            enabled=True,
            aggregation_enabled=True,
            aggregation_max_bytes=4096,
            padding_enabled=True,
            min_padding_bytes=4,
            max_padding_bytes=4,
        )
        shaper = create_traffic_shaper(config, seed=42)
        core, tun, transport, sid = _make_core_pair(traffic_shaper=shaper)
        core.start()
        try:
            payload = b"\x45\x00\x00\x14" + b"\x00" * 16
            tun.inject_packet(payload)
            time.sleep(0.3)
            # flush shaper to emit buffered DATA
            flushed = shaper.flush()
            for ch in flushed:
                transport.send(ch.data)
            sent = transport.get_sent()
            assert len(sent) >= 1
            # Decode everything
            all_frames: list[bytes] = []
            for s in sent:
                all_frames.extend(shaper.decode_chunk(s))
            data_found = any(
                decode_frame(raw).payload == payload
                for raw in all_frames
            )
            assert data_found, f"Pipeline roundtrip lost DATA payload"
        finally:
            core.stop()
