"""Patch Blueprints — module-specific patch templates and guidance.

Phase LLM-M2: Adds a PatchBlueprint layer on top of TaskModuleContract.
While TaskModuleContract defines WHAT can be changed (file boundaries, policies),
PatchBlueprint defines HOW to structure those changes (templates, file specs,
validation requirements).

Design:
  TaskModuleContract → "what this task type allows/requires/forbids"
  PatchBlueprint     → "how to structure the patch for this module"
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FileChangeSpec:
    """Describes a single file change within a blueprint.

    No 'delete' action — deletion is governed by M1D No-Delete Policy.
    """

    path_pattern: str
    action: str  # "edit" | "create" | "append" | "replace"
    required: bool = True
    purpose: str = ""
    expected_content: list[str] = field(default_factory=list)
    forbidden_content: list[str] = field(default_factory=list)
    template_key: str | None = None

    def to_dict(self) -> dict:
        return {
            "path_pattern": self.path_pattern,
            "action": self.action,
            "required": self.required,
            "purpose": self.purpose,
            "expected_content": self.expected_content,
            "forbidden_content": self.forbidden_content,
            "template_key": self.template_key,
        }


@dataclass
class TemplateSpec:
    """A reusable content template for a specific file type."""

    template_key: str
    applies_to: str  # "transport_class" | "factory_registration" | "config" | "test" | "docs" | ...
    content: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "template_key": self.template_key,
            "applies_to": self.applies_to,
            "content": self.content[:500],
            "notes": self.notes,
        }


@dataclass
class PatchBlueprint:
    """Complete blueprint for patching a specific module type.

    Provides file change specifications, implementation/test/docs/config
    templates, and stage-specific prompt sections.
    """

    blueprint_name: str
    module_name: str
    description: str = ""

    # File-level specifications
    required_file_changes: list[FileChangeSpec] = field(default_factory=list)
    allowed_file_changes: list[FileChangeSpec] = field(default_factory=list)
    forbidden_file_changes: list[FileChangeSpec] = field(default_factory=list)

    # Templates
    implementation_templates: list[TemplateSpec] = field(default_factory=list)
    test_templates: list[TemplateSpec] = field(default_factory=list)
    docs_templates: list[TemplateSpec] = field(default_factory=list)
    config_templates: list[TemplateSpec] = field(default_factory=list)

    # Validation
    validation_requirements: list[str] = field(default_factory=list)

    # Stage-specific prompt sections keyed by stage_name
    stage_prompt_sections: dict[str, str] = field(default_factory=dict)

    # Additional metric/countermeasure mappings (for detection_countermeasure)
    metric_mappings: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "blueprint_name": self.blueprint_name,
            "module_name": self.module_name,
            "description": self.description,
            "required_file_changes": [f.to_dict() for f in self.required_file_changes],
            "allowed_file_changes": [f.to_dict() for f in self.allowed_file_changes],
            "forbidden_file_changes": [f.to_dict() for f in self.forbidden_file_changes],
            "implementation_templates": [t.to_dict() for t in self.implementation_templates],
            "test_templates": [t.to_dict() for t in self.test_templates],
            "docs_templates": [t.to_dict() for t in self.docs_templates],
            "config_templates": [t.to_dict() for t in self.config_templates],
            "validation_requirements": self.validation_requirements,
            "stage_prompt_sections": dict(self.stage_prompt_sections),
            "metric_mappings": {
                k: dict(v) for k, v in self.metric_mappings.items()
            },
        }

    def build_prompt_section(self, stage_name: str | None = None,
                             target_transport: str | None = None,
                             detected_metrics: list[str] | None = None) -> str:
        """Build the PATCH BLUEPRINT prompt section for injection into PatchGenerator.

        Args:
            stage_name: If provided, only include templates for this stage.
            target_transport: Transport name for template substitution.
            detected_metrics: Detection metrics to include countermeasure guidance for.

        Returns:
            Prompt section string.
        """
        t = target_transport
        lines = ["PATCH BLUEPRINT"]
        lines.append(f"Blueprint: {self.blueprint_name}")
        lines.append(f"Module: {self.module_name}")
        lines.append(f"Description: {self.description}")
        lines.append("")

        # --- File change plan ---
        if self.required_file_changes:
            lines.append("Required file changes:")
            for f in self.required_file_changes:
                path = f.path_pattern.format(name=t) if t and "{name}" in f.path_pattern else f.path_pattern
                lines.append(f"  - [{f.action}] {path}")
                if f.purpose:
                    lines.append(f"    Purpose: {f.purpose}")
                if f.expected_content:
                    lines.append(f"    Expected: {', '.join(f.expected_content[:5])}")
                if f.forbidden_content:
                    lines.append(f"    Forbidden: {', '.join(f.forbidden_content[:5])}")
            lines.append("")

        if self.forbidden_file_changes:
            lines.append("FORBIDDEN file changes:")
            for f in self.forbidden_file_changes:
                path = f.path_pattern.format(name=t) if t and "{name}" in f.path_pattern else f.path_pattern
                lines.append(f"  - DO NOT [{f.action}] {path}: {f.purpose}")
            lines.append("")

        # --- Stage-specific templates ---
        if stage_name:
            templates = self._get_templates_for_stage(stage_name)
            if templates:
                lines.append(f"Templates for stage '{stage_name}':")
                for tmpl in templates:
                    lines.append(f"  [{tmpl.template_key}] ({tmpl.applies_to})")
                    if tmpl.notes:
                        lines.append(f"    Notes: {tmpl.notes}")
                    if tmpl.content:
                        lines.append(f"    Template:")
                        for content_line in tmpl.content.strip().split("\n"):
                            lines.append(f"      {content_line}")
                    lines.append("")
        else:
            # Include all templates (non-stage mode)
            for category, tmpl_list in [
                ("Implementation", self.implementation_templates),
                ("Test", self.test_templates),
                ("Docs", self.docs_templates),
                ("Config", self.config_templates),
            ]:
                if tmpl_list:
                    lines.append(f"{category} templates:")
                    for tmpl in tmpl_list:
                        lines.append(f"  [{tmpl.template_key}] {tmpl.applies_to}")
                        if tmpl.notes:
                            lines.append(f"    {tmpl.notes}")
                    lines.append("")

        # --- Metric mappings (detection_countermeasure) ---
        if detected_metrics and self.metric_mappings:
            lines.append("Relevant countermeasure mappings:")
            for metric in detected_metrics:
                mapping = self.metric_mappings.get(metric)
                if mapping:
                    lines.append(f"  Metric: {metric}")
                    lines.append(f"    Strategy: {mapping.get('strategy', 'N/A')}")
                    lines.append(f"    Files: {', '.join(mapping.get('files', []))}")
                    if mapping.get('template_key'):
                        lines.append(f"    Template: {mapping['template_key']}")
            lines.append("")

        # --- Validation ---
        if self.validation_requirements:
            lines.append("Blueprint validation requirements:")
            for req in self.validation_requirements:
                lines.append(f"  - {req}")
            lines.append("")

        # --- Stage prompt section override ---
        if stage_name and stage_name in self.stage_prompt_sections:
            lines.append(self.stage_prompt_sections[stage_name])

        return "\n".join(lines)

    def _get_templates_for_stage(self, stage_name: str) -> list[TemplateSpec]:
        """Return templates relevant to a specific generation stage."""
        if stage_name == "runtime_core":
            return [t for t in self.implementation_templates
                    if t.applies_to in ("transport_class", "countermeasure_core")]
        elif stage_name == "integration_wiring":
            return [t for t in self.implementation_templates
                    if t.applies_to in ("factory_registration", "config_field")]
            + self.config_templates
        elif stage_name == "tests_docs_config":
            return self.test_templates + self.docs_templates
        elif stage_name == "final_validation":
            return []
        return []


# ---------------------------------------------------------------------------
# Blueprint registry
# ---------------------------------------------------------------------------

def _build_blueprint_registry() -> dict[str, PatchBlueprint]:
    """Build and return the blueprint registry."""

    registry: dict[str, PatchBlueprint] = {}

    # ---- OuterProtocolRuntimeBlueprint ----
    outer = _build_outer_protocol_runtime_blueprint()
    registry["transport_runtime"] = outer
    registry["default_transport_change"] = outer  # reuses with default gate

    # ---- DetectionCountermeasureBlueprint ----
    detection = _build_detection_countermeasure_blueprint()
    registry["detection_countermeasure"] = detection

    return registry


_BLUEPRINT_REGISTRY: dict[str, PatchBlueprint] | None = None


def get_blueprint_registry() -> dict[str, PatchBlueprint]:
    global _BLUEPRINT_REGISTRY
    if _BLUEPRINT_REGISTRY is None:
        _BLUEPRINT_REGISTRY = _build_blueprint_registry()
    return _BLUEPRINT_REGISTRY


def get_blueprint(module_name: str) -> PatchBlueprint | None:
    """Look up a PatchBlueprint by module name."""
    return get_blueprint_registry().get(module_name)


def list_blueprint_names() -> list[str]:
    """List all registered blueprint names."""
    return sorted(bp.blueprint_name for bp in get_blueprint_registry().values())


# ---------------------------------------------------------------------------
# OuterProtocolRuntimeBlueprint
# ---------------------------------------------------------------------------

def _build_outer_protocol_runtime_blueprint() -> PatchBlueprint:
    """Build the OuterProtocolRuntimeBlueprint.

    Covers transport_runtime and default_transport_change modules.
    Provides templates for the full 6-file transport addition pattern.
    """

    # --- File change specs ---

    transport_file = FileChangeSpec(
        path_pattern="src/transport/{name}_transport.py",
        action="edit",
        required=True,
        purpose="Transport runtime implementation file",
        expected_content=[
            "class {Name}Transport(Transport)",
            "def __init__",
            "def connect",
            "def send",
            "def recv",
            "def close",
            "def is_connected",
        ],
        forbidden_content=[
            "raise TransportError.*not.*implement",
            "raise NotImplementedError",
            "{name}_full_transport",
            "{name}_runtime_transport",
        ],
        template_key="transport_class",
    )

    factory_file = FileChangeSpec(
        path_pattern="src/transport/factory.py",
        action="edit",
        required=True,
        purpose="Register transport in factory",
        expected_content=[
            "from src.transport.{name}_transport import {Name}Transport",
            'create_transport(type="{name}"',
            "return {Name}Transport()",
        ],
        forbidden_content=[
            "pass  # placeholder",
            "raise NotImplementedError",
        ],
        template_key="factory_registration",
    )

    config_file = FileChangeSpec(
        path_pattern="src/common/config.py",
        action="edit",
        required=True,
        purpose="Add transport type to allowed config",
        expected_content=[
            '"{name}"',
        ],
        forbidden_content=[],
        template_key="config_field",
    )

    config_example = FileChangeSpec(
        path_pattern="config/examples/{name}_transport.yaml",
        action="create",
        required=True,
        purpose="Example configuration for the transport",
        expected_content=[
            "transport:",
            "type: {name}",
        ],
        forbidden_content=[],
        template_key="config_example",
    )

    test_file = FileChangeSpec(
        path_pattern="tests/test_{name}_transport.py",
        action="edit" if False else "create",
        required=True,
        purpose="Roundtrip and runtime tests",
        expected_content=[
            "class Test",
            "def test_",
            "import",
            "from src.transport",
        ],
        forbidden_content=[
            "raise TransportError.*not.*implement",
        ],
        template_key="transport_test",
    )

    docs_file = FileChangeSpec(
        path_pattern="docs/transports/{name}.md",
        action="edit" if False else "create",
        required=True,
        purpose="Transport documentation",
        expected_content=[
            "# ",
            "## ",
            "runtime",
            "config",
        ],
        forbidden_content=[
            "NOT runtime usable",
        ],
        template_key="transport_docs",
    )

    # --- Forbidden file changes ---
    forbidden_full_transport = FileChangeSpec(
        path_pattern="src/transport/{name}_full_transport.py",
        action="create",
        required=False,
        purpose="Bypass file — must NOT be created",
    )
    forbidden_runtime_transport = FileChangeSpec(
        path_pattern="src/transport/{name}_runtime_transport.py",
        action="create",
        required=False,
        purpose="Bypass file — must NOT be created",
    )
    forbidden_new_transport = FileChangeSpec(
        path_pattern="src/transport/{name}_new_transport.py",
        action="create",
        required=False,
        purpose="Bypass file — must NOT be created",
    )

    # --- Templates ---

    transport_class_tmpl = TemplateSpec(
        template_key="transport_class",
        applies_to="transport_class",
        notes="Implement all runtime methods. No connection in __init__. close() must be idempotent.",
        content="""class {Name}Transport(Transport):
    \"\"\"{Name} outer protocol transport. \"\"\"

    def __init__(self, config=None):
        super().__init__(config)
        self._connected = False
        # Initialize protocol-specific state here

    def connect(self, target=None):
        \"\"\"Establish transport connection. \"\"\"
        if self._connected:
            return
        # Implement connection logic
        self._connected = True

    def send(self, data: bytes) -> int:
        \"\"\"Send data through the transport. \"\"\"
        if not self._connected:
            raise TransportError("transport not connected")
        # Implement send logic
        return len(data)

    def recv(self, bufsize: int = 4096) -> bytes:
        \"\"\"Receive data from the transport. \"\"\"
        if not self._connected:
            raise TransportError("transport not connected")
        # Implement recv logic
        return b""

    def close(self):
        \"\"\"Close the transport connection. Idempotent. \"\"\"
        if not self._connected:
            return
        # Implement close logic
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected""",
    )

    factory_tmpl = TemplateSpec(
        template_key="factory_registration",
        applies_to="factory_registration",
        notes="Register the transport in create_transport(). Preserve existing transports.",
        content="""# In SUPPORTED_TRANSPORTS or equivalent:
# Add "{name}" to the list of supported transport types

# In create_transport():
from src.transport.{name}_transport import {Name}Transport
...
if transport_type == "{name}":
    return {Name}Transport(config)""",
    )

    config_tmpl = TemplateSpec(
        template_key="config_field",
        applies_to="config_field",
        notes="Add transport type to ALLOWED_TRANSPORT_TYPES. Add optional fields only if needed.",
        content="""# In ALLOWED_TRANSPORT_TYPES or equivalent:
# Add "{name}" to the allowed transport types list

# Optional transport-specific config fields (only if needed):
# {name}_option: "default_value\"""",
    )

    config_example_tmpl = TemplateSpec(
        template_key="config_example",
        applies_to="config_example",
        notes="Explicit example only. Do NOT modify config/client.yaml or config/server.yaml defaults.",
        content="""# Example configuration for {Name} transport
transport:
  type: {name}
  # Add transport-specific options here
  # {name}_option: "value\"""",
    )

    test_tmpl = TemplateSpec(
        template_key="transport_test",
        applies_to="transport_test",
        notes="Must include: import/construct, factory_create, config_load, client_server_connect or local runtime, send_recv_roundtrip, recv_timeout/close idempotent.",
        content="""import unittest
from src.transport.{name}_transport import {Name}Transport
from src.transport.factory import create_transport

class Test{Name}Transport(unittest.TestCase):

    def test_import_and_construct(self):
        t = {Name}Transport()
        self.assertIsNotNone(t)
        self.assertFalse(t.is_connected())

    def test_factory_create(self):
        t = create_transport(type="{name}")
        self.assertIsInstance(t, {Name}Transport)

    def test_connect_send_recv_close(self):
        t = {Name}Transport()
        # Set up: server/client or local loopback
        t.connect()
        self.assertTrue(t.is_connected())
        sent = t.send(b"hello")
        self.assertGreater(sent, 0)
        data = t.recv(1024)
        self.assertIsInstance(data, bytes)
        t.close()
        self.assertFalse(t.is_connected())

    def test_close_idempotent(self):
        t = {Name}Transport()
        t.connect()
        t.close()
        t.close()  # should not raise

    def test_recv_timeout(self):
        t = {Name}Transport()
        # Verify recv handles timeout gracefully

if __name__ == "__main__":
    unittest.main()""",
    )

    docs_tmpl = TemplateSpec(
        template_key="transport_docs",
        applies_to="transport_docs",
        notes="Document runtime status, configuration, limitations, and validation commands.",
        content="""# {Name} Transport

## Status
Runtime capable.

## Configuration
See config/examples/{name}_transport.yaml for an example configuration.

## Usage
```python
from src.transport.factory import create_transport
t = create_transport(type="{name}")
t.connect()
t.send(data)
t.recv(bufsize)
t.close()
```

## Limitations
- (document known limitations here)

## Validation
```bash
python3 -m pytest tests/test_{name}_transport.py -v
```""",
    )

    # --- Stage prompt sections ---
    stage_sections = {
        "runtime_core": (
            "OUTER PROTOCOL RUNTIME CORE STAGE:\n"
            "Focus ONLY on the transport class implementation.\n"
            "- Implement connect(), send(), recv(), close(), is_connected() with real logic.\n"
            "- Do NOT touch factory.py, config.py, tests/, docs/, or config/examples/.\n"
            "- Do NOT create bypass files.\n"
            "- The transport class must be fully functional on its own."
        ),
        "integration_wiring": (
            "OUTER PROTOCOL INTEGRATION WIRING STAGE:\n"
            "Wire the EXISTING runtime transport into factory/config.\n"
            "- Do NOT rewrite the transport class implementation.\n"
            "- Add factory registration for the transport.\n"
            "- Add config field for allowed transport type.\n"
            "- Create config example file.\n"
            "- Preserve all existing transport registrations."
        ),
        "tests_docs_config": (
            "OUTER PROTOCOL TESTS & DOCS STAGE:\n"
            "Add tests and documentation for the transport.\n"
            "- Do NOT modify source files (transport, factory, config).\n"
            "- Add roundtrip or client/server tests.\n"
            "- Document runtime status, configuration, and limitations.\n"
            "- Tests must exercise actual data transmission."
        ),
        "final_validation": (
            "OUTER PROTOCOL FINAL VALIDATION:\n"
            "- Run compileall, full pytest, and tunnel smoke.\n"
            "- Verify all required files are present.\n"
            "- Verify no forbidden files were created."
        ),
    }

    # --- Validation requirements ---
    validation_reqs = [
        "compileall: python3 -m compileall src tests",
        "targeted_transport_tests: pytest tests/test_{name}_transport.py",
        "full_pytest: pytest tests/ -q",
        "tunnel_smoke: tunnel smoke validation must pass",
        "no_forbidden_files: no *_full_transport.py, *_runtime_transport.py, etc.",
        "no_default_change: config/client.yaml and config/server.yaml defaults unchanged",
        "all_six_files_present: transport, factory, config, test, docs, config example",
    ]

    blueprint = PatchBlueprint(
        blueprint_name="OuterProtocolRuntimeBlueprint",
        module_name="transport_runtime",
        description="Blueprint for adding/upgrading outer protocol transports to full runtime capability",
        required_file_changes=[
            transport_file, factory_file, config_file,
            config_example, test_file, docs_file,
        ],
        allowed_file_changes=[
            transport_file, factory_file, config_file,
            config_example, test_file, docs_file,
        ],
        forbidden_file_changes=[
            forbidden_full_transport,
            forbidden_runtime_transport,
            forbidden_new_transport,
        ],
        implementation_templates=[
            transport_class_tmpl, factory_tmpl, config_tmpl,
        ],
        test_templates=[test_tmpl],
        docs_templates=[docs_tmpl],
        config_templates=[config_example_tmpl],
        validation_requirements=validation_reqs,
        stage_prompt_sections=stage_sections,
    )

    return blueprint


# ---------------------------------------------------------------------------
# DetectionCountermeasureBlueprint
# ---------------------------------------------------------------------------

def _build_detection_countermeasure_blueprint() -> PatchBlueprint:
    """Build the DetectionCountermeasureBlueprint.

    Maps detection metrics to countermeasure strategies with file-level
    guidance, config-driven change requirements, and before/after evidence.
    """

    # --- Metric-to-countermeasure mappings ---
    metric_mappings = {
        "small_packet_ratio": {
            "strategy": "frame aggregation + delayed flush + heartbeat/control bypass",
            "files": [
                "src/shaping/aggregation.py",
                "src/shaping/scheduler.py",
                "src/common/config.py",
                "tests/test_traffic_shaper_aggregation.py",
            ],
            "template_key": "aggregation_countermeasure",
            "config_flag": "shaping.aggregation.enabled",
            "notes": (
                "Frame aggregation merges small packets before transmission. "
                "Delayed flush holds packets for a configurable window. "
                "Heartbeat/control packets should bypass aggregation to avoid "
                "breaking keepalive timing."
            ),
        },
        "repeated_length_ratio": {
            "strategy": "random padding + length bucket randomization",
            "files": [
                "src/shaping/padding.py",
                "src/common/config.py",
                "src/transport/factory.py",
                "tests/test_traffic_shaper_padding.py",
            ],
            "template_key": "padding_countermeasure",
            "config_flag": "shaping.padding.enabled",
            "notes": (
                "Random padding adds variable-length padding to packets. "
                "Length bucket randomization assigns packets to random size "
                "buckets within configured ranges. Must preserve encode/decode "
                "roundtrip correctness."
            ),
        },
        "dominant_ngram_ratio": {
            "strategy": "chunking + size randomization + multi-stream distribution",
            "files": [
                "src/shaping/chunking.py",
                "src/transport/http2_transport.py",
                "src/common/config.py",
                "tests/test_traffic_shaper_chunking.py",
            ],
            "template_key": "chunking_countermeasure",
            "config_flag": "shaping.chunking.enabled",
            "notes": (
                "Chunking splits payloads into variable-sized chunks. "
                "For HTTP/2, distribute chunks across multiple streams. "
                "Size randomization prevents n-gram fingerprinting."
            ),
        },
        "burst_pattern_score": {
            "strategy": "pacing + jitter scheduler + controlled flush variation",
            "files": [
                "src/shaping/timing.py",
                "src/shaping/scheduler.py",
                "src/common/config.py",
                "tests/test_traffic_shaper_timing.py",
            ],
            "template_key": "burst_countermeasure",
            "config_flag": "shaping.burst_control.enabled",
            "notes": (
                "Pacing spreads packets over time to avoid burst patterns. "
                "Jitter adds random variation to transmission timing. "
                "Controlled flush variation prevents predictable flush patterns."
            ),
        },
        "app_transport_diff_ms": {
            "strategy": "RTT-aware pacing + timing policy adjustment",
            "files": [
                "src/shaping/timing.py",
                "src/shaping/scheduler.py",
                "src/common/config.py",
                "scripts/comparison_timing.py",
                "tests/test_traffic_shaper_timing.py",
            ],
            "template_key": "timing_countermeasure",
            "config_flag": "shaping.timing.enabled",
            "notes": (
                "RTT-aware pacing adjusts transmission timing based on "
                "observed RTT. Timing policy controls delay distribution. "
                "Synthetic comparison scripts verify before/after metrics."
            ),
        },
        "probe_response_variance": {
            "strategy": "uniform silent drop + constant close policy",
            "files": [
                "src/core/server_core.py",
                "tests/test_core_probe_response.py",
                "src/common/config.py",
            ],
            "template_key": "probe_countermeasure",
            "config_flag": "core.probe_defense.enabled",
            "notes": (
                "Uniform silent drop: all probes receive the same silence — "
                "no distinguishable error responses. Constant close policy "
                "prevents response fingerprinting. Must not break legitimate "
                "connection handling."
            ),
        },
        "http2_frame_pattern": {
            "strategy": "chunking + multi-stream + WINDOW_UPDATE batching + SETTINGS profile",
            "files": [
                "src/transport/http2_transport.py",
                "src/shaping/chunking.py",
                "src/common/config.py",
                "tests/test_http2_transport.py",
            ],
            "template_key": "http2_countermeasure",
            "config_flag": "shaping.http2_obfuscation.enabled",
            "notes": (
                "Multi-stream distribution spreads payload across HTTP/2 streams. "
                "WINDOW_UPDATE batching prevents frame-level fingerprinting. "
                "SETTINGS profile randomization prevents parameter fingerprinting."
            ),
        },
    }

    # --- File change specs ---
    shaping_files = [
        FileChangeSpec(
            path_pattern="src/shaping/aggregation.py",
            action="edit" if False else "create",
            required=False,
            purpose="Frame aggregation countermeasure for small_packet_ratio",
            expected_content=["class", "def aggregate", "config", "enabled"],
            forbidden_content=["lower.*threshold", "disable.*detector"],
            template_key="aggregation_countermeasure",
        ),
        FileChangeSpec(
            path_pattern="src/shaping/padding.py",
            action="edit" if False else "create",
            required=False,
            purpose="Random padding countermeasure for repeated_length_ratio",
            expected_content=["class", "def pad", "def unpad", "config", "enabled"],
            forbidden_content=["lower.*threshold", "disable.*detector"],
            template_key="padding_countermeasure",
        ),
        FileChangeSpec(
            path_pattern="src/shaping/chunking.py",
            action="edit" if False else "create",
            required=False,
            purpose="Chunking countermeasure for dominant_ngram_ratio",
            expected_content=["class", "def chunk", "def unchunk", "config", "enabled"],
            forbidden_content=["lower.*threshold", "disable.*detector"],
            template_key="chunking_countermeasure",
        ),
        FileChangeSpec(
            path_pattern="src/shaping/timing.py",
            action="edit" if False else "create",
            required=False,
            purpose="Timing/pacing countermeasure for burst/app_transport_diff",
            expected_content=["class", "def pace", "config", "enabled"],
            forbidden_content=["lower.*threshold", "disable.*detector"],
            template_key="timing_countermeasure",
        ),
    ]

    config_file = FileChangeSpec(
        path_pattern="src/common/config.py",
        action="edit",
        required=True,
        purpose="Add feature flag config fields (default OFF)",
        expected_content=["enabled", "False", "shaping"],
        forbidden_content=["threshold", "detector_enabled.*False"],
        template_key="config_feature_flag",
    )

    test_files = [
        FileChangeSpec(
            path_pattern="tests/test_traffic_shaper_*.py",
            action="create",
            required=True,
            purpose="Tests for enabled and default-off behavior",
            expected_content=["class Test", "def test_enabled", "def test_default_off"],
            forbidden_content=[],
            template_key="countermeasure_test",
        ),
    ]

    comparison_script = FileChangeSpec(
        path_pattern="scripts/comparison_*.py",
        action="create",
        required=False,
        purpose="Before/after metric comparison script",
        expected_content=["before", "after", "metric", "compare"],
        forbidden_content=[],
        template_key="comparison_script",
    )

    # --- Forbidden changes ---
    forbidden_detector_disable = FileChangeSpec(
        path_pattern="src/llm/detection/*",
        action="edit",
        required=False,
        purpose="Must NOT disable or remove detectors",
    )
    forbidden_threshold = FileChangeSpec(
        path_pattern="src/llm/detection/*",
        action="edit",
        required=False,
        purpose="Must NOT lower detection thresholds",
    )
    forbidden_config_defaults = FileChangeSpec(
        path_pattern="config/client.yaml",
        action="edit",
        required=False,
        purpose="Must NOT change default config without explicit request",
    )
    forbidden_server_defaults = FileChangeSpec(
        path_pattern="config/server.yaml",
        action="edit",
        required=False,
        purpose="Must NOT change default config without explicit request",
    )

    # --- Templates ---
    countermeasure_core_tmpl = TemplateSpec(
        template_key="countermeasure_core",
        applies_to="countermeasure_core",
        notes="Countermeasure implementation template. Feature flag gated, default OFF.",
        content="""class {Name}Countermeasure:
    \"\"\"{Description} countermeasure.

    Config-driven: enabled via config flag, defaults to OFF.
    \"\"\"

    def __init__(self, config=None):
        self._config = config or {}
        self._enabled = self._config.get("{config_flag}", False)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def apply(self, data, **kwargs):
        \"\"\"Apply countermeasure transformation.

        Returns original data unchanged when disabled.
        \"\"\"
        if not self._enabled:
            return data
        return self._transform(data, **kwargs)

    def _transform(self, data, **kwargs):
        \"\"\"Actual countermeasure logic. Override in subclasses. \"\"\"
        raise NotImplementedError

    def reverse(self, data, **kwargs):
        \"\"\"Reverse countermeasure transformation.

        Returns original data unchanged when disabled.
        \"\"\"
        if not self._enabled:
            return data
        return self._untransform(data, **kwargs)

    def _untransform(self, data, **kwargs):
        \"\"\"Reverse transformation logic. Override in subclasses. \"\"\"
        raise NotImplementedError""",
    )

    feature_flag_tmpl = TemplateSpec(
        template_key="config_feature_flag",
        applies_to="config_field",
        notes="Feature flag pattern: default OFF, user enables via config.",
        content="""# {countermeasure_name} feature flag (default OFF)
# Enable via: {module}.{flag_name}: true
{config_path}:
  enabled: false
  # Add countermeasure-specific options below""",
    )

    countermeasure_test_tmpl = TemplateSpec(
        template_key="countermeasure_test",
        applies_to="countermeasure_test",
        notes="Tests must cover both default-off behavior and enabled behavior.",
        content="""import unittest
from src.common.config import load_config

class Test{CountermeasureName}(unittest.TestCase):

    def test_default_off(self):
        \"\"\"Countermeasure MUST be disabled by default. \"\"\"
        # Verify that without explicit config, countermeasure is inactive
        # Default behavior must be preserved
        pass

    def test_enabled_behavior(self):
        \"\"\"When enabled, countermeasure must transform data. \"\"\"
        # Enable via config flag
        # Verify transformation is applied
        pass

    def test_encode_decode_roundtrip(self):
        \"\"\"Enabled countermeasure must preserve data correctness. \"\"\"
        # Apply countermeasure
        # Reverse countermeasure
        # Verify original data is recovered
        pass

    def test_disabled_preserves_default(self):
        \"\"\"Default config must not change existing behavior. \"\"\"
        pass

if __name__ == "__main__":
    unittest.main()""",
    )

    # --- Stage prompt sections ---
    stage_sections = {
        "runtime_core": (
            "DETECTION COUNTERMEASURE CORE STAGE:\n"
            "Implement the countermeasure logic ONLY.\n"
            "- Feature flag gated: default OFF.\n"
            "- Implement apply() and reverse() methods.\n"
            "- Do NOT modify detectors or lower thresholds.\n"
            "- Do NOT touch config files or tests in this stage.\n"
            "- If touching core/transport/shaping runtime path, preserve existing behavior when disabled."
        ),
        "integration_wiring": (
            "DETECTION COUNTERMEASURE INTEGRATION STAGE:\n"
            "Wire countermeasure into config and factory.\n"
            "- Add feature flag to config (default: false).\n"
            "- Wire countermeasure into shaping pipeline.\n"
            "- Do NOT rewrite countermeasure core logic.\n"
            "- Preserve default behavior when flag is off."
        ),
        "tests_docs_config": (
            "DETECTION COUNTERMEASURE TESTS & DOCS STAGE:\n"
            "Add tests for both disabled and enabled states.\n"
            "- Test default-off behavior preserves existing metrics.\n"
            "- Test enabled behavior shows metric improvement.\n"
            "- Provide before/after evidence or synthetic fixture.\n"
            "- Document configuration and usage."
        ),
        "final_validation": (
            "DETECTION COUNTERMEASURE FINAL VALIDATION:\n"
            "- Verify feature flag defaults to OFF.\n"
            "- Verify before/after evidence is present.\n"
            "- Run tunnel smoke if core/transport path was touched.\n"
            "- Verify no detector thresholds were lowered.\n"
            "- Verify no detectors were disabled or removed."
        ),
    }

    # --- Validation requirements ---
    validation_reqs = [
        "config_flag_default_off: feature flag must default to false",
        "enabled_tests: tests for enabled behavior must be present",
        "disabled_default_tests: tests for default-off behavior must be present",
        "before_after_metric_evidence: metric comparison evidence required",
        "encode_decode_roundtrip: roundtrip correctness must be verified",
        "no_detector_threshold_lowering: thresholds must not be lowered",
        "no_detector_disabling: detectors must not be disabled or removed",
        "tunnel_smoke_if_runtime: tunnel smoke required if core/transport path touched",
    ]

    blueprint = PatchBlueprint(
        blueprint_name="DetectionCountermeasureBlueprint",
        module_name="detection_countermeasure",
        description="Blueprint for implementing detection countermeasures behind config flags",
        required_file_changes=[
            config_file,
        ],
        allowed_file_changes=shaping_files + [config_file] + test_files + [comparison_script],
        forbidden_file_changes=[
            forbidden_detector_disable,
            forbidden_threshold,
            forbidden_config_defaults,
            forbidden_server_defaults,
        ],
        implementation_templates=[
            countermeasure_core_tmpl,
        ],
        test_templates=[countermeasure_test_tmpl],
        docs_templates=[],
        config_templates=[feature_flag_tmpl],
        validation_requirements=validation_reqs,
        stage_prompt_sections=stage_sections,
        metric_mappings=metric_mappings,
    )

    return blueprint
