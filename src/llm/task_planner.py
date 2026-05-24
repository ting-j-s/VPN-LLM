"""Task Planner for LLM Agent.

Classifies user requests and generates structured TaskPlans.
MVP: rule-based classification, no real LLM call.
"""

from dataclasses import dataclass, field
from typing import Optional

from src.llm.task_rules import analyze_implementation_level
from src.llm.intent_contract import IntentContract, infer_intent_contract


# Task type constants
TASK_TRANSPORT_CHANGE = "transport_change"
TASK_CONFIG_CHANGE = "config_change"
TASK_TEST_ADDITION = "test_addition"
TASK_DOCS_UPDATE = "docs_update"
TASK_CORE_CHANGE = "core_change"
TASK_BUGFIX = "bugfix"
TASK_REFACTOR = "refactor"
TASK_UNKNOWN = "unknown"

TRANSPORT_KEYWORDS = {
    "websocket": "websocket",
    "tcp": "tcp",
    "tls": "tls",
    "ssh": "ssh",
    "mock": "mock",
    "socks5": "socks5",
    "socks": "socks5",
}

CORE_KEYWORDS = (
    "core", "内核", "session", "会话", "forwarding", "转发",
    "frame", "帧", "codec", "编码", "tun", "nat", "route", "路由",
)


@dataclass
class TaskPlan:
    """Structured plan produced from a user request.

    candidate_files is a planner hint only — the final selected_files must
    come from RepoIndexer + FileRetriever + ImpactExpander.
    """

    task_type: str
    description: str
    target_transport: str | None = None
    affected_areas: list[str] = field(default_factory=list)
    candidate_files: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    validation_goals: list[str] = field(default_factory=list)
    ambiguity: list[str] = field(default_factory=list)
    # Implementation-level fields (derived from request analysis)
    implementation_level: str = "skeleton"
    runtime_required: bool = False
    allow_skeleton: bool = True
    requires_default_switch: bool = False
    default_transport_target: str | None = None
    # Full intent contract for user-goal validation (V2)
    intent_contract: Optional[IntentContract] = None


class TaskPlanner:
    """Rule-based classifier for user requests.

    Usage:
        planner = TaskPlanner()
        plan = planner.plan("switch default transport to WebSocket")
        assert plan.task_type == "transport_change"
        assert plan.target_transport == "websocket"
    """

    @staticmethod
    def plan(request: str) -> TaskPlan:
        """Analyze a natural-language request and produce a TaskPlan.

        Args:
            request: Natural language request string.

        Returns:
            TaskPlan with task_type, description, target_transport, and affected_areas.
        """
        request_lower = request.lower().strip()

        task_type = TaskPlanner._classify(request_lower)
        target_transport = TaskPlanner._extract_transport(request_lower)
        affected_areas = TaskPlanner._extract_areas(request_lower, task_type, target_transport)

        impl = analyze_implementation_level(request, task_type)

        # Build full IntentContract for downstream intent validation
        intent_contract = impl.get("intent_contract") or infer_intent_contract(
            request,
            task_type=task_type,
            target_transport=target_transport,
        )

        return TaskPlan(
            task_type=task_type,
            description=request.strip(),
            target_transport=target_transport,
            affected_areas=affected_areas,
            implementation_level=impl["implementation_level"],
            runtime_required=impl["runtime_required"],
            allow_skeleton=impl["allow_skeleton"],
            requires_default_switch=impl["requires_default_switch"],
            default_transport_target=impl["default_transport_target"],
            intent_contract=intent_contract,
        )

    @staticmethod
    def _classify(request_lower: str) -> str:
        """Classify the request into a task type."""
        if any(kw in request_lower for kw in TRANSPORT_KEYWORDS):
            return TASK_TRANSPORT_CHANGE

        if any(kw in request_lower for kw in CORE_KEYWORDS):
            return TASK_CORE_CHANGE

        if any(kw in request_lower for kw in ("config", "configuration", "设置", "配置")):
            return TASK_CONFIG_CHANGE

        if any(kw in request_lower for kw in ("test", "测试", "add test")):
            return TASK_TEST_ADDITION

        if any(kw in request_lower for kw in ("doc", "readme", "文档", "documentation")):
            return TASK_DOCS_UPDATE

        if any(kw in request_lower for kw in ("fix", "bug", "修复", "bugfix", "修理")):
            return TASK_BUGFIX

        if any(kw in request_lower for kw in ("refactor", "重构", "rewrite", "重写")):
            return TASK_REFACTOR

        return TASK_UNKNOWN

    @staticmethod
    def _extract_transport(request_lower: str) -> str | None:
        """Extract the target transport from the request, if any."""
        for keyword, transport in TRANSPORT_KEYWORDS.items():
            if keyword in request_lower:
                return transport
        return None

    @staticmethod
    def _extract_areas(request_lower: str, task_type: str, transport: str | None) -> list[str]:
        """Determine which areas of the project are affected."""
        areas = []

        if task_type == TASK_TRANSPORT_CHANGE:
            areas.append("src/transport/")
            areas.append("config/")
            if transport:
                areas.append(f"src/transport/{transport}_transport.py")
        elif task_type == TASK_CORE_CHANGE:
            areas.append("src/core/")
            areas.append("src/common/")
            areas.append("tests/test_core.py")
        elif task_type == TASK_CONFIG_CHANGE:
            areas.append("config/")
        elif task_type == TASK_TEST_ADDITION:
            areas.append("tests/")
        elif task_type == TASK_DOCS_UPDATE:
            areas.append("docs/")
        elif task_type == TASK_BUGFIX:
            areas.append("src/")
            areas.append("tests/")
        elif task_type == TASK_REFACTOR:
            areas.append("src/")
            areas.append("tests/")

        return areas
