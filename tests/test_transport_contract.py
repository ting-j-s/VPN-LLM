"""Transport contract tests — mandatory for every transport implementation.

These tests encode the bugs found during SOCKS5 transport implementation
so they can never recur.  Every new transport MUST pass every test in
this file before the LLM pipeline considers the patch valid.

How to add a new transport:
    1. Write a ``_make_<name>(port)`` function
    2. Register it in ``TRANSPORT_PAIRS`` below
    3. Run these tests — they all get the new transport automatically
"""

import threading
import socket
import pytest

from src.common.errors import TransportError, TransportTimeout

# === helpers ================================================================

def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _do_accept(transport, results):
    try:
        transport.accept(timeout=5)
    except Exception as e:
        results["error"] = e


def _setup_pair(make_server, make_client, port):
    """Start server, connect client, wait for accept.  Returns (server, client)."""
    server = make_server(port)
    server.connect()

    results = {"error": None}
    t = threading.Thread(target=_do_accept, args=(server, results), daemon=True)
    t.start()

    client = make_client(port)
    client.connect()

    t.join(timeout=5)
    if results["error"] is not None:
        client.close()
        server.close()
        raise results["error"]
    return server, client


# === transport factories ====================================================

def _make_tcp_server(port):
    from src.transport.tcp_transport import TCPTransport
    return TCPTransport(mode="server", host="127.0.0.1", port=port)


def _make_tcp_client(port):
    from src.transport.tcp_transport import TCPTransport
    return TCPTransport(mode="client", host="127.0.0.1", port=port)


def _make_socks5_server(port):
    from src.transport.socks5_transport import Socks5Transport
    return Socks5Transport(
        mode=Socks5Transport.MODE_SERVER,
        listen_host="127.0.0.1",
        listen_port=port,
    )


def _make_socks5_client(port):
    from src.transport.socks5_transport import Socks5Transport
    return Socks5Transport(
        mode=Socks5Transport.MODE_CLIENT,
        proxy_host="127.0.0.1",
        proxy_port=port,
        target_host="127.0.0.1",
        target_port=0,
    )


# Register transports here — each entry auto-generates all contract tests
TRANSPORT_PAIRS = [
    pytest.param(
        "tcp", _make_tcp_server, _make_tcp_client,
        id="tcp",
    ),
    pytest.param(
        "socks5", _make_socks5_server, _make_socks5_client,
        id="socks5",
    ),
]


# ============================================================================
# Category A — exception hygiene (Bug #1, #3)
# ============================================================================

# Bug #1: ConnectionRefusedError must be wrapped in TransportError
@pytest.mark.parametrize("name,make_server,make_client", TRANSPORT_PAIRS)
def test_connect_to_dead_port_raises_transport_error(name, make_server, make_client):
    """connect() to a port with no listener → TransportError, never raw OSError."""
    dead_port = _free_port()
    client = make_client(dead_port)
    with pytest.raises(TransportError):
        client.connect()
    assert not client.is_connected()


# Bug #3: socket.timeout must propagate to TransportTimeout (not swallowed)
@pytest.mark.parametrize("name,make_server,make_client", TRANSPORT_PAIRS)
def test_recv_timeout_raises_transport_timeout(name, make_server, make_client):
    """recv(timeout=0.1) on idle connection → TransportTimeout, never None."""
    port = _free_port()
    server, client = _setup_pair(make_server, make_client, port)
    try:
        with pytest.raises(TransportTimeout):
            client.recv(timeout=0.1)
        # connection still alive after timeout
        assert client.is_connected()
    finally:
        client.close()
        server.close()


# ============================================================================
# Category B — Optional[int] boundary safety (Bug #2)
# ============================================================================

# Bug #2: not 0 is True — ports/lengths must use "is None", never "not x"
@pytest.mark.parametrize("name,make_server,make_client", TRANSPORT_PAIRS)
def test_zero_handling_no_falsy_trap(name, make_server, make_client):
    """Constructors with Optional[int] fields must not reject the value 0
    as falsy.  This is verified implicitly for client mode via the zero
    target_port in the SOCKS5 factory; the generic check is that making
    a valid client at a real port succeeds."""
    port = _free_port()
    server = make_server(port)
    server.connect()
    results = {"error": None}
    t = threading.Thread(target=_do_accept, args=(server, results), daemon=True)
    t.start()
    try:
        client = make_client(port)
        client.connect()
        t.join(timeout=5)
        assert results["error"] is None, f"accept failed: {results['error']}"
        assert client.is_connected()
        client.close()
    finally:
        server.close()


# ============================================================================
# Category C — lifecycle safety
# ============================================================================

@pytest.mark.parametrize("name,make_server,make_client", TRANSPORT_PAIRS)
def test_send_before_connect_raises_transport_error(name, make_server, make_client):
    """send() on a transport that hasn't connected → TransportError."""
    port = _free_port()
    t = make_client(port)
    try:
        with pytest.raises(TransportError, match="Not connected"):
            t.send(b"data")
    finally:
        t.close()


@pytest.mark.parametrize("name,make_server,make_client", TRANSPORT_PAIRS)
def test_recv_before_connect_raises_transport_error(name, make_server, make_client):
    """recv() on a transport that hasn't connected → TransportError."""
    port = _free_port()
    t = make_client(port)
    try:
        with pytest.raises(TransportError, match="Not connected"):
            t.recv()
    finally:
        t.close()


@pytest.mark.parametrize("name,make_server,make_client", TRANSPORT_PAIRS)
def test_close_idempotent(name, make_server, make_client):
    """close() can be called any number of times, even when not connected."""
    port = _free_port()
    t = make_client(port)
    t.close()
    t.close()
    t.close()


@pytest.mark.parametrize("name,make_server,make_client", TRANSPORT_PAIRS)
def test_connect_twice_safe(name, make_server, make_client):
    """connect() twice is safe — second call is no-op or error, never crash."""
    port = _free_port()
    server = make_server(port)
    server.connect()
    results = {"error": None}
    t = threading.Thread(target=_do_accept, args=(server, results), daemon=True)
    t.start()
    client = make_client(port)
    try:
        client.connect()
        t.join(timeout=5)
        # second connect should not crash
        client.connect()
    finally:
        client.close()
        server.close()


# ============================================================================
# Category D — data integrity (roundtrip)
# ============================================================================

@pytest.mark.parametrize("name,make_server,make_client", TRANSPORT_PAIRS)
def test_roundtrip_small_payload(name, make_server, make_client):
    """Client→Server→Client small payload roundtrip."""
    port = _free_port()
    server, client = _setup_pair(make_server, make_client, port)
    try:
        payload = b"hello from " + name.encode()
        client.send(payload)
        received = server.recv(timeout=3)
        assert received == payload, f"Expected {payload}, got {received}"

        reply = b"ack from " + name.encode()
        server.send(reply)
        echoed = client.recv(timeout=3)
        assert echoed == reply, f"Expected {reply}, got {echoed}"
    finally:
        client.close()
        server.close()


@pytest.mark.parametrize("name,make_server,make_client", TRANSPORT_PAIRS)
def test_roundtrip_64k_payload(name, make_server, make_client):
    """Client→Server→Client 64KB payload roundtrip (stress boundary)."""
    port = _free_port()
    server, client = _setup_pair(make_server, make_client, port)
    try:
        big = b"X" * 65536
        client.send(big)
        received = server.recv(timeout=5)
        assert len(received) == 65536
        assert received == big

        server.send(big)
        echoed = client.recv(timeout=5)
        assert len(echoed) == 65536
        assert echoed == big
    finally:
        client.close()
        server.close()


# ============================================================================
# Category E — factory integration (Bug #4, #5)
# ============================================================================

# Bug #4: --transport choices matches what the factory accepts
@pytest.mark.parametrize("name,make_server,make_client", TRANSPORT_PAIRS)
def test_factory_creates_correct_type(name, make_server, make_client):
    """create_transport with type=<name> returns the correct Transport subclass."""
    from src.transport.factory import create_transport
    from src.common.config import ClientConfig

    config = ClientConfig()
    config.transport.type = name
    config.server.host = "127.0.0.1"
    config.server.port = _free_port()

    t = create_transport(config)
    assert t is not None
    # verify it's the expected type by class name
    assert name.lower() in type(t).__name__.lower(), (
        f"Factory returned {type(t).__name__}, expected {name}"
    )
    t.close()


def test_factory_supported_types_vs_cli_choices():
    """Every transport type the factory supports must be selectable via --transport."""
    import re
    from src.transport.factory import SUPPORTED_TRANSPORTS
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src"
    for entry_point in ("server.py", "client.py"):
        text = (root / entry_point).read_text()
        m = re.search(r'choices=\[(.*?)\]', text)
        assert m is not None, f"Could not find choices in {entry_point}"
        # extract quoted strings
        cli_choices = set(re.findall(r'"([^"]+)"', m.group(1)))
        missing = SUPPORTED_TRANSPORTS - cli_choices
        assert not missing, (
            f"{entry_point} --transport choices missing: {missing}. "
            f"Add them to the choices= list."
        )


# Bug #5: YAML config fields must be propagated to TransportConfig
def test_transport_config_vs_load_functions():
    """Every field in TransportConfig must be deserialized in load_*_config."""
    from src.common.config import TransportConfig, load_client_config, load_server_config
    import inspect
    import tempfile
    import os
    import yaml

    # collect field names from dataclass
    dc_fields = {f.name for f in TransportConfig.__dataclass_fields__.values()}

    # Build a temporary YAML with ALL transport fields set to non-default detectable values
    transport_data = {}
    for f_name in dc_fields:
        if f_name == "type":
            transport_data[f_name] = "tcp"
        elif f_name in ("socks5_proxy_host", "server_hostname"):
            transport_data[f_name] = "test.example.com"
        elif f_name in ("socks5_username", "socks5_password"):
            transport_data[f_name] = "test_value"
        elif f_name in ("certfile", "keyfile", "cafile", "path"):
            transport_data[f_name] = "/test/path"
        elif f_name == "socks5_proxy_port":
            transport_data[f_name] = 9999
        elif f_name == "http2_chunk_rng_seed" or f_name == "http2_stream_rng_seed":
            transport_data[f_name] = 12345
        elif f_name == "http2_settings_rng_seed":
            transport_data[f_name] = 42
        elif f_name in ("http2_stream_assignment", "http2_settings_profile"):
            transport_data[f_name] = "test"
        elif isinstance(TransportConfig.__dataclass_fields__[f_name].default, bool):
            transport_data[f_name] = True
        elif isinstance(TransportConfig.__dataclass_fields__[f_name].default, int):
            transport_data[f_name] = 9999
        elif TransportConfig.__dataclass_fields__[f_name].default is not None:
            transport_data[f_name] = "test"

    client_yaml = {
        "client": {},
        "server": {"host": "127.0.0.1", "port": 2222},
        "transport": transport_data,
        "session": {},
    }
    server_yaml = {
        "server": {"listen_port": 2222},
        "transport": transport_data,
        "session": {},
    }

    tmpdir = tempfile.mkdtemp()
    try:
        client_path = os.path.join(tmpdir, "client.yaml")
        server_path = os.path.join(tmpdir, "server.yaml")
        with open(client_path, "w") as f:
            yaml.dump(client_yaml, f)
        with open(server_path, "w") as f:
            yaml.dump(server_yaml, f)

        client_cfg = load_client_config(client_path)
        server_cfg = load_server_config(server_path)

        for f_name in dc_fields:
            if f_name in transport_data:
                expected = transport_data[f_name]
                client_val = getattr(client_cfg.transport, f_name)
                server_val = getattr(server_cfg.transport, f_name)
                assert client_val == expected, (
                    f"client: transport.{f_name}={client_val!r}, "
                    f"expected {expected!r} — load_client_config may not read this field"
                )
                assert server_val == expected, (
                    f"server: transport.{f_name}={server_val!r}, "
                    f"expected {expected!r} — load_server_config may not read this field"
                )
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
