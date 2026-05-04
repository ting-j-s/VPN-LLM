"""Tests for configuration module."""

import pytest
from pathlib import Path

from src.common.config import (
    ClientConfig,
    ServerConfig,
    ClientTUNConfig,
    ServerTUNConfig,
    ServerEndpointConfig,
    TransportConfig,
    SessionConfig,
    ForwardingConfig,
    ConfigError,
    load_client_config,
    load_server_config,
)


class TestLoadClientConfig:
    """Test loading client configuration."""

    def test_load_valid_client_config(self, tmp_path):
        """Test loading a valid client config."""
        config_file = tmp_path / "client.yaml"
        config_file.write_text("""
client:
  tun_name: tun0
  tun_ip: 10.8.0.2
  tun_peer: 10.8.0.1
  mtu: 1400

server:
  host: 127.0.0.1
  port: 22
  username: testuser
  ssh_key_path: /home/user/.ssh/id_rsa

transport:
  type: ssh

session:
  heartbeat_interval: 10
  reconnect: true
  reconnect_interval: 3
""")
        config = load_client_config(str(config_file))

        assert isinstance(config, ClientConfig)
        assert config.client.tun_name == "tun0"
        assert config.client.tun_ip == "10.8.0.2"
        assert config.client.tun_peer == "10.8.0.1"
        assert config.client.mtu == 1400

        assert config.server.host == "127.0.0.1"
        assert config.server.port == 22
        assert config.server.username == "testuser"
        assert config.server.ssh_key_path == "/home/user/.ssh/id_rsa"

        assert config.transport.type == "ssh"
        assert config.session.heartbeat_interval == 10
        assert config.session.reconnect is True
        assert config.session.reconnect_interval == 3

    def test_load_client_config_with_defaults(self, tmp_path):
        """Test loading client config with minimal fields."""
        config_file = tmp_path / "client.yaml"
        config_file.write_text("""
server:
  host: 127.0.0.1
  username: testuser
  ssh_key_path: /path/to/key
""")
        config = load_client_config(str(config_file))

        assert config.client.tun_name == "tun0"
        assert config.client.tun_ip == "10.8.0.2"
        assert config.client.tun_peer == "10.8.0.1"
        assert config.server.port == 22
        assert config.transport.type == "ssh"

    def test_load_missing_username(self, tmp_path):
        """Test error when server.username is missing."""
        config_file = tmp_path / "client.yaml"
        config_file.write_text("""
server:
  host: 127.0.0.1
  ssh_key_path: /path/to/key
""")
        with pytest.raises(ConfigError, match="server.username is required"):
            load_client_config(str(config_file))

    def test_load_missing_ssh_key_path(self, tmp_path):
        """Test error when server.ssh_key_path is missing."""
        config_file = tmp_path / "client.yaml"
        config_file.write_text("""
server:
  host: 127.0.0.1
  username: testuser
""")
        with pytest.raises(ConfigError, match="server.ssh_key_path is required"):
            load_client_config(str(config_file))

    def test_load_missing_file(self):
        """Test error when config file not found."""
        with pytest.raises(ConfigError, match="Configuration file not found"):
            load_client_config("/nonexistent/config.yaml")

    def test_load_invalid_transport_type(self, tmp_path):
        """Test error when transport.type is invalid."""
        config_file = tmp_path / "client.yaml"
        config_file.write_text("""
server:
  host: 127.0.0.1
  username: testuser
  ssh_key_path: /path/to/key

transport:
  type: http
""")
        with pytest.raises(ConfigError, match="Invalid transport.type"):
            load_client_config(str(config_file))


class TestLoadServerConfig:
    """Test loading server configuration."""

    def test_load_valid_server_config(self, tmp_path):
        """Test loading a valid server config."""
        config_file = tmp_path / "server.yaml"
        config_file.write_text("""
server:
  tun_name: tun0
  tun_ip: 10.8.0.1
  tun_peer: 10.8.0.2
  mtu: 1400
  listen_mode: ssh

forwarding:
  enable_nat: false
  enable_route: true

transport:
  type: ssh

session:
  heartbeat_timeout: 30
""")
        config = load_server_config(str(config_file))

        assert isinstance(config, ServerConfig)
        assert config.server.tun_name == "tun0"
        assert config.server.tun_ip == "10.8.0.1"
        assert config.server.tun_peer == "10.8.0.2"
        assert config.server.mtu == 1400
        assert config.server.listen_mode == "ssh"

        assert config.forwarding.enable_nat is False
        assert config.forwarding.enable_route is True

        assert config.transport.type == "ssh"
        assert config.session.heartbeat_timeout == 30

    def test_load_server_config_with_defaults(self, tmp_path):
        """Test loading server config with minimal fields."""
        config_file = tmp_path / "server.yaml"
        config_file.write_text("""
forwarding:
  enable_nat: false
""")
        config = load_server_config(str(config_file))

        assert config.server.tun_name == "tun0"
        assert config.server.tun_ip == "10.8.0.1"
        assert config.server.listen_mode == "ssh"
        assert config.forwarding.enable_nat is False
        assert config.forwarding.enable_route is True
        assert config.transport.type == "ssh"

    def test_load_invalid_transport_type_server(self, tmp_path):
        """Test error when transport.type is invalid on server."""
        config_file = tmp_path / "server.yaml"
        config_file.write_text("""
transport:
  type: http
""")
        with pytest.raises(ConfigError, match="Invalid transport.type"):
            load_server_config(str(config_file))

    def test_load_missing_file_server(self):
        """Test error when server config file not found."""
        with pytest.raises(ConfigError, match="Configuration file not found"):
            load_server_config("/nonexistent/config.yaml")


class TestDataclasses:
    """Test configuration dataclasses."""

    def test_client_tun_config_defaults(self):
        """Test ClientTUNConfig default values."""
        config = ClientTUNConfig()
        assert config.tun_name == "tun0"
        assert config.tun_ip == "10.8.0.2"
        assert config.tun_peer == "10.8.0.1"
        assert config.mtu == 1400

    def test_server_tun_config_defaults(self):
        """Test ServerTUNConfig default values."""
        config = ServerTUNConfig()
        assert config.tun_name == "tun0"
        assert config.tun_ip == "10.8.0.1"
        assert config.tun_peer == "10.8.0.2"
        assert config.mtu == 1400
        assert config.listen_mode == "ssh"

    def test_transport_config_defaults(self):
        """Test TransportConfig default values."""
        config = TransportConfig()
        assert config.type == "ssh"

    def test_session_config_defaults(self):
        """Test SessionConfig default values."""
        config = SessionConfig()
        assert config.heartbeat_interval == 10
        assert config.reconnect is True
        assert config.reconnect_interval == 3
        assert config.heartbeat_timeout == 30

    def test_forwarding_config_defaults(self):
        """Test ForwardingConfig default values."""
        config = ForwardingConfig()
        assert config.enable_nat is False
        assert config.enable_route is True


class TestSSHKeyPathExpansion:
    """Test SSH key path expansion."""

    def test_ssh_key_path_expansion(self, tmp_path):
        """Test that ~/ in ssh_key_path is expanded."""
        config_file = tmp_path / "client.yaml"
        config_file.write_text("""
server:
  host: 127.0.0.1
  username: testuser
  ssh_key_path: ~/test_key
""")
        config = load_client_config(str(config_file))

        assert config.server.ssh_key_path.endswith("/test_key")
        assert "~" not in config.server.ssh_key_path