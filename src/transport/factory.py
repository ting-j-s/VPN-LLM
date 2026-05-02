"""Transport Factory Module.

Creates Transport instances based on configuration.
Provides a unified interface for creating transports.
"""

from typing import Optional

from ..common.errors import VPNError, ConfigError
from ..common.logger import get_logger
from .base import Transport, MockTransport
from .ssh_transport import SSHTransport
from .tcp_transport import TCPTransport
from .tls_transport import TLSTransport
from .websocket_transport import WebSocketTransport


logger = get_logger(__name__)

# Supported transport types
SUPPORTED_TRANSPORTS = {"ssh", "tcp", "tls", "websocket", "mock"}


def create_transport(config) -> Transport:
    """Create a Transport instance based on configuration.

    Args:
        config: Configuration object with transport.type and transport settings.
            For client: needs server.host, server.port, etc.
            For server: needs server settings and listen mode.

    Returns:
        Transport instance.

    Raises:
        ConfigError: If transport type is not supported.

    Supported types:
        - "ssh": SSHTransport (client only for now)
        - "tcp": TCPTransport (client or server)
        - "tls": TLSTransport (client or server)
        - "websocket": WebSocketTransport (client or server)
        - "mock": MockTransport (for testing)
    """
    transport_type = getattr(config.transport, 'type', 'ssh')

    logger.info(f"Creating transport of type: {transport_type}")

    if transport_type == "ssh":
        return _create_ssh_transport(config)
    elif transport_type == "tcp":
        return _create_tcp_transport(config)
    elif transport_type == "tls":
        return _create_tls_transport(config)
    elif transport_type == "websocket":
        return _create_websocket_transport(config)
    elif transport_type == "mock":
        logger.info("Using MockTransport for testing")
        return MockTransport()
    else:
        raise ConfigError(
            f"Unsupported transport type: '{transport_type}'. "
            f"Supported: {SUPPORTED_TRANSPORTS}"
        )


def _create_ssh_transport(config) -> SSHTransport:
    """Create SSHTransport from configuration.

    Args:
        config: Configuration object.

    Returns:
        SSHTransport instance.

    Raises:
        ConfigError: If required SSH settings are missing.
    """
    # Client config has server endpoint info
    if hasattr(config, 'server'):
        host = getattr(config.server, 'host', '127.0.0.1')
        port = getattr(config.server, 'port', 22)
        username = getattr(config.server, 'username', None)
        ssh_key_path = getattr(config.server, 'ssh_key_path', None)

        if not username:
            raise ConfigError("SSH transport requires server.username in config")

        logger.info(f"Creating SSHTransport: host={host}, port={port}, username={username}")

        auto_add_host_key = getattr(config.transport, 'auto_add_host_key', False)

        return SSHTransport(
            host=host,
            port=port,
            username=username,
            ssh_key_path=ssh_key_path,
            auto_add_host_key=auto_add_host_key,
        )

    # Server config
    else:
        # For now, server side SSH transport needs more work
        logger.warning("Server-side SSH transport not fully implemented")
        raise ConfigError("Server-side SSH transport not yet implemented")


def _create_tcp_transport(config) -> TCPTransport:
    """Create TCPTransport from configuration.

    Args:
        config: Configuration object.

    Returns:
        TCPTransport instance.

    Notes:
        Client config: mode="client", connects to server.host:server.port
        Server config: mode="server", binds to server.tun_ip:2222
    """
    # Determine if client or server
    # Client config has 'client' section, server config has 'server' section
    if hasattr(config, 'server') and hasattr(config.server, 'host'):
        # Client config
        host = getattr(config.server, 'host', '127.0.0.1')
        port = getattr(config.server, 'port', 2222)

        logger.info(f"Creating TCPTransport (client): host={host}, port={port}")

        return TCPTransport(
            mode=TCPTransport.MODE_CLIENT,
            host=host,
            port=port,
        )

    elif hasattr(config, 'server') and hasattr(config.server, 'tun_ip'):
        # Server config
        host = getattr(config.server, 'tun_ip', '0.0.0.0')
        port = getattr(config.server, 'listen_port', 2222)

        logger.info(f"Creating TCPTransport (server): host={host}, port={port}")

        return TCPTransport(
            mode=TCPTransport.MODE_SERVER,
            host=host,
            port=port,
        )

    else:
        raise ConfigError("Invalid configuration for TCP transport")


def _create_tls_transport(config) -> TLSTransport:
    """Create TLSTransport from configuration.

    Args:
        config: Configuration object.

    Returns:
        TLSTransport instance.

    Notes:
        Client config: mode="client", connects to server.host:server.port
        Server config: mode="server", binds to server.tun_ip:2223
    """
    # TLS cert settings from config
    certfile = getattr(config.transport, 'certfile', None)
    keyfile = getattr(config.transport, 'keyfile', None)
    cafile = getattr(config.transport, 'cafile', None)
    verify_server = getattr(config.transport, 'verify_server', True)
    insecure_skip_verify = getattr(config.transport, 'insecure_skip_verify', False)
    server_hostname = getattr(config.transport, 'server_hostname', None) or getattr(config.server, 'host', None)

    # Determine if client or server
    if hasattr(config, 'server') and hasattr(config.server, 'host'):
        # Client config
        host = getattr(config.server, 'host', '127.0.0.1')
        port = getattr(config.server, 'port', 2223)

        logger.info(f"Creating TLSTransport (client): host={host}, port={port}")

        return TLSTransport(
            mode=TLSTransport.MODE_CLIENT,
            host=host,
            port=port,
            certfile=certfile,
            keyfile=keyfile,
            cafile=cafile,
            verify_server=verify_server,
            insecure_skip_verify=insecure_skip_verify,
            server_hostname=server_hostname,
        )

    elif hasattr(config, 'server') and hasattr(config.server, 'tun_ip'):
        # Server config
        host = getattr(config.server, 'tun_ip', '0.0.0.0')
        port = getattr(config.server, 'listen_port', 2223)

        logger.info(f"Creating TLSTransport (server): host={host}, port={port}")

        return TLSTransport(
            mode=TLSTransport.MODE_SERVER,
            host=host,
            port=port,
            certfile=certfile,
            keyfile=keyfile,
            cafile=cafile,
        )

    else:
        raise ConfigError("Invalid configuration for TLS transport")


def _create_websocket_transport(config) -> WebSocketTransport:
    """Create WebSocketTransport from configuration.

    Args:
        config: Configuration object.

    Returns:
        WebSocketTransport instance.

    Notes:
        Client config: mode="client", connects to server.host:server.port
        Server config: mode="server", binds to server.tun_ip:2224
    """
    # WebSocket path from config
    path = getattr(config.transport, 'path', '/')

    # Determine if client or server
    if hasattr(config, 'server') and hasattr(config.server, 'host'):
        # Client config
        host = getattr(config.server, 'host', '127.0.0.1')
        port = getattr(config.server, 'port', 2224)

        logger.info(f"Creating WebSocketTransport (client): host={host}, port={port}, path={path}")

        return WebSocketTransport(
            mode=WebSocketTransport.MODE_CLIENT,
            host=host,
            port=port,
            path=path,
        )

    elif hasattr(config, 'server') and hasattr(config.server, 'tun_ip'):
        # Server config
        host = getattr(config.server, 'tun_ip', '0.0.0.0')
        port = getattr(config.server, 'listen_port', 2224)

        logger.info(f"Creating WebSocketTransport (server): host={host}, port={port}, path={path}")

        return WebSocketTransport(
            mode=WebSocketTransport.MODE_SERVER,
            host=host,
            port=port,
            path=path,
        )

    else:
        raise ConfigError("Invalid configuration for WebSocket transport")


def register_transport(name: str) -> None:
    """Register a custom transport type.

    Args:
        name: Transport type name.
    """
    SUPPORTED_TRANSPORTS.add(name)
    logger.info(f"Registered custom transport type: {name}")


def list_supported_transports() -> set:
    """Get set of supported transport types.

    Returns:
        Set of transport type names.
    """
    return SUPPORTED_TRANSPORTS.copy()
