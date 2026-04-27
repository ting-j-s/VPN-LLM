"""VPN Tunnel Configuration Management."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .errors import ConfigError
from .logger import setup_logger


logger = setup_logger(__name__)


@dataclass
class TransportConfig:
    """Transport layer configuration."""
    type: str = "ssh"
    host: str = "127.0.0.1"
    port: int = 22
    username: Optional[str] = None
    key_file: Optional[str] = None


@dataclass
class TUNConfig:
    """TUN device configuration."""
    name: str = "tun0"
    mtu: int = 1400
    subnet: str = "10.0.0.2/24"


@dataclass
class ServerConfig:
    """Server public address configuration."""
    host: str = "127.0.0.1"
    port: int = 2222


@dataclass
class ForwardingConfig:
    """Forwarding layer configuration."""
    nat_enabled: bool = True
    gateway: str = "192.168.1.1"


@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"


@dataclass
class ClientConfig:
    """Client full configuration."""
    transport: TransportConfig = field(default_factory=TransportConfig)
    tun: TUNConfig = field(default_factory=TUNConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


@dataclass
class ServerFullConfig:
    """Server full configuration."""
    transport: TransportConfig = field(default_factory=TransportConfig)
    tun: TUNConfig = field(default_factory=TUNConfig)
    forwarding: ForwardingConfig = field(default_factory=ForwardingConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def load_config(config_path: str) -> ClientConfig | ServerFullConfig:
    """Load configuration from YAML file.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        Loaded configuration object.

    Raises:
        ConfigError: If configuration file cannot be loaded or parsed.
    """
    path = Path(config_path).expanduser()

    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")

    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse YAML: {e}")

    if not data:
        raise ConfigError("Empty configuration file")

    # Determine config type based on content
    if "forwarding" in data:
        # Server config
        transport_data = data.get("transport", {})
        return ServerFullConfig(
            transport=TransportConfig(
                type=transport_data.get("type", "ssh"),
                host=transport_data.get("host", "0.0.0.0"),
                port=transport_data.get("port", 2222),
                username=transport_data.get("username"),
                key_file=transport_data.get("key_file"),
            ),
            tun=TUNConfig(
                name=data.get("tun", {}).get("name", "tun0"),
                mtu=data.get("tun", {}).get("mtu", 1400),
                subnet=data.get("tun", {}).get("subnet", "10.0.0.1/24"),
            ),
            forwarding=ForwardingConfig(
                nat_enabled=data.get("forwarding", {}).get("nat_enabled", True),
                gateway=data.get("forwarding", {}).get("gateway", "192.168.1.1"),
            ),
            logging=LoggingConfig(
                level=data.get("logging", {}).get("level", "INFO"),
            ),
        )
    else:
        # Client config
        transport_data = data.get("transport", {})
        server_data = data.get("server", {})
        return ClientConfig(
            transport=TransportConfig(
                type=transport_data.get("type", "ssh"),
                host=transport_data.get("host", "127.0.0.1"),
                port=transport_data.get("port", 22),
                username=transport_data.get("username"),
                key_file=transport_data.get("key_file"),
            ),
            tun=TUNConfig(
                name=data.get("tun", {}).get("name", "tun0"),
                mtu=data.get("tun", {}).get("mtu", 1400),
                subnet=data.get("tun", {}).get("subnet", "10.0.0.2/24"),
            ),
            server=ServerConfig(
                host=server_data.get("host", "127.0.0.1"),
                port=server_data.get("port", 2222),
            ),
            logging=LoggingConfig(
                level=data.get("logging", {}).get("level", "INFO"),
            ),
        )
