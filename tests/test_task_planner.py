"""Tests for TaskPlanner."""

from src.llm.task_planner import (
    TaskPlanner,
    TaskPlan,
    TASK_TRANSPORT_CHANGE,
    TASK_CONFIG_CHANGE,
    TASK_TEST_ADDITION,
    TASK_DOCS_UPDATE,
    TASK_CORE_CHANGE,
    TASK_BUGFIX,
    TASK_REFACTOR,
    TASK_UNKNOWN,
)


class TestTaskPlan:
    """Test TaskPlan dataclass."""

    def test_create_transport_change_plan(self):
        plan = TaskPlan(
            task_type=TASK_TRANSPORT_CHANGE,
            description="switch to websocket",
            target_transport="websocket",
            affected_areas=["src/transport/", "config/"],
        )
        assert plan.task_type == TASK_TRANSPORT_CHANGE
        assert plan.target_transport == "websocket"
        assert len(plan.affected_areas) == 2

    def test_create_unknown_plan(self):
        plan = TaskPlan(
            task_type=TASK_UNKNOWN,
            description="do something unclear",
            affected_areas=[],
        )
        assert plan.task_type == TASK_UNKNOWN
        assert plan.target_transport is None


class TestTaskPlannerTransportChange:
    """Test classification of transport change requests."""

    def test_detect_websocket_change(self):
        plan = TaskPlanner.plan("把默认外层协议从 TCP 改成 WebSocket")
        assert plan.task_type == TASK_TRANSPORT_CHANGE
        assert plan.target_transport == "websocket"

    def test_detect_websocket_change_english(self):
        plan = TaskPlanner.plan("switch default transport to WebSocket")
        assert plan.task_type == TASK_TRANSPORT_CHANGE
        assert plan.target_transport == "websocket"

    def test_detect_tcp_change(self):
        plan = TaskPlanner.plan("change transport to tcp")
        assert plan.task_type == TASK_TRANSPORT_CHANGE
        assert plan.target_transport == "tcp"

    def test_detect_tls_change(self):
        plan = TaskPlanner.plan("use TLS transport instead")
        assert plan.task_type == TASK_TRANSPORT_CHANGE
        assert plan.target_transport == "tls"

    def test_detect_ssh_change(self):
        plan = TaskPlanner.plan("switch to ssh transport")
        assert plan.task_type == TASK_TRANSPORT_CHANGE
        assert plan.target_transport == "ssh"

    def test_detect_mock_transport(self):
        plan = TaskPlanner.plan("use mock transport for testing")
        assert plan.task_type == TASK_TRANSPORT_CHANGE
        assert plan.target_transport == "mock"

    def test_transport_change_affected_areas(self):
        plan = TaskPlanner.plan("switch to WebSocket")
        assert "src/transport/" in plan.affected_areas
        assert "config/" in plan.affected_areas


class TestTaskPlannerOtherTypes:
    """Test classification of non-transport requests."""

    def test_detect_config_change(self):
        plan = TaskPlanner.plan("修改服务器配置端口为 3333")
        assert plan.task_type == TASK_CONFIG_CHANGE

    def test_detect_test_addition(self):
        plan = TaskPlanner.plan("add test for websocket transport timeout")
        assert plan.task_type == TASK_TRANSPORT_CHANGE  # transport keyword takes priority

    def test_detect_test_addition_pure(self):
        plan = TaskPlanner.plan("add more unit tests for the project")
        assert plan.task_type == TASK_TEST_ADDITION

    def test_detect_docs_update(self):
        plan = TaskPlanner.plan("update the documentation for TLS configuration")
        assert plan.task_type == TASK_TRANSPORT_CHANGE  # TLS keyword takes priority

    def test_detect_docs_update_pure(self):
        plan = TaskPlanner.plan("update the README documentation")
        assert plan.task_type == TASK_DOCS_UPDATE

    def test_unknown_request(self):
        plan = TaskPlanner.plan("do something amazing with this project")
        assert plan.task_type == TASK_UNKNOWN
        assert plan.target_transport is None


class TestTaskPlannerCoreChange:
    """Test classification of core change requests."""

    def test_detect_core_change_session_chinese(self):
        plan = TaskPlanner.plan("替换 VPN Core 的会话校验策略")
        assert plan.task_type == TASK_CORE_CHANGE
        assert "src/core/" in plan.affected_areas
        assert "tests/test_core.py" in plan.affected_areas

    def test_detect_core_change_forwarding_english(self):
        plan = TaskPlanner.plan("replace the VPN core forwarding strategy")
        assert plan.task_type == TASK_CORE_CHANGE

    def test_detect_core_change_frame_codec(self):
        plan = TaskPlanner.plan("修改 Frame 的编码方式")
        assert plan.task_type == TASK_CORE_CHANGE

    def test_detect_core_change_nat(self):
        plan = TaskPlanner.plan("update the NAT routing logic in the core")
        assert plan.task_type == TASK_CORE_CHANGE

    def test_core_change_affected_areas(self):
        plan = TaskPlanner.plan("替换 Tun 设备的处理逻辑")
        assert "src/core/" in plan.affected_areas
        assert "src/common/" in plan.affected_areas


class TestTaskPlannerRefactorAndBugfix:
    """Test classification of refactor and bugfix requests."""

    def test_detect_bugfix(self):
        plan = TaskPlanner.plan("fix memory leak in transport layer")
        assert plan.task_type == TASK_BUGFIX

    def test_detect_bugfix_chinese(self):
        plan = TaskPlanner.plan("修复 transport 中的崩溃 bug")
        assert plan.task_type == TASK_BUGFIX

    def test_detect_refactor(self):
        plan = TaskPlanner.plan("refactor the transport factory to use registry pattern")
        assert plan.task_type == TASK_REFACTOR

    def test_detect_refactor_chinese(self):
        plan = TaskPlanner.plan("重构 transport factory 模块")
        assert plan.task_type == TASK_REFACTOR

    def test_detect_refactor_pure(self):
        plan = TaskPlanner.plan("rewrite the utility helpers with cleaner code")
        assert plan.task_type == TASK_REFACTOR


class TestTaskPlannerEdgeCases:
    """Test edge cases for the planner."""

    def test_empty_request(self):
        plan = TaskPlanner.plan("")
        assert plan.task_type == TASK_UNKNOWN

    def test_whitespace_only(self):
        plan = TaskPlanner.plan("   ")
        assert plan.task_type == TASK_UNKNOWN

    def test_case_insensitivity(self):
        plan = TaskPlanner.plan("USE WEBSOCKET TRANSPORT")
        assert plan.task_type == TASK_TRANSPORT_CHANGE
        assert plan.target_transport == "websocket"

    def test_preserves_original_description(self):
        original = "switch transport to WebSocket please"
        plan = TaskPlanner.plan(original)
        assert plan.description == original
