"""WebSocket Transport Implementation.

Provides WebSocket-based transport for the VPN tunnel.
Supports both client (connect) and server (bind/listen/accept) modes.
Uses websockets library with a dedicated background event loop per instance.
"""

import asyncio
import concurrent.futures
import threading
from typing import Optional

import websockets

from ..common.errors import TransportError, TransportTimeout
from ..common.logger import get_logger
from .base import Transport


logger = get_logger(__name__)

# Max frame size: 10MB
MAX_FRAME_SIZE = 10 * 1024 * 1024

# websockets API changed across major versions:
#   v10.x: from websockets import connect/serve, parameter=extra_headers
#   v16.x: from websockets import connect/serve, parameter=additional_headers
_WS_MAJOR = int(websockets.__version__.split(".")[0])
_HEADER_KW = "additional_headers" if _WS_MAJOR >= 16 else "extra_headers"


class WebSocketTransport(Transport):
    """WebSocket-based transport implementation.

    Each instance owns a dedicated asyncio event loop running in a
    background thread.  Sync send / recv / connect submit coroutines to
    that loop via ``asyncio.run_coroutine_threadsafe``, so the transport
    never depends on the caller's thread having an event loop.

    Supports two modes:
    - Client mode: connect to WebSocket server
    - Server mode: bind/listen, then accept a single client connection

    Protocol:
        - Each send() sends a binary WebSocket message
        - Each recv() reads one binary WebSocket message

    Usage (client):
        transport = WebSocketTransport(
            mode="client",
            host="127.0.0.1",
            port=2224,
            path="/vpn",
        )
        transport.connect()
        # ... use send/recv ...
        transport.close()

    Usage (server):
        transport = WebSocketTransport(
            mode="server",
            host="127.0.0.1",
            port=2224,
            path="/vpn",
        )
        transport.connect()   # starts listening
        transport.accept()    # waits for a client
        # ... use send/recv ...
        transport.close()
    """

    MODE_CLIENT = "client"
    MODE_SERVER = "server"

    def __init__(
        self,
        mode: str = MODE_CLIENT,
        host: str = "127.0.0.1",
        port: int = 2224,
        path: str = "/",
        backlog: int = 5,
        extra_headers: Optional[dict] = None,
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
        self.extra_headers = extra_headers or {}

        self._ws = None
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

    # ------------------------------------------------------------------
    # Event-loop management
    # ------------------------------------------------------------------

    def _ensure_loop(self) -> None:
        """Start the background event loop if it is not already running."""
        if self._loop is not None and not self._loop.is_closed():
            return
        if self._shutting_down:
            raise TransportError("Transport is shutting down")

        self._loop_ready.clear()
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop, daemon=True, name="ws-transport-loop"
        )
        self._loop_thread.start()
        if not self._loop_ready.wait(timeout=5.0):
            raise TransportError("Event loop failed to start")

    def _run_loop(self) -> None:
        """Run the event loop forever.  Entry point for the background thread."""
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._loop_ready.set)
        self._loop.run_forever()
        # Drain remaining tasks after the loop is stopped
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
        """Submit *coro* to the background loop and wait for its result.

        Raises:
            TransportTimeout: if *timeout* is reached.
            TransportError:  if the coroutine raises.
        """
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
        """Connect to the remote (client) or start listening (server)."""
        if self._connected:
            logger.warning("Already connected")
            return
        if self._shutting_down:
            raise TransportError("Transport is shutting down")

        if self.mode == self.MODE_CLIENT:
            self._connect_client()
        else:
            self._start_server()

    def _connect_client(self) -> None:
        logger.info("WebSocket client connecting to %s:%d%s",
                     self.host, self.port, self.path)
        self._ensure_loop()
        self._setup_error = None
        self._connection_event.clear()

        # Submit a long-running coroutine and wait for the handshake to finish
        asyncio.run_coroutine_threadsafe(
            self._async_connect_client(), self._loop
        )

        if not self._connection_event.wait(timeout=10.0):
            raise TransportError("WebSocket connection timed out")

        if self._setup_error:
            err = self._setup_error
            self._setup_error = None
            raise TransportError(f"WebSocket connection failed: {err}")

    async def _async_connect_client(self) -> None:
        """Client-connect coroutine — runs until the WebSocket is closed."""
        extra_h = dict(self.extra_headers) if self.extra_headers else None
        url = f"ws://{self.host}:{self.port}{self.path}"

        try:
            from websockets import connect

            _kw = {_HEADER_KW: extra_h} if extra_h else {}
            async with connect(url, **_kw) as ws:
                self._ws = ws
                self._connected = True
                self._connection_event.set()
                logger.info("WebSocket client connected")
                await ws.wait_closed()
        except Exception as e:
            self._setup_error = e
            self._connection_event.set()
            return
        finally:
            self._connected = False
            logger.debug("WebSocket client connection closed")

    def _start_server(self) -> None:
        logger.info("WebSocket server binding to %s:%d",
                     self.host, self.port)
        self._server_ready.clear()
        self._setup_error = None
        self._ensure_loop()

        asyncio.run_coroutine_threadsafe(
            self._async_start_server(), self._loop
        )

        if not self._server_ready.wait(timeout=5.0):
            raise TransportError("WebSocket server start timed out")

        if self._setup_error:
            err = self._setup_error
            self._setup_error = None
            raise TransportError(f"WebSocket server failed: {err}")

        logger.info("WebSocket server started on %s:%d",
                     self.host, self.port)

    async def _async_start_server(self) -> None:
        try:
            from websockets import serve

            self._server = await serve(
                self._ws_handler,
                self.host,
                self.port,
            )
        except Exception as e:
            self._setup_error = e
            self._server_ready.set()
            return

        self._server_ready.set()
        logger.info("WebSocket server listening on %s:%d",
                     self.host, self.port)

    async def _ws_handler(self, ws) -> None:
        """Handle an incoming WebSocket connection (server mode)."""
        if self._ws is not None:
            try:
                await ws.close(1013, "Already connected")
            except Exception:
                pass
            return

        self._ws = ws
        self._connected = True
        self._connection_event.set()
        logger.info("WebSocket connection accepted")

        try:
            await ws.wait_closed()
        finally:
            self._connected = False
            logger.debug("WebSocket server connection closed")

    def accept(self, timeout: Optional[float] = None) -> None:
        """Wait for a client to connect (server mode only).

        Args:
            timeout: Maximum time to wait for connection.

        Raises:
            TransportError: If not in server mode or accept fails.
        """
        if self.mode != self.MODE_SERVER:
            raise TransportError("accept() is only available in server mode")

        if self._server is None:
            raise TransportError("Server not started, call connect() first")

        logger.info("Waiting for WebSocket connection...")

        if not self._connected:
            self._connection_event.clear()
            if not self._connection_event.wait(timeout=timeout):
                raise TransportError("Accept timeout")

    def send(self, data: bytes) -> None:
        """Send data as a binary WebSocket message.

        Raises:
            TransportError: If not connected or send fails.
        """
        if not self._connected or self._ws is None:
            raise TransportError("Not connected")

        try:
            self._run_coro(self._ws.send(data))
            logger.debug("WebSocketTransport sent %d bytes", len(data))
        except TransportTimeout:
            raise
        except Exception as e:
            self._connected = False
            raise TransportError(f"Send failed: {e}")

    def recv(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Receive a binary WebSocket message.

        Args:
            timeout: Maximum time to wait in seconds.
                    None = blocking, 0 = non-blocking.

        Returns:
            Frame bytes, or None if connection closed.

        Raises:
            TransportTimeout: If timeout expires with no data.
            TransportError: If receive fails.
        """
        if self._ws is None:
            raise TransportError("Not connected")

        try:
            result = self._run_coro(self._async_recv(timeout), timeout=timeout)
            if result is None:
                self._connected = False
                return None
            logger.debug("WebSocketTransport received %d bytes", len(result))
            return result
        except TransportTimeout:
            raise
        except Exception as e:
            self._connected = False
            raise TransportError(f"Receive failed: {e}")

    async def _async_recv(self, timeout: Optional[float]) -> Optional[bytes]:
        from websockets.exceptions import ConnectionClosed

        try:
            if timeout is not None:
                if timeout <= 0:
                    return await asyncio.wait_for(self._ws.recv(), timeout=0.01)
                return await asyncio.wait_for(self._ws.recv(), timeout=timeout)
            return await self._ws.recv()
        except asyncio.TimeoutError:
            raise TransportTimeout("Receive timeout")
        except ConnectionClosed:
            return None

    def close(self) -> None:
        """Close the connection and stop the background event loop.

        Idempotent -- safe to call multiple times.
        """
        if self._shutting_down:
            return

        logger.info("Closing WebSocket transport")
        self._shutting_down = True
        self._connected = False
        self._server_ready.set()
        self._connection_event.set()

        loop = self._loop

        # Close the WebSocket connection (grab reference first to avoid races)
        ws = self._ws
        self._ws = None
        if ws is not None and loop is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(ws.close(), loop).result(timeout=3.0)
            except Exception:
                pass

        # Close the server
        server = self._server
        self._server = None
        if server is not None and loop is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(
                    server.close(), loop
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
        logger.info("WebSocket transport closed")

    def is_connected(self) -> bool:
        return self._connected

    def __repr__(self) -> str:
        return (
            f"WebSocketTransport(mode={self.mode}, host={self.host}, "
            f"port={self.port}, path={self.path}, "
            f"connected={self._connected})"
        )
