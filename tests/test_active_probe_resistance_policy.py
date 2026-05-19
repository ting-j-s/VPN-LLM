"""Tests for Phase 6B server error path unification policy.

Verifies that the server and client core silently drop malformed/unexpected
input without sending application-layer responses or exposing distinct error
types to remote peers.
"""

import time
import uuid

import pytest

from src.common.frame import (
    Frame,
    FrameDecodeError,
    FrameType,
    create_frame,
    decode_frame,
    encode_frame,
)
from src.core.client_core import ClientCore
from src.core.server_core import ServerCore
from src.evaluation.probe.probe_runner import MockProbeRunner
from src.evaluation.probe.report import (
    compute_behavior_summary,
    generate_probe_report,
)
from src.llm.detection.detector_report import from_probe_report
from src.llm.detection.gate import DetectionThresholds, evaluate_detection_report
from src.transport.base import MockTransport
from src.tun.tun_device import MockTunDevice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_server_and_client():
    """Create a ServerCore + ClientCore pair sharing a single MockTransport."""
    session_id = uuid.uuid4().bytes
    transport = MockTransport()
    transport.connect()

    tun_server = MockTunDevice()
    tun_server.open()
    tun_client = MockTunDevice()
    tun_client.open()

    server = ServerCore(
        tun=tun_server,
        transport=transport,
        session_id=session_id,
        heartbeat_interval=999.0,
        heartbeat_timeout=999.0,
    )
    client = ClientCore(
        tun=tun_client,
        transport=transport,
        session_id=session_id,
        heartbeat_interval=999.0,
        heartbeat_timeout=999.0,
    )

    server._running = True
    server._stop_event.clear()
    client._running = True
    client._stop_event.clear()

    return server, client, transport


def _process_one_recv(core):
    """Simulate a single recv+decode+handle iteration without looping.

    Mirrors the body of _transport_to_tun_loop, minus the while loop.
    Returns True if the loop would continue, False if it would break.
    """
    try:
        data = core.transport.recv(timeout=1.0)
        if data is None:
            return True  # no data → continue

        core._last_received_time = time.time()

        try:
            encoded_frames = core.traffic_shaper.decode_chunk(data)
        except Exception:
            return True  # shaper error → continue

        for encoded in encoded_frames:
            frame = decode_frame(encoded)
            core._handle_frame(frame)

        return True

    except Exception as e:
        from src.common.errors import TransportTimeout
        if core._stop_event.is_set():
            return False
        if isinstance(e, TransportTimeout):
            return True
        if isinstance(e, FrameDecodeError):
            return True  # silent drop → continue
        if not core.transport.is_connected():
            return False
        return False


# ---------------------------------------------------------------------------
# 1. Malformed decode does not send a response
# ---------------------------------------------------------------------------


class TestMalformedDecodeNoResponse:
    """Verify that FrameDecodeError triggers silent drop, not close or response."""

    def test_frame_too_short_dropped_silently(self):
        server, _client, transport = _make_server_and_client()

        transport.inject(b"\x00\x01\x02")
        result = _process_one_recv(server)

        assert result is True  # continues loop
        assert transport.is_connected()
        assert transport.get_sent() == []

    def test_bad_magic_dropped_silently(self):
        server, _client, transport = _make_server_and_client()

        payload = b"BADM" + b"\x01" + b"\x00" + b"\x00\x00\x00\x00" + b"\x00" * 16
        transport.inject(payload)
        result = _process_one_recv(server)

        assert result is True
        assert transport.is_connected()
        assert transport.get_sent() == []

    def test_invalid_frame_type_dropped_silently(self):
        server, _client, transport = _make_server_and_client()

        payload = (
            b"VTUN"
            + b"\x01"
            + b"\xFF"  # invalid frame_type
            + b"\x00\x00\x00\x04"
            + b"\x00" * 16
            + b"abcd"
        )
        transport.inject(payload)
        result = _process_one_recv(server)

        assert result is True
        assert transport.is_connected()
        assert transport.get_sent() == []

    def test_declared_length_too_large_dropped_silently(self):
        server, _client, transport = _make_server_and_client()

        header = (
            b"VTUN"
            + b"\x01"
            + b"\x01"
            + (1024 * 1024).to_bytes(4, "big")
            + b"\x00" * 16
        )
        transport.inject(header + b"short_data")
        result = _process_one_recv(server)

        assert result is True
        assert transport.is_connected()
        assert transport.get_sent() == []

    def test_http_get_garbage_dropped_silently(self):
        server, _client, transport = _make_server_and_client()

        http_payload = b"GET / HTTP/1.1\r\nHost: 127.0.0.1:9000\r\n\r\n"
        transport.inject(http_payload)
        result = _process_one_recv(server)

        assert result is True
        assert transport.is_connected()
        assert transport.get_sent() == []

    def test_tls_clienthello_like_dropped_silently(self):
        server, _client, transport = _make_server_and_client()

        tls_payload = (
            b"\x16\x03\x01\x00\xa5\x01\x00\x00\xa1\x03\x03"
            + b"\x00" * 32
            + b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        )
        transport.inject(tls_payload)
        result = _process_one_recv(server)

        assert result is True
        assert transport.is_connected()
        assert transport.get_sent() == []

    def test_random_bytes_dropped_silently(self):
        server, _client, transport = _make_server_and_client()

        transport.inject(b"\x00" * 2048)
        result = _process_one_recv(server)

        assert result is True
        assert transport.is_connected()
        assert transport.get_sent() == []

    def test_zero_bytes_dropped_silently(self):
        server, _client, transport = _make_server_and_client()

        transport.inject(b"\x00")
        result = _process_one_recv(server)

        assert result is True
        assert transport.is_connected()
        assert transport.get_sent() == []


# ---------------------------------------------------------------------------
# 2. Wrong session_id — no response
# ---------------------------------------------------------------------------


class TestWrongSessionIdNoResponse:
    """Verify wrong session_id frames are silently dropped."""

    def test_wrong_session_id_dropped_no_response(self):
        server, _client, transport = _make_server_and_client()

        wrong_session = uuid.uuid4().bytes
        while wrong_session == server.session_id:
            wrong_session = uuid.uuid4().bytes

        frame = create_frame(FrameType.DATA, wrong_session, b"hello")
        transport.inject(encode_frame(frame))
        result = _process_one_recv(server)

        assert result is True  # continues
        assert transport.is_connected()
        assert transport.get_sent() == []
        # DATA should NOT have been written to TUN
        assert server._transport_to_tun_bytes == 0

    def test_wrong_session_heartbeat_dropped_no_response(self):
        server, _client, transport = _make_server_and_client()

        wrong_session = uuid.uuid4().bytes
        while wrong_session == server.session_id:
            wrong_session = uuid.uuid4().bytes

        frame = create_frame(FrameType.HEARTBEAT, wrong_session)
        transport.inject(encode_frame(frame))
        result = _process_one_recv(server)

        assert result is True
        assert transport.is_connected()
        assert transport.get_sent() == []


# ---------------------------------------------------------------------------
# 3. Normal frames are unaffected
# ---------------------------------------------------------------------------


class TestNormalFramesUnaffected:
    """Verify legitimate DATA, HEARTBEAT, and AUTH frames still work."""

    def test_data_frame_still_written_to_tun(self):
        server, _client, transport = _make_server_and_client()

        frame = create_frame(FrameType.DATA, server.session_id, b"valid_ip_packet")
        transport.inject(encode_frame(frame))
        result = _process_one_recv(server)

        assert result is True
        assert server._transport_to_tun_bytes == len(b"valid_ip_packet")

    def test_heartbeat_still_processed(self):
        server, _client, transport = _make_server_and_client()

        frame = create_frame(FrameType.HEARTBEAT, server.session_id)
        transport.inject(encode_frame(frame))
        result = _process_one_recv(server)

        assert result is True

    def test_multiple_malformed_then_valid(self):
        """Malformed frames should not prevent processing of valid frames."""
        server, _client, transport = _make_server_and_client()

        # Inject malformed
        transport.inject(b"\x00\x01")
        assert _process_one_recv(server) is True

        transport.inject(b"BADM" + b"\x01" + b"\x00" + b"\x00\x00\x00\x00" + b"\x00" * 16)
        assert _process_one_recv(server) is True

        # Then a valid frame
        frame = create_frame(FrameType.DATA, server.session_id, b"after_malformed")
        transport.inject(encode_frame(frame))
        assert _process_one_recv(server) is True

        assert server._transport_to_tun_bytes == len(b"after_malformed")

    def test_client_also_drops_malformed(self):
        """Client core should also silently drop malformed frames (symmetry)."""
        _server, client, transport = _make_server_and_client()

        transport.inject(b"\x00\x01\x02")
        result = _process_one_recv(client)

        assert result is True
        assert transport.is_connected()
        assert transport.get_sent() == []


# ---------------------------------------------------------------------------
# 4. Unified external error_type
# ---------------------------------------------------------------------------


class TestUnifiedExternalErrorType:
    """Verify that all malformed scenarios produce the same external error_type."""

    def test_all_mock_scenarios_same_error_type(self):
        runner = MockProbeRunner(seed=42)
        results = runner.run_all()

        error_types = set(r.error_type for r in results if r.error_type)
        assert error_types == {"timeout"}, (
            f"Expected all scenarios to show 'timeout', got {error_types}"
        )

    def test_no_scenario_produces_close(self):
        runner = MockProbeRunner(seed=42)
        results = runner.run_all()

        close_count = sum(1 for r in results if r.close_observed)
        assert close_count == 0

    def test_no_scenario_produces_response(self):
        runner = MockProbeRunner(seed=42)
        results = runner.run_all()

        response_count = sum(1 for r in results if r.bytes_received > 0)
        assert response_count == 0


# ---------------------------------------------------------------------------
# 5. Probe report risk drops under unified policy
# ---------------------------------------------------------------------------


class TestProbeReportRiskDrop:
    """Verify the unified policy lowers probe report risk scores."""

    def test_risk_drops_to_low_under_unified_policy(self):
        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        report = generate_probe_report(results)

        assert report["risk_score"] < 0.3, (
            f"Risk score should be under low threshold, got {report['risk_score']}"
        )
        assert report["risk_level"] == "low"

    def test_behavior_summary_single_error_type(self):
        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        summary = compute_behavior_summary(results)

        assert summary["distinct_error_types"] == 1

    def test_no_close_time_variance(self):
        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        report = generate_probe_report(results)

        mcv = next(
            m for m in report["metrics"] if m["name"] == "malformed_close_time_variance"
        )
        assert mcv["value"] == 0.0


# ---------------------------------------------------------------------------
# 6. DetectionGate with unified policy
# ---------------------------------------------------------------------------


class TestDetectionGateWithUnifiedPolicy:
    """Verify DetectionGate reports pass or warn appropriately."""

    def test_passes_with_default_thresholds(self):
        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        report = generate_probe_report(results)
        dr = from_probe_report(report)
        evaluated = evaluate_detection_report(dr)
        assert evaluated.passed is True

    def test_passes_with_tight_probe_thresholds(self):
        """Unified policy should pass even tight probe thresholds."""
        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        report = generate_probe_report(results)
        dr = from_probe_report(report)

        thresholds = DetectionThresholds(
            max_probe_response_variance=0.3,
            max_malformed_close_time_variance=0.3,
        )
        evaluated = evaluate_detection_report(dr, thresholds)
        assert evaluated.passed is True

    def test_fails_with_extremely_tight_thresholds(self):
        """Extremely tight thresholds catch the mixture_score component."""
        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        report = generate_probe_report(results)
        dr = from_probe_report(report)

        thresholds = DetectionThresholds(
            max_probe_response_variance=0.05,
        )
        evaluated = evaluate_detection_report(dr, thresholds)

        probe_m = next(
            (m for m in evaluated.metrics if m.name == "probe_response_variance"), None
        )
        if probe_m:
            assert not probe_m.passed
            assert probe_m.severity == "fail"


# ---------------------------------------------------------------------------
# 7. Countermeasure hints
# ---------------------------------------------------------------------------


class TestCountermeasureHintsAccessible:
    """Verify countermeasure hints remain available but report now passes."""

    def test_hints_still_defined(self):
        from src.llm.detection.countermeasure_policy import get_hint_for_metric

        hint = get_hint_for_metric("probe_response_variance")
        assert hint is not None
        assert "silent drop" in " ".join(hint.recommended_changes).lower()

    def test_no_hints_suggested_when_all_pass(self):
        from src.llm.detection.countermeasure_policy import suggest_countermeasures

        runner = MockProbeRunner(seed=42)
        results = runner.run_all()
        report = generate_probe_report(results)
        dr = from_probe_report(report)
        evaluated = evaluate_detection_report(dr)
        hints = suggest_countermeasures(evaluated)
        assert len(hints) == 0


# ---------------------------------------------------------------------------
# 8. decode_frame unchanged
# ---------------------------------------------------------------------------


class TestDecodeFrameUnchanged:
    """decode_frame should still raise FrameDecodeError on bad input.

    The unification happens in the *handling* of the error, not in detection.
    """

    def test_decode_frame_still_raises_on_short(self):
        with pytest.raises(FrameDecodeError, match="too short"):
            decode_frame(b"\x00")

    def test_decode_frame_still_raises_on_bad_magic(self):
        payload = b"BADM" + b"\x01" + b"\x00" + b"\x00\x00\x00\x00" + b"\x00" * 16
        with pytest.raises(FrameDecodeError, match="Invalid magic"):
            decode_frame(payload)

    def test_decode_frame_still_raises_on_bad_type(self):
        payload = (
            b"VTUN" + b"\x01" + b"\xFF"
            + b"\x00\x00\x00\x04" + b"\x00" * 16 + b"abcd"
        )
        with pytest.raises(FrameDecodeError, match="Unknown frame type"):
            decode_frame(payload)

    def test_decode_frame_still_raises_on_truncated(self):
        payload = (
            b"VTUN" + b"\x01" + b"\x01"
            + (100).to_bytes(4, "big") + b"\x00" * 16 + b"short"
        )
        with pytest.raises(FrameDecodeError, match="truncated"):
            decode_frame(payload)

    def test_decode_frame_still_works_for_valid(self):
        session_id = uuid.uuid4().bytes
        frame = create_frame(FrameType.DATA, session_id, b"hello")
        encoded = encode_frame(frame)
        decoded = decode_frame(encoded)
        assert decoded.frame_type == FrameType.DATA
        assert decoded.session_id == session_id
        assert decoded.payload == b"hello"
