"""WebSocket Transport Implementation.

Provides WebSocket-based transport for the VPN tunnel.
Supports both client (connect) and server (bind/listen/accept) modes.
Uses websockets library for WebSocket protocol handling.
"""

import asyncio
import socket
import threading
from typing import Optional

from ..common.errors import TransportError
from ..common.logger import get_logger
from .base import Transport


logger = get_logger(__name__)

# Max frame size: 10MB
MAX_FRAME_SIZE = 10 * 1024 * 1024


class WebSocketTransport(Transport):
    """WebSocket-based transport implementation.

    Supports two modes:
    - Client mode: connect to WebSocket server
    - Server mode: start WebSocket server and accept connections

    Protocol:
        - Each send() sends binary WebSocket message
        - Each recv() receives binary WebSocket message

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
            host="0.0.0.0",
            port=2224,
            path="/vpn",
        )
        transport.connect()  # Sets up listening socket
        # Call accept() to get connection from client
        transport.accept()
        # ... use send/recv ...
        transport.close()
    """

    # Mode constants
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
        """Initialize WebSocket transport.

        Args:
            mode: "client" or "server".
            host: Host to bind or connect to.
            port: Port number.
            path: WebSocket path (client mode).
            backlog: Listen backlog (server mode only).
            extra_headers: Extra HTTP headers for WebSocket handshake.
        """
        if mode not in (self.MODE_CLIENT, self.MODE_SERVER):
            raise TransportError(f"Invalid mode: {mode}. Must be 'client' or 'server'")

        self.mode = mode
        self.host = host
        self.port = port
        self.path = path
        self.backlog = backlog
        self.extra_headers = extra_headers or {}

        self._server_sock: Optional[socket.socket] = None
        self._ws: Optional['websockets.WebSocketProtocol'] = None  # type: ignore
        self._connected = False
        self._closed = False

        # Server threading
        self._server_thread: Optional[threading.Thread] = None
        self._server_ready = threading.Event()
        self._server_error: Optional[Exception] = None

    def connect(self) -> None:
        """Connect or start listening.

        Client mode: connect to WebSocket server.
        Server mode: bind and listen for WebSocket connections.

        Raises:
            TransportError: If connection fails.
        """
        if self._connected:
            logger.warning("Already connected")
            return

        if self._closed:
            raise TransportError("Transport already closed, create a new instance")

        if self.mode == self.MODE_CLIENT:
            self._connect_client()
        else:
            self._start_server()

    def _get_ws_url(self) -> str:
        """Get WebSocket URL."""
        return f"ws://{self.host}:{self.port}{self.path}"

    async def _async_connect_client(self) -> None:
        """Connect as client to WebSocket server (async)."""
        import websockets

        logger.info(f"WebSocket client connecting to {self._get_ws_url()}")

        extra_h = dict(self.extra_headers) if self.extra_headers else None

        try:
            self._ws = await websockets.connect(
                self._get_ws_url(),
                extra_headers=extra_h,
            )
        except Exception as e:
            raise TransportError(f"WebSocket connection failed: {e}")

        self._connected = True
        logger.info("WebSocket client connected")

    async def _async_serve(self) -> None:
        """Start WebSocket server (async)."""
        import websockets
        from websockets.server import serve, WebSocketServerProtocol

        logger.info(f"WebSocket server binding to {self.host}:{self.port}")

        self._server_ready.set()

        async with serve(
            self._ws_handler,
            host=self.host,
            port=self.port,
            path=self.path if self.path != "/" else None,
            backlog=self.backlog,
        ) as server:
            logger.info(f"WebSocket server listening on {self.host}:{self.port}")
            await server.wait_closed()

    async def _ws_handler(self, ws: 'WebSocketServerProtocol') -> None:  # type: ignore
        """Handle incoming WebSocket connection."""
        self._ws = ws
        self._connected = True
        logger.info(f"WebSocket connection accepted from {ws.remote_address}")

    def _connect_client(self) -> None:
        """Connect as client to server (sync wrapper)."""
        try:
            asyncio.get_event_loop().run_until_complete(self._async_connect_client())
        except Exception as e:
            raise TransportError(f"WebSocket connection failed: {e}")

    def _start_server(self) -> None:
        """Start WebSocket server in background thread."""
        def run_server():
            try:
                asyncio.get_event_loop().run_until_complete(self._async_serve())
            except Exception as e:
                self._server_error = e
                self._server_ready.set()

        self._server_thread = threading.Thread(target=run_server, daemon=True)
        self._server_thread.start()

        # Wait for server to be ready
        self._server_ready.wait(timeout=5.0)
        if self._server_error:
            raise TransportError(f"WebSocket server failed: {self._server_error}")

        logger.info(f"WebSocket server started on {self.host}:{self.port}")

    def accept(self, timeout: Optional[float] = None) -> None:
        """Accept incoming connection (server mode only).

        Args:
            timeout: Maximum time to wait for connection.

        Raises:
            TransportError: If not in server mode or accept fails.
        """
        if self.mode != self.MODE_SERVER:
            raise TransportError("accept() is only available in server mode")

        if self._server_thread is None:
            raise TransportError("Server not started, call connect() first")

        # Wait for connection with timeout
        start = asyncio.get_event_loop().time() if hasattr(asyncio.get_event_loop(), 'time') else 0
        while timeout is None or (asyncio.get_event_loop().time() - start < timeout if hasattr(asyncio.get_event_loop(), 'time') else True):
            if self._ws is not None and self._connected:
                return
            import time
            time.sleep(0.1)

        raise TransportError("Accept timeout")

    def send(self, data: bytes) -> None:
        """Send data as binary WebSocket message.

        Args:
            data: Frame bytes to send.

        Raises:
            TransportError: If not connected or send fails.
        """
        if not self._connected or self._ws is None:
            raise TransportError("Not connected")

        async def async_send():
            await self._ws.send(data)

        try:
            asyncio.get_event_loop().run_until_complete(async_send())
            logger.debug(f"WebSocketTransport sent {len(data)} bytes")
        except Exception as e:
            self._connected = False
            raise TransportError(f"Send failed: {e}")

    def recv(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Receive data as binary WebSocket message.

        Args:
            timeout: Maximum time to wait in seconds.
                    None = blocking, 0 = non-blocking.

        Returns:
            Frame bytes, or None if no data (non-blocking).

        Raises:
            TransportError: If receive fails or not connected.
        """
        if not self._connected or self._ws is None:
            raise TransportError("Not connected")

        async def async_recv():
            try:
                if timeout is not None and timeout > 0:
                    return await asyncio.wait_for(
                        self._ws.recv(),
                        timeout=timeout
                    )
                else:
                    return await self._ws.recv()
            except asyncio.TimeoutError:
                return None

        try:
            result = asyncio.get_event_loop().run_until_complete(async_recv())
            if result is None:
                return None
            logger.debug(f"WebSocketTransport received {len(result)} bytes")
            return result
        except Exception as e:
            self._connected = False
            raise TransportError(f"Receive failed: {e}")

    def close(self) -> None:
        """Close the connection."""
        logger.info("Closing WebSocket transport")

        self._connected = False
        self._closed = True

        if self._ws is not None:
            async def async_close():
                await self._ws.close()

            try:
                asyncio.get_event_loop().run_until_complete(async_close())
            except Exception:
                pass

            self._ws = None

        # Server thread will be stopped when daemon exits
        self._server_thread = None

        logger.info("WebSocket transport closed")

    def is_connected(self) -> bool:
        """Check if transport is connected.

        Returns:
            True if connected, False otherwise.
        """
        return self._connected

    def __repr__(self) -> str:
        return (
            f"WebSocketTransport(mode={self.mode}, host={self.host}, port={self.port}, "
            f"path={self.path}, connected={self._connected})"
        )