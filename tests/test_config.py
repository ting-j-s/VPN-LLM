"""Tests for configuration module."""

import pytest
import tempfile
from pathlib import Path

from src.common.config import (
    load_config,
    ClientConfig,
    ServerFullConfig,
    TransportConfig,
    TUNConfig,
    ServerConfig,
    ForwardingConfig,
    ConfigError,
)


class TestTransportConfig:
    """Test TransportConfig dataclass."""

    def test_defaults(self):
        """Test default values."""
        config = TransportConfig()

        assert config.type == "ssh"
        assert config.host == "127.0.0.1"
        assert config.port == 22
        assert config.username is None
        assert config.key_file is None

    def test_custom_values(self):
        """Test custom values."""
        config = TransportConfig(
            type="tcp",
            host="192.168.1.1",
            port=8080,
            username="user",
            key_file="/path/to/key",
        )

        assert config.type == "tcp"
        assert config.host == "192.168.1.1"
        assert config.port == 8080
        assert config.username == "user"
        assert config.key_file == "/path/to/key"


class TestTUNConfig:
    """Test TUNConfig dataclass."""

    def test_defaults(self):
        """Test default values."""
        config = TUNConfig()

        assert config.name == "tun0"
        assert config.mtu == 1400
        assert config.subnet == "10.0.0.2/24"


class TestLoadClientConfig:
    """Test loading client configuration."""

    def test_load_valid_config(self, tmp_path):
        """Test loading a valid client config."""
        config_file = tmp_path / "client.yaml"
        config_file.write_text("""
transport:
  type: ssh
  host: 10.0.0.1
  port: 22
  username: vpnuser
  key_file: ~/.ssh/id_rsa

tun:
  name: tun0
  mtu: 1400
  subnet: 10.0.0.2/24

server:
  host: 10.0.0.1
  port: 2222

logging:
  level: DEBUG
""")

        config = load_config(str(config_file))

        assert isinstance(config, ClientConfig)
        assert config.transport.type == "ssh"
        assert config.transport.host == "10.0.0.1"
        assert config.transport.username == "vpnuser"
        assert config.tun.name == "tun0"
        assert config.server.host == "10.0.0.1"
        assert config.server.port == 2222

    def test_load_missing_file(self):
        """Test loading non-existent config file."""
        with pytest.raises(ConfigError, match="not found"):
            load_config("/nonexistent/config.yaml")

    def test_load_invalid_yaml(self, tmp_path):
        """Test loading invalid YAML."""
        config_file = tmp_path / "invalid.yaml"
        config_file.write_text("""
transport:
  type: [invalid
  yaml content
""")

        with pytest.raises(ConfigError, match="Failed to parse"):
            load_config(str(config_file))

    def test_load_empty_file(self, tmp_path):
        """Test loading empty config file."""
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")

        with pytest.raises(ConfigError, match="Empty configuration"):
            load_config(str(config_file))


class TestLoadServerConfig:
    """Test loading server configuration."""

    def test_load_valid_server_config(self, tmp_path):
        """Test loading a valid server config."""
        config_file = tmp_path / "server.yaml"
        config_file.write_text("""
transport:
  type: ssh
  host: 0.0.0.0
  port: 2222
  key_file: /etc/ssh/ssh_host_rsa_key

tun:
  name: tun0
  mtu: 1400
  subnet: 10.0.0.1/24

forwarding:
  nat_enabled: true
  gateway: 192.168.1.1

logging:
  level: INFO
""")

        config = load_config(str(config_file))

        assert isinstance(config, ServerFullConfig)
        assert config.transport.type == "ssh"
        assert config.transport.port == 2222
        assert config.forwarding.nat_enabled is True
        assert config.forwarding.gateway == "192.168.1.1"

    def test_load_server_config_defaults(self, tmp_path):
        """Test loading server config with defaults."""
        config_file = tmp_path / "minimal.yaml"
        config_file.write_text("""
forwarding:
  nat_enabled: false
""")

        config = load_config(str(config_file))

        assert isinstance(config, ServerFullConfig)
        assert config.transport.type == "ssh"  # default
        assert config.transport.host == "0.0.0.0"  # default
        assert config.tun.name == "tun0"  # default
        assert config.forwarding.nat_enabled is False
