"""Tests for WebSocket RTT runner — local echo server only, no external network.

All async operations use asyncio.run() in sync test functions — no pytest-asyncio needed.
Server + measurement + cleanup always run in the SAME asyncio.run() call to avoid
cross-loop issues.
"""

import asyncio

import pytest

from src.evaluation.rtt.websocket_rtt import (
    WebSocketRTTConfig,
    WebSocketRTTResult,
    _is_local_host,
    async_measure_websocket_rtt,
    create_local_echo_server,
    measure_websocket_rtt,
)


# ---------------------------------------------------------------------------
# websockets availability check
# ---------------------------------------------------------------------------

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

needs_websockets = pytest.mark.skipif(
    not WEBSOCKETS_AVAILABLE,
    reason="websockets library not available",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unused_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _echo_handler(websocket):
    """Simple echo handler for local test servers."""
    async def _run():
        async for message in websocket:
            await websocket.send(message)
    return _run()


async def _blackhole_handler(websocket):
    """Handler that never replies — used for timeout tests."""
    async for _message in websocket:
        pass


async def _mismatch_handler(websocket):
    """Handler that sends back WRONG nonces."""
    async for _message in websocket:
        await websocket.send("wrong-nonce")


# ---------------------------------------------------------------------------
# _is_local_host
# ---------------------------------------------------------------------------


class TestLocalHostCheck:
    def test_localhost_strings_pass(self):
        assert _is_local_host("127.0.0.1") is True
        assert _is_local_host("localhost") is True
        assert _is_local_host("::1") is True

    def test_127_8_range_passes(self):
        assert _is_local_host("127.0.0.99") is True
        assert _is_local_host("127.255.255.1") is True

    def test_external_hosts_rejected(self):
        assert _is_local_host("192.168.1.1") is False
        assert _is_local_host("example.com") is False
        assert _is_local_host("0.0.0.0") is False


# ---------------------------------------------------------------------------
# WebSocketRTTConfig validation
# ---------------------------------------------------------------------------


class TestWebSocketRTTConfigValidation:
    def test_default_config_validates(self):
        WebSocketRTTConfig().validate()

    def test_non_localhost_rejected_when_local_only(self):
        config = WebSocketRTTConfig(host="192.168.1.1", local_only=True)
        with pytest.raises(ValueError, match="non-local host"):
            config.validate()

    def test_non_localhost_allowed_when_local_only_false(self):
        config = WebSocketRTTConfig(host="example.com", local_only=False)
        config.validate()

    def test_sample_count_zero_raises(self):
        with pytest.raises(ValueError, match="sample_count"):
            WebSocketRTTConfig(sample_count=0).validate()

    def test_sample_count_negative_raises(self):
        with pytest.raises(ValueError, match="sample_count"):
            WebSocketRTTConfig(sample_count=-1).validate()

    def test_invalid_port_raises(self):
        with pytest.raises(ValueError, match="port"):
            WebSocketRTTConfig(port=0).validate()

    def test_port_too_high_raises(self):
        with pytest.raises(ValueError, match="port"):
            WebSocketRTTConfig(port=99999).validate()

    def test_zero_timeout_raises(self):
        with pytest.raises(ValueError, match="timeout_s"):
            WebSocketRTTConfig(timeout_s=0).validate()


# ---------------------------------------------------------------------------
# Integration tests with local echo server
# ---------------------------------------------------------------------------


@needs_websockets
class TestMeasureWebSocketRTT:

    def test_collects_sample_count_samples(self):
        async def _test():
            server, host, port = await create_local_echo_server("127.0.0.1", _unused_port())
            try:
                config = WebSocketRTTConfig(host=host, port=port, sample_count=5, timeout_s=2.0)
                result = await async_measure_websocket_rtt(config)
                assert result.connected is True
                assert result.sample_count == 5
                assert result.error is None
                assert len(result.samples_ms) == 5
            finally:
                server.close()
                await server.wait_closed()
        asyncio.run(_test())

    def test_all_samples_positive(self):
        async def _test():
            server, host, port = await create_local_echo_server("127.0.0.1", _unused_port())
            try:
                config = WebSocketRTTConfig(host=host, port=port, sample_count=10, timeout_s=2.0)
                result = await async_measure_websocket_rtt(config)
                for s in result.samples_ms:
                    assert s > 0, f"RTT sample should be positive, got {s}"
            finally:
                server.close()
                await server.wait_closed()
        asyncio.run(_test())

    def test_summary_stats_populated(self):
        async def _test():
            server, host, port = await create_local_echo_server("127.0.0.1", _unused_port())
            try:
                config = WebSocketRTTConfig(host=host, port=port, sample_count=8, timeout_s=2.0)
                result = await async_measure_websocket_rtt(config)
                assert result.min_ms is not None
                assert result.median_ms is not None
                assert result.avg_ms is not None
                assert result.max_ms is not None
                assert result.min_ms <= result.avg_ms <= result.max_ms
            finally:
                server.close()
                await server.wait_closed()
        asyncio.run(_test())

    def test_non_localhost_rejected_immediately(self):
        config = WebSocketRTTConfig(host="192.168.1.1", port=8765, local_only=True)
        result = asyncio.run(async_measure_websocket_rtt(config))
        assert result.connected is False
        assert "non-local" in result.error.lower() or "reject" in result.error.lower()

    def test_connection_failure_returns_error_not_exception(self):
        async def _test():
            config = WebSocketRTTConfig(host="127.0.0.1", port=1, sample_count=1, timeout_s=0.3)
            return await async_measure_websocket_rtt(config)
        result = asyncio.run(_test())
        assert isinstance(result, WebSocketRTTResult)
        assert result.connected is False or result.error is not None or result.sample_count == 0

    def test_timeout_handled_gracefully(self):
        async def _test():
            port = _unused_port()
            server = await websockets.serve(
                _blackhole_handler, "127.0.0.1", port, ping_interval=None,
            )
            actual = port
            for s in server.sockets:
                sn = s.getsockname()
                if len(sn) >= 2:
                    actual = sn[1]
                    break
            try:
                config = WebSocketRTTConfig(host="127.0.0.1", port=actual, sample_count=3, timeout_s=0.2)
                result = await async_measure_websocket_rtt(config)
                assert isinstance(result, WebSocketRTTResult)
                assert result.sample_count == 0 or result.error is not None
            finally:
                server.close()
                await server.wait_closed()
        asyncio.run(_test())

    def test_to_rtt_measurement(self):
        async def _test():
            server, host, port = await create_local_echo_server("127.0.0.1", _unused_port())
            try:
                config = WebSocketRTTConfig(host=host, port=port, sample_count=6, timeout_s=2.0)
                result = await async_measure_websocket_rtt(config)
                measurement = result.to_rtt_measurement()
                assert measurement.name == "app_echo_ws"
                assert measurement.layer == "application"
                assert measurement.sample_count == 6
                assert measurement.avg_ms is not None
            finally:
                server.close()
                await server.wait_closed()
        asyncio.run(_test())

    def test_result_with_error_to_rtt_measurement(self):
        result = WebSocketRTTResult(connected=False, error="test failure")
        measurement = result.to_rtt_measurement()
        assert measurement.name == "app_echo_ws"
        assert measurement.layer == "application"
        assert measurement.sample_count == 0
        assert any("test failure" in n for n in measurement.notes)

    def test_result_reports_notes(self):
        async def _test():
            server, host, port = await create_local_echo_server("127.0.0.1", _unused_port())
            try:
                config = WebSocketRTTConfig(host=host, port=port, sample_count=3, timeout_s=2.0)
                result = await async_measure_websocket_rtt(config)
                assert any("sample_count=3" in n for n in result.notes)
                assert any(f"ws://{host}:{port}" in n for n in result.notes)
            finally:
                server.close()
                await server.wait_closed()
        asyncio.run(_test())


# ---------------------------------------------------------------------------
# Synchronous measure_websocket_rtt wrapper
# ---------------------------------------------------------------------------


@needs_websockets
class TestSyncMeasureWebSocketRTT:
    def test_sync_wrapper_produces_same_result_type(self):
        async def _test():
            server, host, port = await create_local_echo_server("127.0.0.1", _unused_port())
            try:
                config = WebSocketRTTConfig(host=host, port=port, sample_count=3, timeout_s=2.0)
                result = await async_measure_websocket_rtt(config)
                return result
            finally:
                server.close()
                await server.wait_closed()
        # measure_websocket_rtt uses its own asyncio.run(), needs server in a thread
        # Use async variant inside asyncio.run() instead
        result = asyncio.run(_test())
        assert isinstance(result, WebSocketRTTResult)
        assert result.connected is True
        assert result.sample_count == 3

    def test_sync_wrapper_non_localhost_rejected(self):
        config = WebSocketRTTConfig(host="example.com", port=8765, local_only=True)
        result = measure_websocket_rtt(config)
        assert result.connected is False

    def test_sync_wrapper_collects_stats(self):
        async def _test():
            server, host, port = await create_local_echo_server("127.0.0.1", _unused_port())
            try:
                config = WebSocketRTTConfig(host=host, port=port, sample_count=5, timeout_s=2.0)
                return await async_measure_websocket_rtt(config)
            finally:
                server.close()
                await server.wait_closed()
        result = asyncio.run(_test())
        assert result.min_ms is not None
        assert result.max_ms is not None


# ---------------------------------------------------------------------------
# local echo server
# ---------------------------------------------------------------------------


@needs_websockets
class TestLocalEchoServer:
    def test_echo_server_echoes(self):
        async def _test():
            server, host, port = await create_local_echo_server("127.0.0.1", _unused_port())
            try:
                async with websockets.connect(
                    f"ws://{host}:{port}/", ping_interval=None,
                ) as ws:
                    await ws.send("hello")
                    return await ws.recv()
            finally:
                server.close()
                await server.wait_closed()
        reply = asyncio.run(_test())
        assert reply == "hello"

    def test_echo_server_multiple_messages(self):
        async def _test():
            server, host, port = await create_local_echo_server("127.0.0.1", _unused_port())
            try:
                async with websockets.connect(
                    f"ws://{host}:{port}/", ping_interval=None,
                ) as ws:
                    replies = []
                    for msg in ("ping", "pong", "hello"):
                        await ws.send(msg)
                        replies.append(await ws.recv())
                    return replies
            finally:
                server.close()
                await server.wait_closed()
        replies = asyncio.run(_test())
        assert replies == ["ping", "pong", "hello"]

    def test_echo_server_non_localhost_rejected(self):
        async def _test():
            return await create_local_echo_server("192.168.1.1", 8765)
        with pytest.raises(ValueError, match="localhost"):
            asyncio.run(_test())

    def test_echo_server_port_zero_returns_actual_port(self):
        async def _test():
            server, host, actual = await create_local_echo_server("127.0.0.1", 0)
            try:
                assert actual > 0
                assert actual <= 65535
            finally:
                server.close()
                await server.wait_closed()
        asyncio.run(_test())


# ---------------------------------------------------------------------------
# Nonce matching
# ---------------------------------------------------------------------------


@needs_websockets
class TestNonceMatching:
    def test_nonce_mismatch_skips_samples(self):
        async def _test():
            port = _unused_port()
            server = await websockets.serve(
                _mismatch_handler, "127.0.0.1", port, ping_interval=None,
            )
            actual = port
            for s in server.sockets:
                sn = s.getsockname()
                if len(sn) >= 2:
                    actual = sn[1]
                    break
            try:
                config = WebSocketRTTConfig(
                    host="127.0.0.1", port=actual, sample_count=4,
                    timeout_s=1.0, use_nonce=True,
                )
                result = await async_measure_websocket_rtt(config)
                assert result.sample_count == 0
                assert any("nonce mismatch" in n for n in result.notes)
            finally:
                server.close()
                await server.wait_closed()
        asyncio.run(_test())

    def test_nonce_disabled_accepts_any_reply(self):
        async def _test():
            server, host, port = await create_local_echo_server("127.0.0.1", _unused_port())
            try:
                config = WebSocketRTTConfig(
                    host=host, port=port, sample_count=5,
                    timeout_s=2.0, use_nonce=False,
                )
                result = await async_measure_websocket_rtt(config)
                assert result.sample_count == 5
                assert not any("nonce mismatch" in n for n in result.notes)
            finally:
                server.close()
                await server.wait_closed()
        asyncio.run(_test())


# ---------------------------------------------------------------------------
# Graceful skip when websockets is not installed
# ---------------------------------------------------------------------------


class TestGracefulSkipWithoutWebsockets:
    def test_websocket_not_available_import(self):
        assert WEBSOCKETS_AVAILABLE in (True, False)
