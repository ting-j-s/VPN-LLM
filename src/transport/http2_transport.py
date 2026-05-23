"""HTTP/2 Transport Implementation (experimental).

Provides HTTP/2-based transport for the VPN tunnel.
Uses the ``h2`` library with a dedicated background event loop per instance.

**Status: experimental skeleton.**  The ``h2`` library is an optional
dependency.  When it is not installed, the transport class is still
importable and factory-constructable, but ``connect()`` raises a clear
``TransportError`` asking the user to install ``h2``.
"""

import asyncio
import concurrent.futures
import importlib.util
import threading
from typing import Optional

from ..common.errors import TransportError, TransportTimeout
from ..common.logger import get_logger
from .base import Transport

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Optional dependency check (module-level, evaluated once at import time)
# ---------------------------------------------------------------------------
_H2_AVAILABLE = importlib.util.find_spec("h2") is not None


class HTTP2Transport(Transport):
    """HTTP/2-based transport implementation (experimental).

    Each instance owns a dedicated asyncio event loop running in a
    background thread, following the same pattern as WebSocketTransport.

    Supports two modes:
    - Client mode: connect to HTTP/2 server
    - Server mode: bind/listen, then accept a single client connection

    .. note::
        This transport is **experimental**.  The ``h2`` Python library is
        an optional dependency.  Install it with ``pip install h2``.
        Without ``h2``, the class is constructable but ``connect()`` raises
        ``TransportError``.
    """

    MODE_CLIENT = "client"
    MODE_SERVER = "server"

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    def __init__(
        self,
        mode: str = MODE_CLIENT,
        host: str = "127.0.0.1",
        port: int = 2225,
        path: str = "/",
        backlog: int = 5,
        server_hostname: Optional[str] = None,
    ):
        if mode not in (self.MODE_CLIENT, self.MODE_SERVER):
            raise TransportError(
                f"Invalid mode: {mode}. Must be 'client' or 'server'"
            )

        self.mode = mode
        self.host = host
        self.port = port
        self.path = path
        self.backlog = backlog
        self.server_hostname = server_hostname or host

        self._connected = False
        self._shutting_down = False

        # Dedicated event loop running in a background thread
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

        # Server state
        self._server = None
        self._server_ready = threading.Event()
        self._connection_event = threading.Event()
        self._setup_error: Optional[Exception] = None
        self._loop_ready = threading.Event()

        # h2 connection handle (populated after connect)
        self._h2_conn = None
        self._writer = None
        self._reader = None

        # Queue for data received on HTTP/2 streams (decouples read loop from recv())
        self._rx_queue: asyncio.Queue = asyncio.Queue()

        # Track whether the tunnel stream is open
        self._stream_open = False
        self._stream_ended = False

    # ------------------------------------------------------------------
    # Event-loop management (same pattern as WebSocketTransport)
    # ------------------------------------------------------------------

    def _ensure_loop(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            return
        if self._shutting_down:
            raise TransportError("Transport is shutting down")

        self._loop_ready.clear()
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop, daemon=True, name="h2-transport-loop"
        )
        self._loop_thread.start()
        if not self._loop_ready.wait(timeout=5.0):
            raise TransportError("Event loop failed to start")

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._loop_ready.set)
        self._loop.run_forever()
        try:
            pending = asyncio.all_tasks(self._loop)
            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except Exception:
            pass
        self._loop.close()

    def _run_coro(self, coro, timeout: Optional[float] = None):
        if self._shutting_down:
            raise TransportError("Transport is shutting down")
        self._ensure_loop()
        if self._loop is None or self._loop.is_closed():
            raise TransportError("Event loop is not available")

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TransportTimeout("Operation timed out")

    # ------------------------------------------------------------------
    # Transport interface
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Establish HTTP/2 connection.

        Raises:
            TransportError: If ``h2`` is not installed, or connection fails.
        """
        if self._connected:
            logger.warning("Already connected")
            return
        if self._shutting_down:
            raise TransportError("Transport is shutting down")

        if not _H2_AVAILABLE:
            raise TransportError(
                "HTTP/2 transport requires the 'h2' library. "
                "Install it with: pip install h2"
            )

        if self.mode == self.MODE_CLIENT:
            self._connect_client()
        else:
            self._start_server()

    def _connect_client(self) -> None:
        logger.info("HTTP/2 client connecting to %s:%d%s",
                     self.host, self.port, self.path)
        self._ensure_loop()
        self._setup_error = None
        self._connection_event.clear()

        asyncio.run_coroutine_threadsafe(
            self._async_connect_client(), self._loop
        )

        if not self._connection_event.wait(timeout=10.0):
            raise TransportError("HTTP/2 connection timed out")

        if self._setup_error:
            err = self._setup_error
            self._setup_error = None
            raise TransportError(f"HTTP/2 connection failed: {err}")

    async def _async_connect_client(self) -> None:
        import h2.connection
        import h2.events
        import h2.config

        try:
            reader, writer = await asyncio.open_connection(
                self.host, self.port
            )
            config = h2.config.H2Configuration(
                client_side=True,
                header_encoding='utf-8',
            )
            conn = h2.connection.H2Connection(config=config)
            conn.initiate_connection()
            writer.write(conn.data_to_send())

            # Read server preface + SETTINGS
            data = await asyncio.wait_for(reader.read(65535), timeout=5.0)
            events = conn.receive_data(data)
            for event in events:
                if isinstance(event, h2.events.RemoteSettingsChanged):
                    logger.debug("Client received server SETTINGS")
            out = conn.data_to_send()
            if out:
                writer.write(out)

            # Open stream 1 with HEADERS (required before sending DATA)
            headers = [
                (':method', 'POST'),
                (':path', self.path),
                (':scheme', 'http'),
                (':authority', f'{self.server_hostname}:{self.port}'),
            ]
            conn.send_headers(stream_id=1, headers=headers, end_stream=False)
            writer.write(conn.data_to_send())

            self._h2_conn = conn
            self._reader = reader
            self._writer = writer
            self._connected = True
            self._connection_event.set()
            logger.info("HTTP/2 client connected")

            # Read loop — dispatches events, queues data for recv()
            await self._read_loop(reader, conn, writer)
        except Exception as e:
            self._setup_error = e
            self._connection_event.set()
        finally:
            self._connected = False
            self._stream_open = False
            logger.debug("HTTP/2 client connection closed")

    def _start_server(self) -> None:
        logger.info("HTTP/2 server binding to %s:%d", self.host, self.port)
        self._server_ready.clear()
        self._setup_error = None
        self._ensure_loop()

        asyncio.run_coroutine_threadsafe(
            self._async_start_server(), self._loop
        )

        if not self._server_ready.wait(timeout=5.0):
            raise TransportError("HTTP/2 server start timed out")

        if self._setup_error:
            err = self._setup_error
            self._setup_error = None
            raise TransportError(f"HTTP/2 server failed: {err}")

        logger.info("HTTP/2 server started on %s:%d", self.host, self.port)

    async def _async_start_server(self) -> None:
        import h2.connection
        import h2.events
        import h2.config

        try:
            server = await asyncio.start_server(
                self._handle_connection,
                self.host,
                self.port,
                backlog=self.backlog,
            )
            self._server = server
        except Exception as e:
            self._setup_error = e
            self._server_ready.set()
            return

        self._server_ready.set()
        logger.info("HTTP/2 server listening on %s:%d", self.host, self.port)

    async def _handle_connection(self, reader, writer) -> None:
        import h2.connection
        import h2.events
        import h2.config

        if self._h2_conn is not None:
            writer.close()
            return

        config = h2.config.H2Configuration(
            client_side=False,
            header_encoding='utf-8',
        )
        conn = h2.connection.H2Connection(config=config)
        conn.initiate_connection()
        writer.write(conn.data_to_send())

        self._h2_conn = conn
        self._reader = reader
        self._writer = writer
        self._connected = True
        self._connection_event.set()
        logger.info("HTTP/2 connection accepted")

        try:
            await self._read_loop(reader, conn, writer)
        finally:
            self._connected = False
            self._stream_open = False
            logger.debug("HTTP/2 server connection closed")

    async def _read_loop(self, reader, conn, writer) -> None:
        """Shared read loop: dispatch events, queue DataReceived for recv()."""
        import h2.connection as _h2_conn_mod
        import h2.events
        import h2.config

        while True:
            try:
                data = await asyncio.wait_for(reader.read(65535), timeout=30.0)
            except asyncio.TimeoutError:
                continue
            if not data:
                break
            events = conn.receive_data(data)
            for event in events:
                if isinstance(event, h2.events.DataReceived):
                    conn.acknowledge_received_data(
                        event.flow_controlled_length,
                        stream_id=event.stream_id,
                    )
                    # Increment flow control window for received data
                    conn.increment_flow_control_window(
                        event.flow_controlled_length,
                        stream_id=event.stream_id,
                    )
                    conn.increment_flow_control_window(
                        event.flow_controlled_length,
                    )
                    out = conn.data_to_send()
                    if out:
                        writer.write(out)
                    await self._rx_queue.put(event.data)
                elif isinstance(event, h2.events.ResponseReceived):
                    # Server sent :status headers — stream is open
                    self._stream_open = True
                    logger.debug("Stream 1 opened (response received)")
                elif isinstance(event, h2.events.RequestReceived):
                    # Client sent :method headers — open stream and respond
                    self._stream_open = True
                    logger.debug("Stream 1 opened (request received)")
                    response_headers = [
                        (':status', '200'),
                    ]
                    conn.send_headers(
                        stream_id=event.stream_id,
                        headers=response_headers,
                        end_stream=False,
                    )
                    out = conn.data_to_send()
                    if out:
                        writer.write(out)
                elif isinstance(event, h2.events.WindowUpdated):
                    pass  # h2 library handles flow control bookkeeping
                elif isinstance(event, h2.events.StreamEnded):
                    self._stream_ended = True
                    await self._rx_queue.put(None)
                    return
                elif isinstance(event, h2.events.ConnectionTerminated):
                    await self._rx_queue.put(None)
                    return
                elif isinstance(event, h2.events.RemoteSettingsChanged):
                    logger.debug("Remote SETTINGS changed")
                elif isinstance(event, h2.events.SettingsAcknowledged):
                    pass
                elif isinstance(event, h2.events.PingAcknowledged):
                    pass
                else:
                    logger.debug("Unhandled h2 event: %s", type(event).__name__)
            # Flush any protocol data
            out = conn.data_to_send()
            if out:
                writer.write(out)

    def accept(self, timeout: Optional[float] = None) -> None:
        """Wait for a client to connect (server mode only)."""
        if self.mode != self.MODE_SERVER:
            raise TransportError("accept() is only available in server mode")
        if self._server is None:
            raise TransportError("Server not started, call connect() first")

        logger.info("Waiting for HTTP/2 connection...")
        if not self._connected:
            self._connection_event.clear()
            if not self._connection_event.wait(timeout=timeout):
                raise TransportError("Accept timeout")

    def send(self, data: bytes) -> None:
        """Send data over an HTTP/2 stream.

        Raises:
            TransportError: If not connected or send fails.
        """
        if not self._connected or self._h2_conn is None:
            raise TransportError("Not connected")

        if not _H2_AVAILABLE:
            raise TransportError(
                "HTTP/2 transport requires the 'h2' library. "
                "Install it with: pip install h2"
            )

        try:
            self._run_coro(self._async_send(data))
        except TransportTimeout:
            raise
        except Exception as e:
            self._connected = False
            raise TransportError(f"Send failed: {e}")

    async def _async_send(self, data: bytes) -> None:
        self._h2_conn.send_data(stream_id=1, data=data, end_stream=False)
        out = self._h2_conn.data_to_send()
        if out and self._writer is not None:
            self._writer.write(out)

    def recv(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Receive data from an HTTP/2 stream.

        Returns:
            Data bytes, or None if connection closed.

        Raises:
            TransportTimeout: If timeout expires.
            TransportError: If receive fails.
        """
        if self._h2_conn is None:
            raise TransportError("Not connected")

        if not _H2_AVAILABLE:
            raise TransportError(
                "HTTP/2 transport requires the 'h2' library. "
                "Install it with: pip install h2"
            )

        try:
            result = self._run_coro(self._async_recv(timeout), timeout=timeout)
            if result is None:
                self._connected = False
            return result
        except TransportTimeout:
            raise
        except Exception as e:
            self._connected = False
            raise TransportError(f"Receive failed: {e}")

    async def _async_recv(self, timeout: Optional[float]) -> Optional[bytes]:
        """Read from the rx queue (populated by the read loop)."""
        if self._stream_ended:
            return None
        try:
            if timeout is not None and timeout > 0:
                data = await asyncio.wait_for(
                    self._rx_queue.get(), timeout=timeout
                )
            else:
                data = await self._rx_queue.get()
            return data
        except asyncio.TimeoutError:
            raise TransportTimeout("Receive timeout")

    def close(self) -> None:
        """Close the connection and stop the background event loop.

        Idempotent -- safe to call multiple times.
        """
        if self._shutting_down:
            return

        logger.info("Closing HTTP/2 transport")
        self._shutting_down = True
        self._connected = False
        self._stream_open = False
        self._server_ready.set()
        self._connection_event.set()

        loop = self._loop

        # Wake up any pending recv()
        try:
            self._rx_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

        # Close writer
        writer = self._writer
        self._writer = None
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass

        # Close server
        server = self._server
        self._server = None
        if server is not None and loop is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(
                    server.wait_closed(), loop
                ).result(timeout=3.0)
            except Exception:
                pass

        # Stop the event loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)

        if self._loop_thread is not None and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=5.0)

        self._loop = None
        self._loop_thread = None
        self._h2_conn = None
        self._reader = None
        self._rx_queue = asyncio.Queue()
        logger.info("HTTP/2 transport closed")

    def is_connected(self) -> bool:
        return self._connected

    def __repr__(self) -> str:
        return (
            f"HTTP2Transport(mode={self.mode}, host={self.host}, "
            f"port={self.port}, path={self.path}, "
            f"h2_available={_H2_AVAILABLE}, "
            f"connected={self._connected})"
        )
