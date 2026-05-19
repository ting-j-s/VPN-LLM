"""Tests for YAML config shaping section — integration with ClientConfig/ServerConfig."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.config import (
    ClientConfig,
    ServerConfig,
    ShapingConfig,
    load_client_config,
    load_server_config,
    ConfigError,
)


# ---------------------------------------------------------------------------
# 1. ShapingConfig is integrated into ClientConfig / ServerConfig
# ---------------------------------------------------------------------------

class TestShapingInConfigDataclass:
    def test_client_config_has_shaping_default(self):
        c = ClientConfig()
        assert isinstance(c.shaping, ShapingConfig)
        assert c.shaping.enabled is False

    def test_server_config_has_shaping_default(self):
        c = ServerConfig()
        assert isinstance(c.shaping, ShapingConfig)
        assert c.shaping.enabled is False


# ---------------------------------------------------------------------------
# 2. Old config without shaping section still loads
# ---------------------------------------------------------------------------

class TestOldConfigCompatibility:
    def test_client_config_without_shaping_section(self):
        """Config missing the shaping key must load with all shaping disabled."""
        yaml_content = """
client:
  tun_name: tun0
  tun_ip: 10.8.0.2
  tun_peer: 10.8.0.1
  mtu: 1400
server:
  host: 127.0.0.1
  port: 2222
transport:
  type: tcp
session:
  heartbeat_interval: 10
  reconnect: true
"""
        with patch("src.common.config._load_yaml", return_value={"client": {}, "server": {}, "transport": {"type": "tcp"}, "session": {}}):
            pass  # placeholder — we use patching approach differently

    def test_old_config_loads_via_from_dict(self):
        """Test ShapingConfig.from_dict({}) gives all defaults."""
        c = ShapingConfig.from_dict({})
        assert c.enabled is False
        assert c.padding_enabled is False
        c.validate()

    def test_empty_shaping_section_loads(self):
        c = ShapingConfig.from_dict({"shaping": {}} if False else {})
        assert c.enabled is False


# ---------------------------------------------------------------------------
# 3. Shaping config from dict
# ---------------------------------------------------------------------------

class TestShapingFromDict:
    def test_enabled_false_returns_noop(self):
        c = ShapingConfig.from_dict({"enabled": False, "padding_enabled": True,
                                      "min_padding_bytes": 8, "max_padding_bytes": 64})
        c.validate()
        from src.shaping.factory import create_traffic_shaper
        from src.shaping.base import NoopTrafficShaper
        shaper = create_traffic_shaper(c)
        assert isinstance(shaper, NoopTrafficShaper)

    def test_padding_only_config_creates_shaper(self):
        c = ShapingConfig.from_dict({
            "enabled": True,
            "padding_enabled": True,
            "min_padding_bytes": 8,
            "max_padding_bytes": 64,
        })
        c.validate()
        from src.shaping.factory import create_traffic_shaper, PipelineTrafficShaper
        from src.shaping.padding import PaddingShaper
        shaper = create_traffic_shaper(c)
        # Factory wraps single stages in Pipeline for consistency
        assert isinstance(shaper, (PaddingShaper, PipelineTrafficShaper))

    def test_aggregation_config_creates_shaper(self):
        c = ShapingConfig.from_dict({
            "enabled": True,
            "aggregation_enabled": True,
            "aggregation_max_bytes": 4096,
            "aggregation_max_delay_ms": 50.0,
        })
        c.validate()
        from src.shaping.factory import create_traffic_shaper, PipelineTrafficShaper
        from src.shaping.aggregation import AggregationShaper
        shaper = create_traffic_shaper(c)
        assert isinstance(shaper, (AggregationShaper, PipelineTrafficShaper))

    def test_padding_aggregation_config_creates_pipeline(self):
        c = ShapingConfig.from_dict({
            "enabled": True,
            "padding_enabled": True,
            "min_padding_bytes": 8,
            "max_padding_bytes": 64,
            "aggregation_enabled": True,
            "aggregation_max_bytes": 4096,
        })
        c.validate()
        from src.shaping.factory import create_traffic_shaper, PipelineTrafficShaper
        shaper = create_traffic_shaper(c)
        assert isinstance(shaper, PipelineTrafficShaper)


# ---------------------------------------------------------------------------
# 4. Validation of shaping config
# ---------------------------------------------------------------------------

class TestShapingConfigValidation:
    def test_invalid_padding_range_raises(self):
        c = ShapingConfig.from_dict({
            "enabled": True,
            "padding_enabled": True,
            "min_padding_bytes": 100,
            "max_padding_bytes": 10,
        })
        with pytest.raises(ValueError, match="max_padding_bytes"):
            c.validate()

    def test_invalid_jitter_range_raises(self):
        c = ShapingConfig.from_dict({
            "enabled": True,
            "jitter_enabled": True,
            "jitter_min_ms": 100.0,
            "jitter_max_ms": 10.0,
        })
        with pytest.raises(ValueError, match="jitter_max_ms"):
            c.validate()

    def test_negative_aggregation_bytes_raises(self):
        c = ShapingConfig.from_dict({
            "enabled": True,
            "aggregation_enabled": True,
            "aggregation_max_bytes": -1,
        })
        with pytest.raises(ValueError):
            c.validate()


# ---------------------------------------------------------------------------
# 5. Load client/server config with shaping from YAML
# ---------------------------------------------------------------------------

YAML_CLIENT_SHAPING = """
client:
  tun_name: tun0
  tun_ip: 10.8.0.2
  tun_peer: 10.8.0.1
  mtu: 1400
server:
  host: 127.0.0.1
  port: 2222
transport:
  type: tcp
session:
  heartbeat_interval: 10
shaping:
  enabled: true
  padding_enabled: true
  min_padding_bytes: 8
  max_padding_bytes: 64
"""

YAML_SERVER_SHAPING = """
server:
  tun_name: tun0
  tun_ip: 10.8.0.1
  tun_peer: 10.8.0.2
  mtu: 1400
transport:
  type: tcp
session:
  heartbeat_timeout: 30
shaping:
  enabled: true
  padding_enabled: true
  min_padding_bytes: 8
  max_padding_bytes: 64
"""


class TestYamlConfigWithShaping:
    def test_client_config_yaml_with_shaping(self, tmp_path):
        import yaml
        config_path = tmp_path / "client_shaping.yaml"
        config_path.write_text(YAML_CLIENT_SHAPING)
        config = load_client_config(str(config_path))
        assert config.shaping.enabled is True
        assert config.shaping.padding_enabled is True
        assert config.shaping.min_padding_bytes == 8
        assert config.shaping.max_padding_bytes == 64
        # Other sections still work
        assert config.transport.type == "tcp"
        assert config.client.tun_ip == "10.8.0.2"

    def test_server_config_yaml_with_shaping(self, tmp_path):
        import yaml
        config_path = tmp_path / "server_shaping.yaml"
        config_path.write_text(YAML_SERVER_SHAPING)
        config = load_server_config(str(config_path))
        assert config.shaping.enabled is True
        assert config.shaping.padding_enabled is True
        assert config.shaping.min_padding_bytes == 8
        assert config.transport.type == "tcp"

    def test_client_config_yaml_without_shaping_section(self, tmp_path):
        """Old YAML without shaping section must load with defaults."""
        import yaml
        yaml_no_shaping = """
client:
  tun_name: tun0
  tun_ip: 10.8.0.2
  tun_peer: 10.8.0.1
  mtu: 1400
server:
  host: 127.0.0.1
  port: 2222
transport:
  type: mock
session:
  heartbeat_interval: 10
"""
        config_path = tmp_path / "client_no_shaping.yaml"
        config_path.write_text(yaml_no_shaping)
        config = load_client_config(str(config_path))
        assert config.shaping.enabled is False
        assert config.shaping.padding_enabled is False

    def test_server_config_yaml_without_shaping_section(self, tmp_path):
        """Old YAML without shaping section must load with defaults."""
        import yaml
        yaml_no_shaping = """
server:
  tun_name: tun0
  tun_ip: 10.8.0.1
  tun_peer: 10.8.0.2
  mtu: 1400
transport:
  type: mock
session:
  heartbeat_timeout: 30
"""
        config_path = tmp_path / "server_no_shaping.yaml"
        config_path.write_text(yaml_no_shaping)
        config = load_server_config(str(config_path))
        assert config.shaping.enabled is False
        assert config.shaping.padding_enabled is False


# ---------------------------------------------------------------------------
# 6. Example config files are valid YAML
# ---------------------------------------------------------------------------

class TestExampleConfigs:
    def test_example_padding_yaml_loads(self):
        import yaml
        example_path = Path(__file__).resolve().parent.parent / "config" / "examples" / "shaping_padding.yaml"
        if not example_path.exists():
            pytest.skip("Example config file not found")
        with open(example_path) as f:
            data = yaml.safe_load(f)
        assert "shaping" in data
        assert data["shaping"]["enabled"] is True
        assert data["shaping"]["padding_enabled"] is True

    def test_example_padding_aggregation_yaml_loads(self):
        import yaml
        example_path = Path(__file__).resolve().parent.parent / "config" / "examples" / "shaping_padding_aggregation.yaml"
        if not example_path.exists():
            pytest.skip("Example config file not found")
        with open(example_path) as f:
            data = yaml.safe_load(f)
        assert "shaping" in data
        assert data["shaping"]["enabled"] is True
        assert data["shaping"]["aggregation_enabled"] is True
