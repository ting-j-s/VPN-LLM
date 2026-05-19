"""Tests for probe scenario definitions."""

import pytest
from src.evaluation.probe import (
    ProbeScenario,
    PROBE_SCENARIOS,
    get_scenario_by_name,
)


class TestProbeScenarioBasics:
    """Test ProbeScenario dataclass and field validation."""

    def test_scenario_creation(self):
        s = ProbeScenario(
            name="test",
            description="test description",
            payload=b"hello",
            expected_policy="silent close",
            timeout_s=3.0,
            tags=["malformed"],
        )
        assert s.name == "test"
        assert s.payload == b"hello"
        assert s.timeout_s == 3.0
        assert s.tags == ["malformed"]

    def test_scenario_none_payload(self):
        s = ProbeScenario(
            name="empty",
            description="connect and wait",
            payload=None,
            expected_policy="timeout",
        )
        assert s.payload is None


class TestProbeScenariosBuiltin:
    """Test built-in scenario definitions."""

    def test_scenario_count(self):
        assert len(PROBE_SCENARIOS) == 12

    def test_all_have_unique_names(self):
        names = [s.name for s in PROBE_SCENARIOS]
        assert len(names) == len(set(names)), f"duplicate names: {names}"

    def test_all_have_description(self):
        for s in PROBE_SCENARIOS:
            assert s.description, f"{s.name} missing description"

    def test_all_have_expected_policy(self):
        for s in PROBE_SCENARIOS:
            assert s.expected_policy, f"{s.name} missing expected_policy"

    def test_all_have_tags(self):
        for s in PROBE_SCENARIOS:
            assert s.tags, f"{s.name} missing tags"

    def test_all_have_timeout(self):
        for s in PROBE_SCENARIOS:
            assert s.timeout_s > 0, f"{s.name} has invalid timeout"

    def test_payloads_are_bytes_or_none(self):
        for s in PROBE_SCENARIOS:
            assert s.payload is None or isinstance(s.payload, bytes), \
                f"{s.name} payload is not bytes or None: {type(s.payload)}"

    def test_empty_connection_has_none_payload(self):
        s = get_scenario_by_name("empty_connection")
        assert s is not None
        assert s.payload is None

    def test_protocol_scenarios_contain_session_id_bytes(self):
        for name in ("bad_session_id", "valid_heartbeat_wrong_session"):
            s = get_scenario_by_name(name)
            assert s is not None
            assert s.payload is not None
            assert len(s.payload) >= 26  # at least frame header

    def test_get_scenario_by_name_found(self):
        s = get_scenario_by_name("one_zero")
        assert s is not None
        assert s.name == "one_zero"

    def test_get_scenario_by_name_not_found(self):
        s = get_scenario_by_name("nonexistent")
        assert s is None

    def test_tags_categorization(self):
        """Verify tag-based grouping is consistent for mock runner."""
        malformed = [s for s in PROBE_SCENARIOS if "malformed" in s.tags]
        protocol = [s for s in PROBE_SCENARIOS if "protocol" in s.tags]
        session = [s for s in PROBE_SCENARIOS if "session" in s.tags]
        assert len(malformed) >= 6
        assert len(protocol) >= 4
        assert len(session) >= 2
