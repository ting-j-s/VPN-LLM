"""Intent Contract — user-goal validation for LLM agent tasks.

Provides data structures and inference rules to capture what the user
*actually wants* (not just what the code looks like), so the pipeline
can verify that the generated patch satisfies the real intent.

Supports diverse task types: transport, shaping, evaluation, config,
docs, bugfix, refactor, script, and more.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# AcceptanceCriterion
# ---------------------------------------------------------------------------

@dataclass
class AcceptanceCriterion:
    """A single verifiable condition that must hold for the task to be done."""

    name: str
    category: str  # structure, syntax, unit_test, runtime, config, cli, docs, evaluation, trace, backward_compatibility, security_boundary, user_visible_behavior
    required: bool = True
    validation_method: str = ""
    expected_evidence: str = ""
    failure_message: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "required": self.required,
            "validation_method": self.validation_method,
            "expected_evidence": self.expected_evidence,
            "failure_message": self.failure_message,
        }


# ---------------------------------------------------------------------------
# IntentContract
# ---------------------------------------------------------------------------

VALID_IMPLEMENTATION_LEVELS = frozenset({
    "runtime",
    "config_only",
    "docs_only",
    "test_only",
    "analysis_only",
    "evaluation_only",
    "refactor",
    "bugfix",
})


@dataclass
class IntentContract:
    """Captures the user's real intent as verifiable acceptance criteria.

    Built by the Planner from the natural-language request and carried
    through the pipeline to drive prompt generation and final validation.
    """

    original_request: str = ""
    task_type: str = ""
    target_component: str | None = None
    target_transport: str | None = None
    implementation_level: str = "runtime"
    allow_stub: bool = True
    runtime_required: bool = False
    requires_default_change: bool = False
    requires_user_visible_behavior_change: bool = False
    requires_tests: bool = False
    requires_docs: bool = False
    requires_config_update: bool = False
    requires_cli_update: bool = False
    requires_trace_or_evaluation: bool = False
    requires_no_behavior_change: bool = False
    acceptance_criteria: list[AcceptanceCriterion] = field(default_factory=list)
    forbidden_degradations: list[str] = field(default_factory=list)
    validation_commands: list[str] = field(default_factory=list)
    manual_verification_hints: list[str] = field(default_factory=list)
    # End-to-end fulfillment gate
    end_to_end_required: bool = False
    must_pass_without_warnings: bool = False
    allow_partial_completion: bool = True
    expected_integration_points: list[str] = field(default_factory=list)
    runtime_wiring_required: bool = False
    default_switch_gate_required: bool = False
    required_evidence: list[str] = field(default_factory=list)
    # Phase LLM-M1: TaskModuleContract resolution
    selected_module: str | None = None
    module_resolution: dict | None = None

    def to_dict(self) -> dict:
        return {
            "original_request": self.original_request,
            "task_type": self.task_type,
            "target_component": self.target_component,
            "target_transport": self.target_transport,
            "implementation_level": self.implementation_level,
            "allow_stub": self.allow_stub,
            "runtime_required": self.runtime_required,
            "requires_default_change": self.requires_default_change,
            "requires_user_visible_behavior_change": self.requires_user_visible_behavior_change,
            "requires_tests": self.requires_tests,
            "requires_docs": self.requires_docs,
            "requires_config_update": self.requires_config_update,
            "requires_cli_update": self.requires_cli_update,
            "requires_trace_or_evaluation": self.requires_trace_or_evaluation,
            "requires_no_behavior_change": self.requires_no_behavior_change,
            "acceptance_criteria": [a.to_dict() for a in self.acceptance_criteria],
            "forbidden_degradations": self.forbidden_degradations,
            "validation_commands": self.validation_commands,
            "manual_verification_hints": self.manual_verification_hints,
            "end_to_end_required": self.end_to_end_required,
            "must_pass_without_warnings": self.must_pass_without_warnings,
            "allow_partial_completion": self.allow_partial_completion,
            "expected_integration_points": self.expected_integration_points,
            "runtime_wiring_required": self.runtime_wiring_required,
            "default_switch_gate_required": self.default_switch_gate_required,
            "required_evidence": self.required_evidence,
            "selected_module": self.selected_module,
            "module_resolution": self.module_resolution,
        }

    # ------------------------------------------------------------------
    # Prompt directive — what the PatchGenerator should tell the LLM
    # ------------------------------------------------------------------

    def build_prompt_directive(self) -> str:
        """Build a concise prompt directive for PatchGenerator based on this contract.

        Returns a string to inject into the LLM prompt that enforces
        the user's real intent.
        """
        parts: list[str] = []

        level = self.implementation_level

        if level == "runtime":
            parts.append("IMPLEMENTATION LEVEL: RUNTIME")
            parts.append("- You MUST generate a fully functional implementation — NOT a skeleton.")
            parts.append("- Do NOT satisfy the task only by adding import/factory/config stubs.")
            parts.append("- Provide working behavior required by the intent contract.")
            if self.runtime_required:
                parts.append("- connect(), send(), recv() or equivalent methods MUST work.")
            parts.append("- Add runtime or smoke tests as required by the contract.")
            parts.append("- If full implementation is too large, implement the minimal runtime")
            parts.append("  subset needed for local controlled tests, but do NOT silently")
            parts.append("  downgrade to skeleton.")

        elif level == "config_only":
            parts.append("IMPLEMENTATION LEVEL: CONFIG ONLY")
            parts.append("- Only modify or create configuration files.")
            parts.append("- Config must load and validate correctly.")
            parts.append("- Do NOT change source code behavior.")

        elif level == "docs_only":
            parts.append("IMPLEMENTATION LEVEL: DOCS ONLY")
            parts.append("- Only modify documentation files.")
            parts.append("- Do NOT change any source code behavior.")
            parts.append("- Do NOT modify .py files unless explicitly requested.")

        elif level == "test_only":
            parts.append("IMPLEMENTATION LEVEL: TEST ONLY")
            parts.append("- Only add or modify test files.")
            parts.append("- Do NOT change source code behavior.")

        elif level == "analysis_only" or level == "evaluation_only":
            parts.append("IMPLEMENTATION LEVEL: EVALUATION/ANALYSIS")
            parts.append("- Add or update evaluation/detection/report logic.")
            parts.append("- Add tests for metric extraction, parsing, and scoring.")
            parts.append("- Report fields must appear in output.")
            if self.requires_trace_or_evaluation:
                parts.append("- Provide before/after comparison or synthetic fixture evidence.")

        elif level == "refactor":
            parts.append("IMPLEMENTATION LEVEL: REFACTOR")
            parts.append("- Preserve all existing behavior.")
            parts.append("- Existing tests must pass unchanged (or with minimal updates).")
            parts.append("- Do NOT introduce new features or change public APIs unless requested.")
            if self.requires_no_behavior_change:
                parts.append("- NO behavior change is allowed.")

        elif level == "bugfix":
            parts.append("IMPLEMENTATION LEVEL: BUGFIX")
            parts.append("- Fix the reported bug.")
            parts.append("- Add or update a regression test that fails before and passes after.")
            parts.append("- Do NOT make unrelated changes.")
            if self.requires_user_visible_behavior_change:
                parts.append("- The fix MUST change user-visible behavior.")

        # Cross-cutting constraints
        if self.requires_default_change:
            target = self.target_transport or "the target"
            parts.append(f"DEFAULT SWITCH REQUESTED: Setting '{target}' as default.")
            parts.append("- Target MUST be runtime-usable before switching defaults.")
            parts.append("- DO NOT modify default config unless runtime smoke tests exist.")

        if self.requires_no_behavior_change:
            parts.append("CONSTRAINT: NO BEHAVIOR CHANGE")
            parts.append("- Do NOT change runtime behavior except as explicitly requested.")
            parts.append("- Existing behavior tests must remain valid.")

        if self.requires_trace_or_evaluation:
            parts.append("CONSTRAINT: TRACE/EVALUATION EVIDENCE REQUIRED")
            parts.append("- Add or update report fields.")
            parts.append("- Add tests for metric extraction/scoring.")
            parts.append("- If possible, provide before/after command or synthetic fixture.")

        if self.requires_tests:
            parts.append("CONSTRAINT: TESTS REQUIRED")
            parts.append("- You MUST include test files or test modifications.")

        if self.requires_docs:
            parts.append("CONSTRAINT: DOCS REQUIRED")
            parts.append("- You MUST include documentation updates.")

        if self.requires_cli_update:
            parts.append("CONSTRAINT: CLI UPDATE REQUIRED")
            parts.append("- Script must support --help and produce expected output on sample input.")

        # End-to-end gate
        if self.end_to_end_required:
            parts.append("END-TO-END FULFILLMENT REQUIRED:")
            parts.append("- This task requires FULL end-to-end delivery — not partial completion.")
            parts.append("- Do NOT return a partial implementation and mark it complete.")
            parts.append("- All required acceptance criteria MUST be satisfied.")
            parts.append("- completed_with_warnings is NOT acceptable for this request.")
            if self.runtime_wiring_required:
                parts.append("- The implementation MUST be wired through factory/config.")
                parts.append("- Do NOT create a parallel *_full_transport.py or *_runtime_transport.py file.")
                parts.append("- Do NOT bypass the existing registration path.")
                if self.target_transport:
                    parts.append(f"- create_transport(type=\"{self.target_transport}\") MUST return "
                                 f"the runtime-capable implementation.")
            if self.expected_integration_points:
                parts.append("- Required integration points (all must be in the patch):")
                for pt in self.expected_integration_points:
                    parts.append(f"  * {pt}")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# IntentContract inference engine (rule-based)
# ---------------------------------------------------------------------------

def infer_intent_contract(
    request: str,
    task_type: str = "",
    target_transport: str | None = None,
    target_component: str | None = None,
) -> IntentContract:
    """Infer an IntentContract from a natural-language request.

    Uses deterministic keyword rules for high-confidence classification.
    LLM planner output can supplement but must not contradict rule-layer
    hard constraints.

    Args:
        request: Natural language request string.
        task_type: Pre-classified task type (from Planner), or "".
        target_transport: Extracted transport name, if any.
        target_component: Extracted component name, if any.

    Returns:
        IntentContract with inferred fields and acceptance criteria.
    """
    text = request.lower().strip()

    contract = IntentContract(
        original_request=request.strip(),
        task_type=task_type,
        target_transport=target_transport,
        target_component=target_component,
    )

    # ---- Step 1: Detect implementation level from keywords ----

    impl_level, impl_signals = _detect_implementation_level(text, task_type)
    contract.implementation_level = impl_level

    # ---- Step 2: Derive boolean flags ----

    contract.runtime_required = impl_level == "runtime"
    contract.allow_stub = False
    contract.requires_default_change = _has_any(text, _DEFAULT_SWITCH_KEYWORDS)
    contract.requires_user_visible_behavior_change = (
        contract.runtime_required or impl_level == "bugfix"
    )
    contract.requires_tests = (
        _has_any(text, _TEST_KEYWORDS)
        or impl_level in ("runtime", "bugfix", "evaluation_only")
    )
    contract.requires_docs = _has_any(text, _DOCS_KEYWORDS) or impl_level == "docs_only"
    contract.requires_config_update = (
        _has_any(text, _CONFIG_KEYWORDS) or impl_level == "config_only"
    )
    contract.requires_cli_update = (
        _has_any(text, _SCRIPT_KEYWORDS)
        or _has_any(text, _CLI_KEYWORDS)
    )
    contract.requires_trace_or_evaluation = (
        _has_any(text, _EVALUATION_KEYWORDS)
        or impl_level == "evaluation_only"
    )
    contract.requires_no_behavior_change = (
        _has_any(text, _NO_BEHAVIOR_CHANGE_KEYWORDS)
        or impl_level in ("docs_only", "refactor", "config_only")
    )

    # ---- Step 3: Hard constraint overrides ----

    # "默认/替换默认/设为默认" → force runtime, forbid stub
    if contract.requires_default_change:
        contract.runtime_required = True
        contract.allow_stub = False

    # docs_only must not have runtime_required
    if impl_level == "docs_only":
        contract.runtime_required = False
        contract.requires_no_behavior_change = True

    # bugfix must require tests
    if impl_level == "bugfix":
        contract.requires_tests = True

    # refactor must preserve behavior
    if impl_level == "refactor":
        contract.requires_no_behavior_change = True

    # ---- Step 3b: End-to-end fulfillment gate ----

    # Detect end-to-end requirement from explicit keywords
    has_end_to_end = _has_any(text, _END_TO_END_KEYWORDS)
    has_skeleton_stub = _has_any(text, _SKELETON_KEYWORDS)
    has_runtime_kw = _has_any(text, _RUNTIME_KEYWORDS)

    if has_end_to_end or contract.runtime_required:
        contract.end_to_end_required = True
        contract.must_pass_without_warnings = True
        contract.allow_partial_completion = False
        contract.allow_stub = False

    # requires_default_change always forces end-to-end
    if contract.requires_default_change:
        contract.end_to_end_required = True
        contract.must_pass_without_warnings = True
        contract.runtime_required = True
        contract.allow_stub = False
        contract.allow_partial_completion = False
        contract.default_switch_gate_required = True

    # runtime_required + transport_addition → wiring required
    if (contract.runtime_required
            and task_type in ("transport_addition", "feature_addition", "transport_change")
            and contract.target_transport):
        contract.runtime_wiring_required = True
        t = contract.target_transport
        contract.expected_integration_points = [
            f"src/transport/{t}_transport.py",
            "src/transport/factory.py",
            "src/common/config.py",
            f"tests/test_{t}_transport.py",
            f"docs/transports/{t}.md",
            f"config/examples/{t}_transport.yaml",
        ]

    # ---- Step 4: Build acceptance criteria ----

    contract.acceptance_criteria = _build_acceptance_criteria(contract, text)

    # ---- Step 5: Forbidden degradations ----

    contract.forbidden_degradations = _build_forbidden_degradations(contract)

    # ---- Step 6: Validation commands ----

    contract.validation_commands = _build_validation_commands(contract)

    # ---- Step 7: Manual verification hints ----

    contract.manual_verification_hints = _build_manual_hints(contract)

    return contract


# ---------------------------------------------------------------------------
# Keyword sets
# ---------------------------------------------------------------------------

_SKELETON_KEYWORDS = (
    "skeleton", "stub", "placeholder", "占位", "骨架", "空壳",
    "先加框架", "不要求运行", "not yet implement", "not fully implement",
    "skeleton only", "skeleton-only", "placeholder only",
    "先做 skeleton", "初版", "不要求完整", "先搭框架",
)

_RUNTIME_KEYWORDS = (
    "使用", "能用", "可运行", "真实通信", "真实数据", "实际使用",
    "替换默认", "默认外层协议", "改为默认",
    "make it default", "make it the default", "use as default",
    "switch default", "set as default", "replace default",
    "change default transport", "change the default transport",
    "set as the default",
    "作为新的外层协议", "新的外层协议",
    "as new outer protocol", "as the new outer protocol",
    "new outer protocol",
    "run", "working", "usable", "end-to-end", "smoke",
)

_DEFAULT_SWITCH_KEYWORDS = (
    "替换默认", "默认外层协议", "改为默认",
    "make it default", "make it the default", "use as default",
    "switch default", "set as default", "replace default",
    "change default transport", "change the default transport",
    "set as the default",
)

_DOCS_KEYWORDS = (
    "文档", "readme", "说明", "报告", "总结", "documentation",
    "doc", "docs",
)

_EVALUATION_KEYWORDS = (
    "检测", "评估", "risk", "trace", "report", "metric", "gate",
    "before/after", "before", "after", "指标",
)

_CONFIG_KEYWORDS = (
    "配置", "yaml", "example config", "默认参数", "config", "configuration",
)

_BUGFIX_KEYWORDS = (
    "修复", "bug", "fail", "regression", "error", "traceback",
    "fix", "修理", "bugfix",
)

_REFACTOR_KEYWORDS = (
    "重构", "refactor", "不改变行为", "cleanup",
)

_TEST_KEYWORDS = (
    "test", "tests", "测试", "pytest",
)

_SCRIPT_KEYWORDS = (
    "script", "脚本", "批量",
)

_CLI_KEYWORDS = (
    "--help", "命令行", "cli",
)

_NO_BEHAVIOR_CHANGE_KEYWORDS = (
    "不改变行为", "no behavior change", "without behavior change",
    "保持行为", "preserve behavior",
)

_END_TO_END_KEYWORDS = (
    "全程跑通", "跑通", "能用", "使用", "接入",
    "实验验证", "端到端",
)

_TRANSPORT_NAME_RE = re.compile(
    r'(?:add|new|create|implement|generate|use|采用|使用|生成)\s+'
    r'(?:a\s+)?(?:new\s+)?(\w[\w.-]*?)\s+'
    r'(?:transport|protocol|外层协议)'
)

_DEFAULT_TARGET_RE = re.compile(
    r'(?:默认|default)\s*(?:外层协议|transport|protocol)?\s*'
    r'(?:为|是|to|as)?\s*(\w[\w.-]*)'
)


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(kw in text for kw in keywords)


def _detect_implementation_level(text: str, task_type: str) -> tuple[str, dict]:
    """Detect implementation_level and return (level, signals dict)."""
    signals = {
        "has_skeleton": _has_any(text, _SKELETON_KEYWORDS),
        "has_runtime": _has_any(text, _RUNTIME_KEYWORDS),
        "has_docs": _has_any(text, _DOCS_KEYWORDS) and not _has_any(text, _RUNTIME_KEYWORDS),
        "has_evaluation": _has_any(text, _EVALUATION_KEYWORDS),
        "has_config": _has_any(text, _CONFIG_KEYWORDS) and not _has_any(text, _RUNTIME_KEYWORDS),
        "has_bugfix": _has_any(text, _BUGFIX_KEYWORDS),
        "has_refactor": _has_any(text, _REFACTOR_KEYWORDS),
        "has_script": _has_any(text, _SCRIPT_KEYWORDS) or _has_any(text, _CLI_KEYWORDS),
    }

    # Priority-ordered detection
    # NOTE: skeleton is no longer a valid level — all requests are treated
    # as requiring full runtime capability.

    # Transport/feature additions default to runtime, but evaluation-only
    # requests with no runtime keywords take precedence
    if task_type in ("transport_addition", "feature_addition", "transport_change"):
        if signals["has_evaluation"] and not signals["has_runtime"]:
            return "evaluation_only", signals
        return "runtime", signals

    if signals["has_bugfix"]:
        return "bugfix", signals

    if signals["has_refactor"]:
        return "refactor", signals

    if signals["has_evaluation"] and not signals["has_runtime"]:
        return "evaluation_only", signals

    if signals["has_docs"] and not signals["has_config"] and not signals["has_runtime"]:
        return "docs_only", signals

    if signals["has_config"] and not signals["has_runtime"]:
        return "config_only", signals

    if signals["has_script"]:
        return "runtime", signals  # scripts need to be runnable

    if signals["has_runtime"]:
        return "runtime", signals

    # Default by task_type
    if task_type == "docs_update":
        return "docs_only", signals
    if task_type == "config_change":
        return "config_only", signals
    if task_type == "test_addition":
        return "test_only", signals

    return "runtime", signals


def _detect_transport_name(text: str) -> str | None:
    m = _TRANSPORT_NAME_RE.search(text)
    if m:
        name = m.group(1).strip().lower()
        if name not in ("the", "this", "that", "and", "for", "with", "from",
                        "default", "current", "existing", "new", "a", "an"):
            return name
    return None


def _detect_default_target(text: str) -> str | None:
    m = _DEFAULT_TARGET_RE.search(text)
    if m:
        name = m.group(1).strip().lower()
        if name not in ("the", "this", "that", "and", "for", "with", "from",
                        "default", "current", "existing", "new", "a", "an",
                        "transport", "protocol"):
            return name
    return None


# ---------------------------------------------------------------------------
# Acceptance criteria builders
# ---------------------------------------------------------------------------

def _build_acceptance_criteria(contract: IntentContract, text: str) -> list[AcceptanceCriterion]:
    """Build task-type-specific acceptance criteria."""
    criteria: list[AcceptanceCriterion] = []
    level = contract.implementation_level

    # Universal criteria
    criteria.append(AcceptanceCriterion(
        name="syntax_valid",
        category="syntax",
        required=True,
        validation_method="python3 -m compileall src tests",
        expected_evidence="No SyntaxError for any .py file in the patch",
        failure_message="Patch contains Python syntax errors",
    ))

    # Runtime transport (required for all transport/feature additions)
    if (contract.task_type in ("transport_addition", "feature_addition", "transport_change")
            and contract.implementation_level != "evaluation_only"):
        criteria.extend([
            AcceptanceCriterion(
                name="importable_and_constructable",
                category="structure",
                required=True,
                validation_method="python3 -c 'from src.transport.{name}_transport import {Name}Transport; t = {Name}Transport()'",
                expected_evidence="Class imports and constructs without error",
                failure_message="Transport class is not importable or constructable",
            ),
            AcceptanceCriterion(
                name="factory_registered",
                category="structure",
                required=True,
                validation_method="grep for transport name in factory.py and config.py",
                expected_evidence="Transport appears in SUPPORTED_TRANSPORTS and ALLOWED_TRANSPORT_TYPES",
                failure_message="Transport not registered in factory or config",
            ),
            AcceptanceCriterion(
                name="runtime_connect",
                category="runtime",
                required=True,
                validation_method="Check connect() establishes real connection (no raise TransportError placeholder)",
                expected_evidence="connect() method body contains socket/listen/connect logic, not raise TransportError",
                failure_message="connect() is skeleton-only, must establish real connection",
            ),
            AcceptanceCriterion(
                name="runtime_send_recv",
                category="runtime",
                required=True,
                validation_method="Check send()/recv() transmit actual data",
                expected_evidence="send() and recv() methods contain data transmission logic",
                failure_message="send()/recv() are skeleton-only, must transmit actual data",
            ),
            AcceptanceCriterion(
                name="roundtrip_test",
                category="unit_test",
                required=True,
                validation_method="Check test file for roundtrip/send_recv/client_server test",
                expected_evidence="Test function or class exercising data roundtrip",
                failure_message="No roundtrip test found for runtime transport",
            ),
        ])
        if not contract.requires_default_change:
            criteria.append(AcceptanceCriterion(
                name="default_switch_forbidden",
                category="security_boundary",
                required=True,
                validation_method="Verify default transport config is unchanged",
                expected_evidence="config/client.yaml and config/server.yaml still point to previous default",
                failure_message="Must not be set as default without explicit request",
            ))
        # Runtime transport must update docs and provide config example
        criteria.extend([
            AcceptanceCriterion(
                name="docs_updated",
                category="docs",
                required=True,
                validation_method="Check docs/transports/{name}.md exists or was updated in patch",
                expected_evidence="Transport documentation reflects runtime capability",
                failure_message="Transport docs not updated to reflect runtime status",
            ),
            AcceptanceCriterion(
                name="config_example_exists",
                category="config",
                required=True,
                validation_method="Check config/examples/{name}_transport.yaml exists or was created in patch",
                expected_evidence="Example config file for the transport",
                failure_message="No config example for transport",
            ),
            AcceptanceCriterion(
                name="wired_correctly",
                category="structure",
                required=True,
                validation_method="Check transport file is at expected path src/transport/{name}_transport.py and not a bypass file like *_full_transport.py",
                expected_evidence="Transport file is at the canonical path, not a bypass file",
                failure_message="Runtime implementation is not wired to requested transport name",
            ),
        ])

    # Default switch
    if contract.requires_default_change:
        criteria.extend([
            AcceptanceCriterion(
                name="default_config_load",
                category="config",
                required=True,
                validation_method="Verify default config loads with new transport type",
                expected_evidence="Config parses without error when transport type is changed",
                failure_message="Default config does not support the new transport type",
            ),
            AcceptanceCriterion(
                name="runtime_gate_for_default",
                category="security_boundary",
                required=True,
                validation_method="Verify target transport is runtime-usable before switching default",
                expected_evidence="Transport has working connect/send/recv and roundtrip tests",
                failure_message="Cannot switch default to skeleton transport",
            ),
        ])

    # Docs only
    if level == "docs_only":
        criteria.append(AcceptanceCriterion(
            name="no_source_changes",
            category="backward_compatibility",
            required=True,
            validation_method="git diff shows only .md or doc file changes",
            expected_evidence="No .py file modifications in the patch",
            failure_message="Docs-only task modified source code",
        ))

    # Config only
    if level == "config_only":
        criteria.append(AcceptanceCriterion(
            name="config_loads",
            category="config",
            required=True,
            validation_method="YAML parse or config load function succeeds",
            expected_evidence="Config parses without error",
            failure_message="Config file is invalid or does not load",
        ))

    # Evaluation
    if level == "evaluation_only":
        criteria.extend([
            AcceptanceCriterion(
                name="metric_in_output",
                category="evaluation",
                required=True,
                validation_method="Check report fields include the new metric",
                expected_evidence="Metric appears in evaluation output fields",
                failure_message="New metric not found in evaluation output",
            ),
            AcceptanceCriterion(
                name="metric_tests",
                category="unit_test",
                required=True,
                validation_method="Test for metric extraction/scoring exists",
                expected_evidence="Test covers parsing and scoring of the new metric",
                failure_message="No tests for new evaluation metric",
            ),
        ])

    # Bugfix
    if level == "bugfix":
        criteria.append(AcceptanceCriterion(
            name="regression_test",
            category="unit_test",
            required=True,
            validation_method="Check that a test was added or updated for the bug",
            expected_evidence="Test that fails before fix and passes after fix",
            failure_message="Bugfix must include a regression test",
        ))

    # Refactor
    if level == "refactor":
        criteria.append(AcceptanceCriterion(
            name="existing_tests_pass",
            category="backward_compatibility",
            required=True,
            validation_method="python3 -m pytest tests/ -q",
            expected_evidence="All existing tests pass unchanged",
            failure_message="Refactor broke existing tests",
        ))

    # Script
    if contract.requires_cli_update:
        criteria.extend([
            AcceptanceCriterion(
                name="help_works",
                category="cli",
                required=True,
                validation_method="python3 script.py --help",
                expected_evidence="--help produces usage info with exit code 0",
                failure_message="Script --help does not work",
            ),
            AcceptanceCriterion(
                name="sample_io",
                category="cli",
                required=True,
                validation_method="Run script with sample input and check output",
                expected_evidence="Script produces expected output format (JSON/MD/text)",
                failure_message="Script does not produce expected output on sample input",
            ),
        ])

    # Trace/evaluation
    if contract.requires_trace_or_evaluation:
        criteria.append(AcceptanceCriterion(
            name="before_after_evidence",
            category="evaluation",
            required=False,  # optional — synthetic fixture may substitute
            validation_method="Before/after comparison or synthetic fixture",
            expected_evidence="Metric values show expected change direction",
            failure_message="No before/after evidence for evaluation task",
        ))

    return criteria


def _build_forbidden_degradations(contract: IntentContract) -> list[str]:
    """Build the list of forbidden degradations."""
    degradations: list[str] = []

    if contract.runtime_required and not contract.allow_stub:
        degradations.append(
            "SILENT DOWNGRADE: Must not generate skeleton-only implementation "
            "when runtime behavior is required"
        )

    if contract.requires_default_change:
        degradations.append(
            "DEFAULT SWITCH TO SKELETON: Must not switch default transport "
            "to a skeleton-only transport"
        )

    if contract.requires_no_behavior_change:
        degradations.append(
            "BEHAVIOR CHANGE: Must not change existing runtime behavior "
            "except as explicitly requested"
        )

    if contract.implementation_level == "docs_only":
        degradations.append(
            "SOURCE CODE CHANGE: Must not modify .py files in docs-only task"
        )

    if contract.implementation_level == "bugfix":
        degradations.append(
            "NO REGRESSION TEST: Bugfix must include a regression test"
        )

    return degradations


def _build_validation_commands(contract: IntentContract) -> list[str]:
    """Build suggested validation commands."""
    commands: list[str] = []

    if contract.requires_tests:
        if contract.target_transport:
            commands.append(
                f"python3 -m pytest tests/test_{contract.target_transport}_transport.py -v"
            )
        commands.append("python3 -m pytest tests/ -q")

    if contract.requires_config_update:
        commands.append("python3 -c 'from src.common.config import load_config; load_config()'")

    if contract.requires_cli_update:
        commands.append("python3 <script> --help")

    return commands


def _build_manual_hints(contract: IntentContract) -> list[str]:
    """Build manual verification hints."""
    hints: list[str] = []

    if contract.runtime_required:
        hints.append(
            "Verify that the component actually transmits data end-to-end, "
            "not just passes unit tests"
        )

    if contract.requires_default_change:
        hints.append(
            "Manually verify that the default config change does not break "
            "existing client/server smoke tests"
        )

    if contract.requires_trace_or_evaluation:
        hints.append(
            "Run the evaluation on synthetic or real trace data and verify "
            "the metric values make sense"
        )

    if contract.requires_no_behavior_change:
        hints.append(
            "Run the full test suite and verify no existing tests break"
        )

    return hints


# ---------------------------------------------------------------------------
# Backward-compatible wrapper for analyze_implementation_level
# ---------------------------------------------------------------------------

def analyze_implementation_level_v2(
    request: str,
    task_type: str = "",
    target_transport: str | None = None,
) -> dict:
    """V2 replacement for task_rules.analyze_implementation_level.

    Returns a dict compatible with the old interface, plus the full
    IntentContract so callers can access the richer data.
    """
    contract = infer_intent_contract(request, task_type=task_type,
                                      target_transport=target_transport)
    target = _detect_default_target(request.lower())

    return {
        "implementation_level": contract.implementation_level,
        "runtime_required": contract.runtime_required,
        "allow_skeleton": contract.allow_stub,
        "requires_default_switch": contract.requires_default_change,
        "default_transport_target": target or contract.target_transport,
        # New fields
        "requires_tests": contract.requires_tests,
        "requires_docs": contract.requires_docs,
        "requires_config_update": contract.requires_config_update,
        "requires_cli_update": contract.requires_cli_update,
        "requires_trace_or_evaluation": contract.requires_trace_or_evaluation,
        "requires_no_behavior_change": contract.requires_no_behavior_change,
        "requires_user_visible_behavior_change": contract.requires_user_visible_behavior_change,
        "intent_contract": contract,
        # End-to-end gate
        "end_to_end_required": contract.end_to_end_required,
        "must_pass_without_warnings": contract.must_pass_without_warnings,
        "runtime_wiring_required": contract.runtime_wiring_required,
    }
