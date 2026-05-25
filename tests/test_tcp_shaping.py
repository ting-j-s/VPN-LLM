"""ShapedTransport decorator unit tests.

Verifies that ShapedTransport correctly wraps any Transport with
traffic shaping — padding, aggregation, and combined pipeline.
"""

from __future__ import annotations

import random

from src.shaping.base import NoopTrafficShaper
from src.shaping.padding import MAGIC_PAD, PaddingShaper
from src.shaping.aggregation import MAGIC_AGG, AggregationShaper
from src.shaping.factory import PipelineTrafficShaper, create_traffic_shaper
from src.shaping.config import ShapingConfig
from src.transport.base import MockTransport
from src.transport.shaped_transport import ShapedTransport


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_shaped(shaper=None):
    t = MockTransport()
    t.connect()
    return ShapedTransport(t, shaper), t


def _pair(shaper=None):
    """Create a client+server ShapedTransport pair sharing two MockTransports.

    Client's send() writes into server's transport via inject(), and vice versa.
    """
    client_base = MockTransport()
    server_base = MockTransport()
    client_base.connect()
    server_base.connect()

    # Wire: client send -> server recv, server send -> client recv
    _orig_client_send = client_base.send
    _orig_server_send = server_base.send

    def _client_send(data):
        _orig_client_send(data)
        server_base.inject(data)

    def _server_send(data):
        _orig_server_send(data)
        client_base.inject(data)

    client_base.send = _client_send  # type: ignore[method-assign]
    server_base.send = _server_send  # type: ignore[method-assign]

    client = ShapedTransport(client_base, shaper)
    server = ShapedTransport(server_base, shaper)
    return client, server, client_base, server_base


# ---------------------------------------------------------------------------
# 1. Default-off: pass-through
# ---------------------------------------------------------------------------

def test_noop_shaper_is_pass_through():
    st, base = _make_shaped()
    st.send(b"hello")
    assert base.get_sent() == [b"hello"]

    base.inject(b"world")
    assert st.recv() == b"world"


def test_noop_shaper_roundtrip():
    client, server, cb, sb = _pair()
    client.send(b"ping")
    assert server.recv(timeout=1) == b"ping"
    server.send(b"pong")
    assert client.recv(timeout=1) == b"pong"


# ---------------------------------------------------------------------------
# 2. Padding roundtrip
# ---------------------------------------------------------------------------

def test_padding_roundtrip():
    rng = random.Random(42)
    shaper = PaddingShaper(min_padding_bytes=4, max_padding_bytes=8, rng=rng)
    client, server, cb, sb = _pair(shaper)

    client.send(b"hello-padding")
    raw = server.recv(timeout=1)
    assert raw == b"hello-padding"


def test_padding_envelope_on_wire():
    rng = random.Random(42)
    shaper = PaddingShaper(min_padding_bytes=4, max_padding_bytes=8, rng=rng)
    client, server, cb, sb = _pair(shaper)

    client.send(b"test")
    assert len(cb.get_sent()) == 1
    assert cb.get_sent()[0][:4] == MAGIC_PAD


def test_padding_disabled_is_pass_through():
    rng = random.Random(42)
    shaper = PaddingShaper(min_padding_bytes=4, max_padding_bytes=8, rng=rng, enabled=False)
    client, server, cb, sb = _pair(shaper)

    client.send(b"test")
    assert cb.get_sent()[0] == b"test"
    assert server.recv(timeout=1) == b"test"


# ---------------------------------------------------------------------------
# 3. Aggregation roundtrip
# ---------------------------------------------------------------------------

def test_aggregation_flush_roundtrip():
    rng = random.Random(42)
    shaper = AggregationShaper(max_bytes=4096, rng=rng, enabled=True)
    client, server, cb, sb = _pair(shaper)

    # Send two frames — both buffered
    client.send(b"frame-1")
    client.send(b"frame-2")
    assert len(cb.get_sent()) == 0  # still buffered

    client.flush()
    assert len(cb.get_sent()) == 1
    assert cb.get_sent()[0][:4] == MAGIC_AGG

    assert server.recv(timeout=1) == b"frame-1"
    assert server.recv(timeout=1) == b"frame-2"


def test_aggregation_disabled_is_pass_through():
    rng = random.Random(42)
    shaper = AggregationShaper(max_bytes=4096, rng=rng, enabled=False)
    client, server, cb, sb = _pair(shaper)

    client.send(b"test")
    assert cb.get_sent()[0] == b"test"
    assert server.recv(timeout=1) == b"test"


# ---------------------------------------------------------------------------
# 4. Combined pipeline (aggregation + padding)
# ---------------------------------------------------------------------------

def test_pipeline_roundtrip():
    config = ShapingConfig(
        enabled=True,
        aggregation_enabled=True,
        aggregation_max_bytes=4096,
        padding_enabled=True,
        min_padding_bytes=4,
        max_padding_bytes=8,
    )
    shaper = create_traffic_shaper(config, seed=42)
    client, server, cb, sb = _pair(shaper)

    client.send(b"alpha")
    client.send(b"beta")
    client.flush()

    assert server.recv(timeout=1) == b"alpha"
    assert server.recv(timeout=1) == b"beta"


def test_pipeline_single_frame():
    config = ShapingConfig(
        enabled=True,
        aggregation_enabled=True,
        aggregation_max_bytes=4096,
        padding_enabled=True,
        min_padding_bytes=4,
        max_padding_bytes=4,
    )
    shaper = create_traffic_shaper(config, seed=42)
    client, server, cb, sb = _pair(shaper)

    client.send(b"single")
    client.flush()
    assert server.recv(timeout=1) == b"single"


# ---------------------------------------------------------------------------
# 5. Lifecycle
# ---------------------------------------------------------------------------

def test_send_before_connect_raises():
    from src.common.errors import TransportError
    import pytest
    st = ShapedTransport(MockTransport())
    with pytest.raises(TransportError, match="Not connected"):
        st.send(b"data")


def test_close_idempotent():
    st, base = _make_shaped()
    st.close()
    st.close()
    st.close()


def test_flush_after_close_no_error():
    st, base = _make_shaped()
    st.close()
    st.flush()  # should not raise (flush during close failed is debug-logged)


def test_shaper_property():
    rng = random.Random(42)
    shaper = PaddingShaper(min_padding_bytes=4, max_padding_bytes=8, rng=rng)
    st, base = _make_shaped(shaper)
    assert st.shaper is shaper
