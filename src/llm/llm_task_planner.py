"""LLM-based Task Planner.

Uses an OpenAI-compatible API to classify user requests and generate structured
task plans. All LLM output is validated against a strict schema and checked by
the SafetyGuard before being returned.

The LLM is ONLY used for planning — it cannot execute commands, write files,
or modify code.
"""

import json
import os
import urllib.request
import urllib.error

import yaml

from src.llm.safety_guard import SafetyGuard, SafetyError
from src.llm.task_rules import analyze_implementation_level
from src.llm.intent_contract import IntentContract, infer_intent_contract


def _verbose_print(msg: str) -> None:
    """Print verbose output to stderr."""
    import sys
    print(msg, file=sys.stderr, flush=True)


# Valid task types (extended from rule-based planner)
VALID_TASK_TYPES = frozenset({
    "transport_change",
    "transport_addition",
    "config_change",
    "test_addition",
    "docs_update",
    "core_change",
    "feature_addition",
    "mixed_feature_change",
    "bugfix",
    "refactor",
    "fingerprint_mitigation",
    "traffic_shaping",
    "llm_detection",
    "probe_resistance",
    "rtt_evaluation",
    "unknown",
})

VALID_TRANSPORTS = frozenset({"tcp", "tls", "ssh", "websocket", "mock", "socks5"})

VALID_RISK_LEVELS = frozenset({"low", "medium", "high"})

REQUIRED_FIELDS = frozenset({
    "task_type",
    "target_transport",
    "summary",
    "candidate_files",
    "validation_commands",
    "risk_level",
})


class LLMTaskPlannerError(Exception):
    """Raised when LLM output fails validation or safety checks."""


class LLMTaskPlan:
    """Validated task plan produced by an LLM.

    Compatible with the rule-based TaskPlan interface (task_type,
    target_transport, affected_areas) while adding LLM-specific fields.

    candidate_files is a planner hint only — the final selected_files must
    come from RepoIndexer + FileRetriever + ImpactExpander.
    """

    def __init__(
        self,
        task_type: str,
        target_transport: str | None,
        summary: str,
        candidate_files: list[str],
        validation_commands: list[str],
        risk_level: str,
        requirements: list[str] | None = None,
        constraints: list[str] | None = None,
        validation_goals: list[str] | None = None,
        ambiguity: list[str] | None = None,
        implementation_level: str = "skeleton",
        runtime_required: bool = False,
        allow_skeleton: bool = True,
        requires_default_switch: bool = False,
        default_transport_target: str | None = None,
        intent_contract: IntentContract | None = None,
    ):
        self.task_type = task_type
        self.target_transport = target_transport
        self.summary = summary
        self.candidate_files = candidate_files
        self.validation_commands = validation_commands
        self.risk_level = risk_level
        self.requirements = requirements or []
        self.constraints = constraints or []
        self.validation_goals = validation_goals or []
        self.ambiguity = ambiguity or []
        self.implementation_level = implementation_level
        self.runtime_required = runtime_required
        self.allow_skeleton = allow_skeleton
        self.requires_default_switch = requires_default_switch
        self.default_transport_target = default_transport_target
        self.intent_contract = intent_contract

    @property
    def affected_areas(self) -> list[str]:
        """Alias for compatibility with rule-based TaskPlan consumers."""
        return self.candidate_files

    @property
    def description(self) -> str:
        """Alias for compatibility with report_writer and task_record."""
        return self.summary

    def to_dict(self) -> dict:
        return {
            "task_type": self.task_type,
            "target_transport": self.target_transport,
            "summary": self.summary,
            "candidate_files": self.candidate_files,
            "validation_commands": self.validation_commands,
            "risk_level": self.risk_level,
            "requirements": self.requirements,
            "constraints": self.constraints,
            "validation_goals": self.validation_goals,
            "ambiguity": self.ambiguity,
            "implementation_level": self.implementation_level,
            "runtime_required": self.runtime_required,
            "allow_skeleton": self.allow_skeleton,
            "requires_default_switch": self.requires_default_switch,
            "default_transport_target": self.default_transport_target,
            "intent_contract": self.intent_contract.to_dict() if self.intent_contract else None,
        }


class LLMTaskPlanner:
    """LLM-based task planner using an OpenAI-compatible API.

    Usage:
        planner = LLMTaskPlanner("config/llm_agent.yaml")
        plan = planner.plan("switch default transport to WebSocket")
        # plan is an LLMTaskPlan with validated fields
    """

    # System prompt instructing the model to return ONLY valid JSON
    SYSTEM_PROMPT = (
        "You are a task planner for a VPN software project. "
        "Analyze the user's natural language request and output a JSON plan.\n\n"
        "Rules:\n"
        "- Output ONLY valid JSON. No markdown, no explanation, no code fences.\n"
        "- task_type: one of transport_change, transport_addition, config_change, test_addition, docs_update,"
        " core_change, feature_addition, mixed_feature_change, bugfix, refactor,"
        " fingerprint_mitigation, traffic_shaping, llm_detection, probe_resistance, rtt_evaluation, unknown\n"
        "- target_transport: The transport name the user wants to work with. "
        "For transport_addition tasks, set target_transport to the NEW transport name "
        "(e.g. 'socks5', 'http2', 'quic'). For transport_change tasks, set it to the "
        "EXISTING transport being changed (tcp, tls, ssh, websocket, mock). "
        "Set to null only when no specific transport is mentioned.\n"
        "- summary: one-sentence summary of what the user wants\n"
        "- candidate_files: list of file paths that MIGHT need changes (hints only — local index determines final selection)\n"
        "- validation_commands: list of shell commands to validate the result (empty list if unknown)\n"
        "- risk_level: low, medium, or high\n"
        "- requirements: (optional) list of explicit requirements derived from the request\n"
        "- constraints: (optional) list of constraints that must be respected\n"
        "- validation_goals: (optional) list of validation goals\n"
        "- ambiguity: (optional) list of ambiguous points needing clarification\n\n"
        "Example output:\n"
        '{"task_type":"transport_change","target_transport":"websocket",'
        '"summary":"Switch default transport from TCP to WebSocket",'
        '"candidate_files":["src/transport/websocket_transport.py","config/server.yaml"],'
        '"validation_commands":["python3 -m pytest tests/test_websocket_transport.py -v"],'
        '"risk_level":"medium",'
        '"requirements":["replace tcp with websocket in config"],'
        '"constraints":["must not break existing tests"],'
        '"validation_goals":["transport smoke matrix passes"],'
        '"ambiguity":[]}'
    )

    def __init__(self, config_path: str):
        self._config = self._load_config(config_path)
        self._api_key = os.environ.get(self._config["api_key_env"], "")
        if not self._api_key:
            raise LLMTaskPlannerError(
                f"API key not found. Set the {self._config['api_key_env']} environment variable."
            )
        self._base_url = self._config["base_url"].rstrip("/")
        self._model = self._config["model"]
        self._timeout = int(self._config.get("agent.request_timeout", 30))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(self, request: str) -> LLMTaskPlan:
        """Send the user request to the LLM and return a validated plan.

        Raises LLMTaskPlannerError on any failure: HTTP error, invalid JSON,
        missing fields, illegal values, or dangerous commands/files.
        """
        raw = self._call_api(request)
        parsed = self._parse_json(raw)
        validated = self._validate(parsed)
        validated = self._normalize_task_type(request, validated)

        impl = analyze_implementation_level(request, validated.get("task_type", ""),
                                             target_transport=validated.get("target_transport"))

        intent_contract = impl.get("intent_contract") or infer_intent_contract(
            request,
            task_type=validated.get("task_type", ""),
            target_transport=validated.get("target_transport"),
        )

        return LLMTaskPlan(
            task_type=validated["task_type"],
            target_transport=validated["target_transport"],
            summary=validated["summary"],
            candidate_files=validated["candidate_files"],
            validation_commands=validated["validation_commands"],
            risk_level=validated["risk_level"],
            requirements=validated.get("requirements", []),
            constraints=validated.get("constraints", []),
            validation_goals=validated.get("validation_goals", []),
            ambiguity=validated.get("ambiguity", []),
            implementation_level=impl["implementation_level"],
            runtime_required=impl["runtime_required"],
            allow_skeleton=impl["allow_skeleton"],
            requires_default_switch=impl["requires_default_switch"],
            default_transport_target=impl["default_transport_target"],
            intent_contract=intent_contract,
        )

    # ------------------------------------------------------------------
    # Internal: config loading
    # ------------------------------------------------------------------

    @staticmethod
    def _load_config(path: str) -> dict:
        if not os.path.isfile(path):
            raise LLMTaskPlannerError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict):
            raise LLMTaskPlannerError("Config file must be a YAML dictionary")

        required = ["base_url", "model", "api_key_env"]
        for key in required:
            if key not in raw:
                raise LLMTaskPlannerError(f"Missing required config key: {key}")

        return raw

    # ------------------------------------------------------------------
    # Internal: LLM API call
    # ------------------------------------------------------------------

    def _call_api(self, request: str) -> str:
        url = f"{self._base_url}/chat/completions"
        body = json.dumps({
            "model": self._model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": request},
            ],
            "temperature": 0.1,
            "max_tokens": 1024,
        })

        req = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        if os.environ.get("VPN_LLM_VERBOSE") == "1":
            _verbose_print("=== LLM Call: TaskPlanner ===")
            _verbose_print(f"URL: {url}")
            _verbose_print(f"Model: {self._model}")
            _verbose_print(f"System prompt ({len(self.SYSTEM_PROMPT)} chars): {self.SYSTEM_PROMPT[:200]}...")
            _verbose_print(f"User request: {request}")
            _verbose_print("--- sending request ---")

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise LLMTaskPlannerError(
                f"LLM API HTTP {e.code}: {e.reason}"
            ) from e
        except urllib.error.URLError as e:
            raise LLMTaskPlannerError(f"LLM API connection error: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise LLMTaskPlannerError(f"LLM API returned invalid JSON: {e}") from e

        data = json.loads(raw)

        if os.environ.get("VPN_LLM_VERBOSE") == "1":
            _verbose_print(f"--- response ({len(raw)} bytes) ---")
            _verbose_print(raw[:4000])
            _verbose_print("=== End TaskPlanner ===")

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMTaskPlannerError(
                f"Unexpected API response structure: {e}"
            ) from e

        return content

    # ------------------------------------------------------------------
    # Internal: JSON parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json(raw: str) -> dict:
        text = raw.strip()

        # Strip markdown code fences if present (defensive)
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMTaskPlannerError(
                f"LLM output is not valid JSON: {e}\nRaw output: {raw[:500]}"
            ) from e

        if not isinstance(parsed, dict):
            raise LLMTaskPlannerError(
                f"LLM output must be a JSON object, got {type(parsed).__name__}"
            )

        return parsed

    # ------------------------------------------------------------------
    # Internal: schema validation + safety checks
    # ------------------------------------------------------------------

    @staticmethod
    def _validate(parsed: dict) -> dict:
        # 1. Required fields
        missing = REQUIRED_FIELDS - set(parsed.keys())
        if missing:
            raise LLMTaskPlannerError(
                f"LLM plan missing required fields: {sorted(missing)}"
            )

        # 2. task_type whitelist
        task_type = parsed["task_type"]
        if not isinstance(task_type, str) or task_type not in VALID_TASK_TYPES:
            raise LLMTaskPlannerError(
                f"Invalid task_type '{task_type}'. Must be one of: {sorted(VALID_TASK_TYPES)}"
            )

        # 3. target_transport validation
        transport = parsed["target_transport"]
        _addition_types = {"transport_addition", "feature_addition", "mixed_feature_change"}
        if transport is not None:
            if not isinstance(transport, str):
                raise LLMTaskPlannerError(
                    f"target_transport must be a string or null, got {type(transport).__name__}"
                )
            # For transport_addition, allow new transport names not yet in VALID_TRANSPORTS
            if transport not in VALID_TRANSPORTS and task_type not in _addition_types:
                raise LLMTaskPlannerError(
                    f"Invalid target_transport '{transport}'. Must be one of: {sorted(VALID_TRANSPORTS)} or null"
                )

        # 4. summary must be a non-empty string
        summary = parsed.get("summary", "")
        if not isinstance(summary, str) or not summary.strip():
            raise LLMTaskPlannerError("summary must be a non-empty string")

        # 5. candidate_files must be a list of strings
        candidate_files = parsed.get("candidate_files", [])
        if not isinstance(candidate_files, list):
            raise LLMTaskPlannerError("candidate_files must be a list")
        for f in candidate_files:
            if not isinstance(f, str):
                raise LLMTaskPlannerError(f"candidate_files must contain strings, got {type(f).__name__}")
            # SafetyGuard check on every candidate file path
            try:
                SafetyGuard.validate_write_path(f)
            except SafetyError as e:
                raise LLMTaskPlannerError(
                    f"LLM proposed unsafe file path '{f}': {e}"
                ) from e

        # 6. validation_commands must be a list of strings, each checked by SafetyGuard
        validation_commands = parsed.get("validation_commands", [])
        if not isinstance(validation_commands, list):
            raise LLMTaskPlannerError("validation_commands must be a list")
        for cmd in validation_commands:
            if not isinstance(cmd, str):
                raise LLMTaskPlannerError(f"validation_commands must contain strings, got {type(cmd).__name__}")
            try:
                SafetyGuard.validate_command(cmd)
            except SafetyError as e:
                raise LLMTaskPlannerError(
                    f"LLM proposed unsafe command '{cmd[:80]}': {e}"
                ) from e

        # 7. risk_level whitelist
        risk_level = parsed["risk_level"]
        if not isinstance(risk_level, str) or risk_level not in VALID_RISK_LEVELS:
            raise LLMTaskPlannerError(
                f"Invalid risk_level '{risk_level}'. Must be one of: {sorted(VALID_RISK_LEVELS)}"
            )

        return {
            "task_type": task_type,
            "target_transport": transport,
            "summary": summary.strip(),
            "candidate_files": candidate_files,
            "validation_commands": validation_commands,
            "risk_level": risk_level,
            "requirements": parsed.get("requirements", []),
            "constraints": parsed.get("constraints", []),
            "validation_goals": parsed.get("validation_goals", []),
            "ambiguity": parsed.get("ambiguity", []),
        }

    @staticmethod
    def _normalize_task_type(request: str, validated: dict) -> dict:
        """Normalize task_type based on request content and affected areas.

        Rules:
        - refactor with core-related keywords (ClientCore, ServerCore, core,
          forwarding loop) → core_change
        - refactor with affected_areas containing src/core/client_core.py or
          src/core/server_core.py → core_change
        - All other types pass through unchanged.
        """
        task_type = validated.get("task_type", "")
        if task_type != "refactor":
            return validated

        request_lower = request.lower()
        candidate_files = validated.get("candidate_files", [])

        core_keywords = [
            "clientcore", "servercore", "forwarding loop",
            "forwarding", "core forwarding",
        ]
        has_core_keyword = any(kw in request_lower for kw in core_keywords)

        has_core_candidate = any(
            f in ("src/core/client_core.py", "src/core/server_core.py")
            for f in candidate_files
        )

        # Also check if a majority of affected_areas are under src/core/
        core_area_count = sum(
            1 for f in candidate_files
            if f.startswith("src/core/") or f.startswith("src/common/")
        )
        predominantly_core = (
            len(candidate_files) > 0
            and core_area_count >= len(candidate_files) * 0.5
        )

        if has_core_keyword or has_core_candidate or predominantly_core:
            validated = dict(validated)
            validated["task_type"] = "core_change"

        return validated
