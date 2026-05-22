"""VPN Tunnel Configuration Management.

Loads and validates YAML configuration files for client and server.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .errors import VPNError
from ..shaping.config import ShapingConfig


class ConfigError(VPNError):
    """Configuration error."""
    pass


# Allowed transport types
ALLOWED_TRANSPORT_TYPES = {"ssh", "tcp", "tls", "websocket", "http2", "mock"}


@dataclass
class ClientTUNConfig:
    """Client TUN device configuration."""
    tun_name: str = "tun0"
    tun_ip: str = "10.8.0.2"
    tun_peer: str = "10.8.0.1"
    mtu: int = 1400


@dataclass
class ServerTUNConfig:
    """Server TUN device configuration."""
    tun_name: str = "tun0"
    tun_ip: str = "10.8.0.1"
    tun_peer: str = "10.8.0.2"
    mtu: int = 1400
    listen_mode: str = "ssh"
    listen_port: int = 2222


@dataclass
class ServerEndpointConfig:
    """Server endpoint (SSH server) configuration."""
    host: str = "127.0.0.1"
    port: int = 22
    username: str = ""
    ssh_key_path: str = ""


@dataclass
class TransportConfig:
    """Transport layer configuration."""
    type: str = "ssh"
    # TLS-specific options
    certfile: Optional[str] = None
    keyfile: Optional[str] = None
    cafile: Optional[str] = None
    verify_server: bool = True
    insecure_skip_verify: bool = False
    # SSH-specific options
    auto_add_host_key: bool = False
    # WebSocket-specific options
    path: str = "/"
    # HTTP/2-specific options (experimental)
    server_hostname: Optional[str] = None
    experimental: bool = False


@dataclass
class SessionConfig:
    """Session management configuration."""
    # Heartbeat settings (used by both client and server)
    heartbeat_interval: int = 10
    heartbeat_timeout: int = 30
    # Client-only settings
    reconnect: bool = True
    reconnect_interval: int = 3
    # Optional shared session identifier (32-char hex, not a secret/key)
    session_id: Optional[str] = None


@dataclass
class ForwardingConfig:
    """Forwarding layer configuration (server only)."""
    enable_nat: bool = False
    enable_route: bool = True


@dataclass
class ClientConfig:
    """Client full configuration."""
    client: ClientTUNConfig = field(default_factory=ClientTUNConfig)
    server: ServerEndpointConfig = field(default_factory=ServerEndpointConfig)
    transport: TransportConfig = field(default_factory=TransportConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    shaping: ShapingConfig = field(default_factory=ShapingConfig)


@dataclass
class ServerConfig:
    """Server full configuration."""
    server: ServerTUNConfig = field(default_factory=ServerTUNConfig)
    forwarding: ForwardingConfig = field(default_factory=ForwardingConfig)
    transport: TransportConfig = field(default_factory=TransportConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    shaping: ShapingConfig = field(default_factory=ShapingConfig)


def _load_yaml(path: Path) -> dict:
    """Load YAML file.

    Args:
        path: Path to YAML file.

    Returns:
        Parsed YAML data.

    Raises:
        ConfigError: If file not found or YAML parse error.
    """
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")

    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse YAML: {e}")

    if not data:
        raise ConfigError(f"Empty configuration file: {path}")

    return data


def _validate_transport(data: dict) -> None:
    """Validate transport section.

    Args:
        data: Configuration data.

    Raises:
        ConfigError: If validation fails.
    """
    transport = data.get("transport", {})
    transport_type = transport.get("type", "ssh")

    if transport_type not in ALLOWED_TRANSPORT_TYPES:
        raise ConfigError(
            f"Invalid transport.type: '{transport_type}'. "
            f"Allowed: {ALLOWED_TRANSPORT_TYPES}"
        )


def _load_shaping(data: dict) -> ShapingConfig:
    """Load shaping configuration from YAML data.

    The shaping section is optional — missing keys default to disabled.
    """
    shaping_data = data.get("shaping", {})
    if not isinstance(shaping_data, dict):
        raise ConfigError("shaping must be a mapping")
    config = ShapingConfig.from_dict(shaping_data)
    config.validate()
    return config


def load_client_config(path: str) -> ClientConfig:
    """Load client configuration from YAML file.

    Args:
        path: Path to client YAML configuration file.

    Returns:
        ClientConfig object.

    Raises:
        ConfigError: If validation fails.
    """
    data = _load_yaml(Path(path))
    _validate_transport(data)

    # Build ClientTUNConfig
    client_data = data.get("client", {})
    tun = ClientTUNConfig(
        tun_name=client_data.get("tun_name", "tun0"),
        tun_ip=client_data.get("tun_ip", "10.8.0.2"),
        tun_peer=client_data.get("tun_peer", "10.8.0.1"),
        mtu=client_data.get("mtu", 1400),
    )

    # Build ServerEndpointConfig
    server_data = data.get("server", {})
    endpoint = ServerEndpointConfig(
        host=server_data.get("host", "127.0.0.1"),
        port=server_data.get("port", 22),
        username=server_data.get("username", ""),
        ssh_key_path=str(Path(server_data["ssh_key_path"]).expanduser()) if server_data.get("ssh_key_path") else "",
    )

    # Build TransportConfig
    transport_data = data.get("transport", {})
    transport = TransportConfig(
        type=transport_data.get("type", "ssh"),
        certfile=transport_data.get("certfile"),
        keyfile=transport_data.get("keyfile"),
        cafile=transport_data.get("cafile"),
        verify_server=transport_data.get("verify_server", True),
        insecure_skip_verify=transport_data.get("insecure_skip_verify", False),
        auto_add_host_key=transport_data.get("auto_add_host_key", False),
        path=transport_data.get("path", "/"),
        server_hostname=transport_data.get("server_hostname"),
        experimental=transport_data.get("experimental", False),
    )

    # SSH-specific validation (only when using SSH transport)
    if transport.type == "ssh":
        if not server_data.get("username"):
            raise ConfigError("server.username is required for SSH transport")
        if not server_data.get("ssh_key_path"):
            raise ConfigError("server.ssh_key_path is required for SSH transport")

    # Build SessionConfig
    session_data = data.get("session", {})
    session = SessionConfig(
        heartbeat_interval=session_data.get("heartbeat_interval", 10),
        reconnect=session_data.get("reconnect", True),
        reconnect_interval=session_data.get("reconnect_interval", 3),
        session_id=session_data.get("session_id"),
    )

    shaping = _load_shaping(data)

    return ClientConfig(client=tun, server=endpoint, transport=transport, session=session, shaping=shaping)


def load_server_config(path: str) -> ServerConfig:
    """Load server configuration from YAML file.

    Args:
        path: Path to server YAML configuration file.

    Returns:
        ServerConfig object.

    Raises:
        ConfigError: If validation fails.
    """
    data = _load_yaml(Path(path))
    _validate_transport(data)

    # Build ServerTUNConfig
    server_data = data.get("server", {})
    tun = ServerTUNConfig(
        tun_name=server_data.get("tun_name", "tun0"),
        tun_ip=server_data.get("tun_ip", "10.8.0.1"),
        tun_peer=server_data.get("tun_peer", "10.8.0.2"),
        mtu=server_data.get("mtu", 1400),
        listen_mode=server_data.get("listen_mode", "ssh"),
        listen_port=server_data.get("listen_port", 2222),
    )

    # Build ForwardingConfig
    fwd_data = data.get("forwarding", {})
    forwarding = ForwardingConfig(
        enable_nat=fwd_data.get("enable_nat", False),
        enable_route=fwd_data.get("enable_route", True),
    )

    # Build TransportConfig
    transport_data = data.get("transport", {})
    transport = TransportConfig(
        type=transport_data.get("type", "ssh"),
        certfile=transport_data.get("certfile"),
        keyfile=transport_data.get("keyfile"),
        cafile=transport_data.get("cafile"),
        verify_server=transport_data.get("verify_server", True),
        insecure_skip_verify=transport_data.get("insecure_skip_verify", False),
        auto_add_host_key=transport_data.get("auto_add_host_key", False),
        path=transport_data.get("path", "/"),
        server_hostname=transport_data.get("server_hostname"),
        experimental=transport_data.get("experimental", False),
    )

    # Build SessionConfig
    session_data = data.get("session", {})
    session = SessionConfig(
        heartbeat_timeout=session_data.get("heartbeat_timeout", 30),
        session_id=session_data.get("session_id"),
    )

    shaping = _load_shaping(data)

    return ServerConfig(server=tun, forwarding=forwarding, transport=transport, session=session, shaping=shaping)