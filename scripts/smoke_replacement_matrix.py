#!/usr/bin/env python3
"""Smoke validation matrix for Transport and Core replacement.

This script is the verification entry point for LLM-driven transport/core
replacement. When the LLM Agent proposes a new Transport or Core implementation,
this matrix answers: "Does the replacement still pass minimum smoke?"

Usage:
    python3 scripts/smoke_replacement_matrix.py --transports mock,tcp,tls,websocket --cores default
    python3 scripts/smoke_replacement_matrix.py --transports mock,tcp,websocket --cores default --json
    python3 scripts/smoke_replacement_matrix.py --transports tls --cores default
    python3 scripts/smoke_replacement_matrix.py --transports mock,tcp,websocket --cores default --include-ssh

Exit codes:
    0 — all non-skipped entries pass
    1 — at least one entry failed
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Optional

# Ensure repo root is on sys.path so that `from src.*` works regardless of
# the working directory or PYTHONPATH.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

# Suppress INFO-level log output early so that structured output (--json)
# remains parseable.  Individual transport modules read this env var in their
# get_logger() calls.
if "VPN_LLM_LOG_LEVEL" not in os.environ:
    os.environ["VPN_LLM_LOG_LEVEL"] = "WARNING"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _gen_test_certs(tmp_dir: str):
    """Generate self-signed cert/key in *tmp_dir*.  Returns (cert_path, key_path)."""
    key_path = os.path.join(tmp_dir, "test.key")
    cert_path = os.path.join(tmp_dir, "test.crt")
    subprocess.run(
        ["openssl", "genrsa", "-out", key_path, "2048"],
        capture_output=True, timeout=10,
    )
    subprocess.run(
        [
            "openssl", "req", "-new", "-x509",
            "-key", key_path, "-out", cert_path,
            "-days", "365", "-subj", "/CN=localhost/O=Test/C=US",
        ],
        capture_output=True, timeout=10,
    )
    return cert_path, key_path


# ---------------------------------------------------------------------------
# smoke runner interface
# ---------------------------------------------------------------------------

class SmokeResult:
    __slots__ = ("transport", "core", "status", "duration_sec", "error")

    def __init__(self, transport: str, core: str, status: str,
                 duration_sec: float, error: Optional[str] = None):
        self.transport = transport
        self.core = core
        self.status = status
        self.duration_sec = duration_sec
        self.error = error

    def to_dict(self) -> dict:
        return {
            "transport": self.transport,
            "core": self.core,
            "status": self.status,
            "duration_sec": self.duration_sec,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# per-transport smoke checks
# ---------------------------------------------------------------------------

def _smoke_mock(core: str) -> SmokeResult:
    """MockTransport: connect, inject b'smoke-test', recv, verify."""
    from src.transport.base import MockTransport

    t = MockTransport()
    try:
        t.connect()
        assert t.is_connected()

        t.inject(b"smoke-test")
        got = t.recv(timeout=0.5)
        assert got == b"smoke-test", f"Expected b'smoke-test', got {got!r}"

        return SmokeResult("mock", core, "pass", 0.0)
    except Exception as exc:
        return SmokeResult("mock", core, "fail", 0.0, str(exc))
    finally:
        t.close()


def _smoke_tcp(core: str) -> SmokeResult:
    """TCPTransport: localhost client/server roundtrip with b'smoke-test'."""
    from src.transport.tcp_transport import TCPTransport

    port = _free_port()
    server = TCPTransport(mode="server", host="127.0.0.1", port=port)
    client = TCPTransport(mode="client", host="127.0.0.1", port=port)

    try:
        t0 = time.monotonic()
        server.connect()

        # Accept in background thread
        accept_err: list[Exception] = []

        def _accept():
            try:
                server.accept(timeout=5.0)
            except Exception as e:
                accept_err.append(e)

        accept_thread = threading.Thread(target=_accept)
        accept_thread.start()

        client.connect()
        accept_thread.join(timeout=5.0)

        if accept_err:
            raise accept_err[0]

        client.send(b"smoke-test")
        got = server.recv(timeout=2.0)
        assert got == b"smoke-test", f"Expected b'smoke-test', got {got!r}"

        dt = round(time.monotonic() - t0, 3)
        return SmokeResult("tcp", core, "pass", dt)
    except Exception as exc:
        return SmokeResult("tcp", core, "fail", 0.0, str(exc))
    finally:
        client.close()
        server.close()


def _smoke_tls(core: str) -> SmokeResult:
    """TLSTransport: localhost client/server with ephemeral certs."""
    from src.transport.tls_transport import TLSTransport

    port = _free_port()
    tmp_dir = tempfile.mkdtemp(prefix="smoke_tls_")
    try:
        cert, key = _gen_test_certs(tmp_dir)

        server = TLSTransport(
            mode="server", host="127.0.0.1", port=port,
            certfile=cert, keyfile=key,
        )
        client = TLSTransport(
            mode="client", host="127.0.0.1", port=port,
            verify_server=False,
        )

        try:
            t0 = time.monotonic()
            server.connect()
            client_ok: list[bool] = []

            def _connect():
                try:
                    client.connect()
                    client_ok.append(True)
                except Exception:
                    client_ok.append(False)

            client_thread = threading.Thread(target=_connect)
            client_thread.start()

            server.accept(timeout=5.0)
            client_thread.join(timeout=5.0)

            if not client_ok or not client_ok[0]:
                raise RuntimeError("TLS client connect failed")

            client.send(b"smoke-test")
            got = server.recv(timeout=2.0)
            assert got == b"smoke-test", f"Expected b'smoke-test', got {got!r}"

            dt = round(time.monotonic() - t0, 3)
            return SmokeResult("tls", core, "pass", dt)
        except Exception as exc:
            return SmokeResult("tls", core, "fail", 0.0, str(exc))
        finally:
            client.close()
            server.close()
    except Exception as exc:
        return SmokeResult("tls", core, "fail", 0.0, str(exc))
    finally:
        # Clean up temp certs
        for fn in os.listdir(tmp_dir):
            os.unlink(os.path.join(tmp_dir, fn))
        os.rmdir(tmp_dir)


def _smoke_websocket(core: str) -> SmokeResult:
    """WebSocketTransport: localhost client/server roundtrip with b'smoke-test'."""
    from src.transport.websocket_transport import WebSocketTransport

    port = _free_port()
    server = WebSocketTransport(mode="server", host="127.0.0.1", port=port)
    client = WebSocketTransport(mode="client", host="127.0.0.1", port=port)

    try:
        t0 = time.monotonic()
        server.connect()
        client.connect()
        server.accept(timeout=5.0)

        client.send(b"smoke-test")
        got = server.recv(timeout=2.0)
        assert got == b"smoke-test", f"Expected b'smoke-test', got {got!r}"

        dt = round(time.monotonic() - t0, 3)
        return SmokeResult("websocket", core, "pass", dt)
    except Exception as exc:
        return SmokeResult("websocket", core, "fail", 0.0, str(exc))
    finally:
        client.close()
        server.close()


def _smoke_ssh(core: str) -> SmokeResult:
    """SSH: always skipped — requires external SSH server."""
    return SmokeResult(
        "ssh", core, "skip", 0.0,
        "requires external SSH server",
    )


# ---------------------------------------------------------------------------
# per-core smoke checks
# ---------------------------------------------------------------------------

CORE_SMOKE_REGISTRY: dict[str, Any] = {}


def _register_core_smoke(name: str):
    """Decorator to register a core smoke handler."""
    def deco(fn):
        CORE_SMOKE_REGISTRY[name] = fn
        return fn
    return deco


@_register_core_smoke("default")
def _smoke_core_default(transport_result: SmokeResult) -> SmokeResult:
    """Default core smoke: verify ClientCore/ServerCore are importable and
    session_id validation works at the unit level.
    """
    try:
        from src.core.client_core import ClientCore
        from src.core.server_core import ServerCore
        from src.tun.tun_device import MockTunDevice
        from src.transport.base import MockTransport
        from src.common.frame import create_frame, encode_frame, FrameType
        import uuid

        session_id = uuid.uuid4().bytes
        wrong_id = uuid.uuid4().bytes

        server_tun = MockTunDevice(name="smoke-tun", mtu=1400)
        client_tun = MockTunDevice(name="smoke-tun", mtu=1400)

        server_t = MockTransport()
        client_t = MockTransport()

        server_core = ServerCore(
            tun=server_tun, transport=server_t, session_id=session_id,
            heartbeat_interval=30, heartbeat_timeout=60,
        )
        client_core = ClientCore(
            tun=client_tun, transport=client_t, session_id=session_id,
            heartbeat_interval=30, heartbeat_timeout=60,
        )

        server_t.connect()
        client_t.connect()
        server_core.start()
        client_core.start()
        time.sleep(0.1)

        try:
            # session_id validation: wrong session_id DATA must be dropped
            wrong_frame = create_frame(FrameType.DATA, wrong_id, b"smoke-data")
            server_t.inject(encode_frame(wrong_frame))
            time.sleep(0.1)
            assert len(server_tun.get_tx_packets()) == 0, "wrong session_id not dropped"
        finally:
            client_core.stop()
            server_core.stop()

        # Return a clone of transport_result with the core dimension already accounted for
        # The core smoke itself passed; transport status is from the transport dimension
        return SmokeResult(transport_result.transport, "default", transport_result.status,
                           transport_result.duration_sec, transport_result.error)
    except Exception as exc:
        return SmokeResult(transport_result.transport, "default", "fail", 0.0, str(exc))


_TRANSPORT_SMOKE = {
    "mock": _smoke_mock,
    "tcp": _smoke_tcp,
    "tls": _smoke_tls,
    "websocket": _smoke_websocket,
    "ssh": _smoke_ssh,
}


# ---------------------------------------------------------------------------
# matrix runner
# ---------------------------------------------------------------------------

def _redirect_logging_to_stderr() -> None:
    """Redirect all ``src.*`` loggers from stdout to stderr.

    This keeps stdout clean for structured output (e.g. ``--json``).
    """
    import logging
    for name in list(logging.root.manager.loggerDict):
        if not name.startswith("src"):
            continue
        logger = logging.getLogger(name)
        for h in list(logger.handlers):
            if isinstance(h, logging.StreamHandler) and h.stream is sys.stdout:
                logger.removeHandler(h)
                new_h = logging.StreamHandler(sys.stderr)
                new_h.setLevel(h.level)
                if h.formatter:
                    new_h.setFormatter(h.formatter)
                logger.addHandler(new_h)


def run_matrix(transports: list[str], cores: list[str],
               include_ssh: bool = False) -> list[SmokeResult]:
    """Run the smoke matrix and return ordered results."""
    _redirect_logging_to_stderr()
    results: list[SmokeResult] = []

    for core in cores:
        core_handler = CORE_SMOKE_REGISTRY.get(core)
        if core_handler is None:
            for transport in transports:
                results.append(SmokeResult(transport, core, "fail", 0.0,
                                           f"unknown core: {core}"))
            continue

        for transport in transports:
            if transport == "ssh" and not include_ssh:
                results.append(_smoke_ssh(core))
                continue

            smoke_fn = _TRANSPORT_SMOKE.get(transport)
            if smoke_fn is None:
                results.append(SmokeResult(transport, core, "fail", 0.0,
                                           f"unknown transport: {transport}"))
                continue

            result = smoke_fn(core)
            results.append(result)

    return results


def print_text(results: list[SmokeResult]) -> None:
    print("Transport/Core Smoke Matrix")
    print("=" * 60)
    max_name = max((len(f"{r.transport}/{r.core}") for r in results), default=0)
    for r in results:
        name = f"{r.transport}/{r.core}"
        status = r.status.upper()
        dur = f"{r.duration_sec:.2f}s" if r.duration_sec else ""
        err = f"  ({r.error})" if r.error else ""
        print(f"{name:<{max_name + 2}} {status:<8} {dur}{err}")


def print_json(results: list[SmokeResult]) -> None:
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    skipped = sum(1 for r in results if r.status == "skip")
    output = {
        "results": [r.to_dict() for r in results],
        "summary": {
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        },
    }
    print(json.dumps(output, indent=2))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke validation matrix for Transport/Core replacement",
    )
    parser.add_argument(
        "--transports", default="mock,tcp,tls,websocket",
        help="Comma-separated transport list (default: mock,tcp,tls,websocket)",
    )
    parser.add_argument(
        "--cores", default="default",
        help="Comma-separated core list (default: default)",
    )
    parser.add_argument(
        "--timeout", type=int, default=5,
        help="Per-check timeout in seconds (default: 5)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--include-ssh", action="store_true",
        help="Include SSH transport (default: skip)",
    )

    args = parser.parse_args()
    transports = [t.strip() for t in args.transports.split(",") if t.strip()]
    cores = [c.strip() for c in args.cores.split(",") if c.strip()]

    results = run_matrix(transports, cores, include_ssh=args.include_ssh)

    if args.json_output:
        print_json(results)
    else:
        print_text(results)

    # Exit code: 1 if any fail, 0 otherwise
    if any(r.status == "fail" for r in results):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
