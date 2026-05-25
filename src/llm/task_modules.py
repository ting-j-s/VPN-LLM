"""Task Module Contracts — module-driven prompt and validation constraints.

Phase LLM-M1: Defines TaskModuleContract as the "what can be changed" counterpart
to IntentContract's "what the user wants". The TaskModuleResolver maps an
IntentContract to a specific module and expands it into concrete file lists.

Design:
  IntentContract  →  "what the user wants"
  TaskModuleContract → "what this task type allows/requires/forbids"
  TaskModuleResolution → concrete file lists + evidence for the pipeline
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# TaskModuleContract
# ---------------------------------------------------------------------------

@dataclass
class TaskModuleContract:
    """Defines the boundary box for a class of tasks.

    Each module specifies which files may be edited/created, which files must
    be edited/created, which files/paths are forbidden, what evidence is
    required, and what validation gates must pass.

    required_evidence entries reference AcceptanceCriterion names from
    IntentContract (e.g. "runtime_connect", "roundtrip_test", "tunnel_smoke").
    """

    module_name: str
    description: str = ""
    task_types: list[str] = field(default_factory=list)
    implementation_levels: list[str] = field(default_factory=list)

    # File boundary rules
    allowed_edit_patterns: list[str] = field(default_factory=list)
    allowed_create_patterns: list[str] = field(default_factory=list)
    required_edit_patterns: list[str] = field(default_factory=list)
    required_create_patterns: list[str] = field(default_factory=list)
    forbidden_patterns: list[str] = field(default_factory=list)

    # Context limits
    required_context_patterns: list[str] = field(default_factory=list)
    max_context_files: int = 50
    max_must_review_files: int = 30

    # Requirement gates
    require_tests: bool = False
    require_docs: bool = False
    require_config: bool = False
    require_cli: bool = False
    require_tunnel_smoke: bool = False
    require_trace_or_evaluation: bool = False

    # Policy flags
    allow_stub: bool = False
    allow_default_change: bool = False
    allow_completed_with_warnings: bool = False

    # Evidence (references AcceptanceCriterion names)
    required_evidence: list[str] = field(default_factory=list)
    forbidden_evidence_failures: list[str] = field(default_factory=list)

    # Degradations that must not happen
    forbidden_degradations: list[str] = field(default_factory=list)

    # Suggested validation commands
    validation_commands: list[str] = field(default_factory=list)

    # If True, files outside allowed/required patterns need a justification
    extra_files_require_reason: bool = True

    # Staged generation (Phase LLM-M1.1)
    staged_generation_required: bool = False
    stages: list = field(default_factory=list)  # list[TaskModuleStage]

    def to_dict(self) -> dict:
        return {
            "module_name": self.module_name,
            "description": self.description,
            "task_types": self.task_types,
            "implementation_levels": self.implementation_levels,
            "allowed_edit_patterns": self.allowed_edit_patterns,
            "allowed_create_patterns": self.allowed_create_patterns,
            "required_edit_patterns": self.required_edit_patterns,
            "required_create_patterns": self.required_create_patterns,
            "forbidden_patterns": self.forbidden_patterns,
            "required_context_patterns": self.required_context_patterns,
            "max_context_files": self.max_context_files,
            "max_must_review_files": self.max_must_review_files,
            "require_tests": self.require_tests,
            "require_docs": self.require_docs,
            "require_config": self.require_config,
            "require_cli": self.require_cli,
            "require_tunnel_smoke": self.require_tunnel_smoke,
            "require_trace_or_evaluation": self.require_trace_or_evaluation,
            "allow_stub": self.allow_stub,
            "allow_default_change": self.allow_default_change,
            "allow_completed_with_warnings": self.allow_completed_with_warnings,
            "required_evidence": self.required_evidence,
            "forbidden_evidence_failures": self.forbidden_evidence_failures,
            "forbidden_degradations": self.forbidden_degradations,
            "validation_commands": self.validation_commands,
            "extra_files_require_reason": self.extra_files_require_reason,
            "staged_generation_required": self.staged_generation_required,
            "stages": [s.to_dict() for s in self.stages],
        }

    def build_prompt_section(self, target_transport: str | None = None) -> str:
        """Build the TASK MODULE CONTRACT section for the PatchGenerator prompt."""
        t = target_transport
        lines = ["TASK MODULE CONTRACT:"]
        lines.append(f"  Module: {self.module_name}")
        if t:
            lines.append(f"  Target Transport: {t}")
        lines.append(f"  Description: {self.description}")

        if self.required_edit_patterns:
            lines.append("  Required Edit Files (MUST be modified if they exist):")
            for p in self.required_edit_patterns:
                lines.append(f"    - {p}")
        if self.required_create_patterns:
            lines.append("  Required Create Files (MUST be created):")
            for p in self.required_create_patterns:
                lines.append(f"    - {p}")
        if self.allowed_edit_patterns:
            lines.append("  Allowed Edit Patterns:")
            for p in self.allowed_edit_patterns:
                lines.append(f"    - {p}")
        if self.forbidden_patterns:
            lines.append("  FORBIDDEN (do NOT create or modify):")
            for p in self.forbidden_patterns:
                lines.append(f"    - {p}")

        if self.required_evidence:
            lines.append("  Required Evidence:")
            for e in self.required_evidence:
                lines.append(f"    - {e}")

        lines.append(f"  Tunnel Smoke Required: {self.require_tunnel_smoke}")
        lines.append(f"  Allow Stub: {self.allow_stub}")
        lines.append(f"  Allow Completed With Warnings: {self.allow_completed_with_warnings}")
        lines.append(f"  Extra Files Require Reason: {self.extra_files_require_reason}")

        return "\n".join(lines)

    def build_prompt_constraints(self, target_transport: str | None = None) -> str:
        """Build additional constraint text injected into the PatchGenerator prompt."""
        t = target_transport
        parts: list[str] = []

        if self.module_name == "transport_runtime":
            parts.append("TRANSPORT RUNTIME CONSTRAINTS:")
            parts.append("- Upgrade the existing transport file if it already exists as a skeleton.")
            parts.append("- Do NOT create *_full_transport.py or *_runtime_transport.py bypass files.")
            parts.append("- Do NOT generate a skeleton-only implementation.")
            if t:
                parts.append(f'- create_transport(type="{t}") MUST return the runtime-capable implementation.')
                parts.append(f"- tests/test_{t}_transport.py MUST include client/server or send/recv roundtrip.")
            parts.append("- Tunnel smoke evidence is REQUIRED before this task can be completed.")
            parts.append("- 'completed_with_warnings' is NOT acceptable for this module.")
            parts.append("- All required files (transport, factory, config, tests, docs, config example)")
            parts.append("  MUST be present in the patch.")

        elif self.module_name == "transport_skeleton":
            parts.append("TRANSPORT SKELETON CONSTRAINTS:")
            parts.append("- Generate a SKELETON implementation only.")
            parts.append("- connect() should raise TransportError with a clear 'not yet implemented' message.")
            parts.append("- Do NOT change the default transport config.")
            parts.append("- Docs MUST state the transport is NOT runtime usable.")
            if t:
                parts.append(f"- Use the canonical path src/transport/{t}_transport.py.")

        elif self.module_name == "docs_only":
            parts.append("DOCS-ONLY CONSTRAINTS:")
            parts.append("- ONLY edit documentation files (*.md, docs/**).")
            parts.append("- Do NOT change src/, config/, scripts/, or tests/.")
            parts.append("- Runtime behavior changes are FORBIDDEN.")

        elif self.module_name == "default_transport_change":
            parts.append("DEFAULT TRANSPORT CHANGE CONSTRAINTS:")
            parts.append("- Target transport MUST be runtime-usable before switching defaults.")
            parts.append("- DO NOT modify default config unless runtime smoke tests exist and pass.")
            if t:
                parts.append(f"- Run tunnel smoke with transport={t} before marking task complete.")

        elif self.module_name == "llm_workflow":
            parts.append("LLM WORKFLOW CONSTRAINTS:")
            parts.append("- Modify files under src/llm/ and scripts/llm_task.py.")
            parts.append("- Do NOT change transport implementations or config defaults.")
            parts.append("- Existing tests must continue to pass.")

        return "\n".join(parts)


    def build_stage_prompt_section(self, stage_name: str,
                                    target_transport: str | None = None) -> str:
        """Build prompt section for a specific stage of this module."""
        stage = None
        for s in self.stages:
            if s.stage_name == stage_name:
                stage = s
                break
        if stage is None:
            return ""

        t = target_transport
        lines = [f"STAGE: {stage.stage_name} — {stage.description}"]
        lines.append(f"  Module: {self.module_name}")
        if t:
            lines.append(f"  Target Transport: {t}")
        lines.append(f"  Max output files: {stage.max_output_files}")

        expanded_allowed_edit = [
            p.format(name=t) if t and "{name}" in p else p
            for p in stage.allowed_edit_patterns
        ]
        expanded_required_edit = [
            p.format(name=t) if t and "{name}" in p else p
            for p in stage.required_edit_patterns
        ]
        expanded_required_create = [
            p.format(name=t) if t and "{name}" in p else p
            for p in stage.required_create_patterns
        ]
        expanded_forbidden = [
            p.format(name=t) if t and "{name}" in p else p
            for p in stage.forbidden_patterns
        ]

        if expanded_allowed_edit:
            lines.append("  Allowed Edit Files (ONLY these in this stage):")
            for p in expanded_allowed_edit:
                lines.append(f"    - {p}")
        if expanded_required_edit:
            lines.append("  Required Edit Files (MUST modify if they exist):")
            for p in expanded_required_edit:
                lines.append(f"    - {p}")
        if expanded_required_create:
            lines.append("  Required Create Files (MUST create):")
            for p in expanded_required_create:
                lines.append(f"    - {p}")
        if expanded_forbidden:
            lines.append("  FORBIDDEN (do NOT touch in this stage):")
            for p in expanded_forbidden:
                lines.append(f"    - {p}")
        if stage.required_evidence:
            lines.append("  Required Evidence for this stage:")
            for e in stage.required_evidence:
                lines.append(f"    - {e}")

        return "\n".join(lines)

    def build_stage_prompt_constraints(self, stage_name: str,
                                        target_transport: str | None = None) -> str:
        """Build constraint text for a specific stage."""
        t = target_transport
        stage = None
        for s in self.stages:
            if s.stage_name == stage_name:
                stage = s
                break
        if stage is None:
            return ""

        parts: list[str] = []
        parts.append(f"STAGE CONSTRAINT: {stage.stage_name}")

        if stage_name == "runtime_core":
            parts.append(f"ONLY modify src/transport/{t}_transport.py in this stage.")
            parts.append("Do NOT touch factory.py, config.py, tests/, docs/, or config/examples/.")
            parts.append(f"Do NOT create src/transport/{t}_full_transport.py or any bypass file.")
            parts.append("Implement runtime methods: connect(), send(), recv(), close(), is_connected().")
            parts.append("Keep output small and complete. Focus on the transport protocol only.")
            parts.append("Do NOT generate any other files. This stage is for the transport file ONLY.")
        elif stage_name == "integration_wiring":
            parts.append("ONLY modify factory.py, config.py, and create config example.")
            parts.append(f"Do NOT rewrite the transport implementation in src/transport/{t}_transport.py.")
            parts.append(f"Wire the EXISTING runtime transport class into create_transport(type=\"{t}\").")
            parts.append("Add config fields for this transport's options.")
        elif stage_name == "tests_docs_config":
            parts.append("ONLY modify tests/ and docs/ for this transport.")
            parts.append(f"Do NOT modify src/transport/{t}_transport.py, factory.py, or config.py.")
            parts.append("Add roundtrip or client/server tests that exercise actual data transmission.")
            parts.append("Update docs to reflect runtime status, usage, and limitations.")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# TaskModuleStage
# ---------------------------------------------------------------------------

@dataclass
class StageTargetFile:
    """Describes a target file for a stage, with content and strategy hints.

    Used to give the LLM enough context to perform precise FIND/REPLACE
    or append operations without guessing at file content.
    """

    path: str
    exists: bool = False
    content_excerpt: str = ""
    full_content: str | None = None
    stable_anchors: list[str] = field(default_factory=list)
    recommended_strategy: str = "exact_replace"  # append, whole_file_replace, exact_replace
    reason: str = ""


@dataclass
class StageContext:
    """Context bundle for a single stage of a staged-generation module.

    Includes the target files the stage should touch, their current content,
    and recommended edit strategies so the LLM has precise anchors.
    """

    stage_name: str
    target_files: list[StageTargetFile] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "stage_name": self.stage_name,
            "target_files": [
                {
                    "path": tf.path,
                    "exists": tf.exists,
                    "content_excerpt": tf.content_excerpt[:500],
                    "full_content": tf.full_content,
                    "stable_anchors": tf.stable_anchors,
                    "recommended_strategy": tf.recommended_strategy,
                    "reason": tf.reason,
                }
                for tf in self.target_files
            ],
        }


@dataclass
class TaskModuleStage:
    """A single stage within a staged-generation module.

    Each stage has independent file boundaries, required evidence,
    and validation gates. Stages are executed sequentially —
    the next stage only starts after the current stage passes.
    """

    stage_name: str
    description: str = ""
    allowed_edit_patterns: list[str] = field(default_factory=list)
    allowed_create_patterns: list[str] = field(default_factory=list)
    required_edit_patterns: list[str] = field(default_factory=list)
    required_create_patterns: list[str] = field(default_factory=list)
    forbidden_patterns: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    validation_commands: list[str] = field(default_factory=list)
    max_output_files: int = 4
    allow_partial_stage: bool = False
    next_stage: str | None = None

    def to_dict(self) -> dict:
        return {
            "stage_name": self.stage_name,
            "description": self.description,
            "allowed_edit_patterns": self.allowed_edit_patterns,
            "allowed_create_patterns": self.allowed_create_patterns,
            "required_edit_patterns": self.required_edit_patterns,
            "required_create_patterns": self.required_create_patterns,
            "forbidden_patterns": self.forbidden_patterns,
            "required_evidence": self.required_evidence,
            "validation_commands": self.validation_commands,
            "max_output_files": self.max_output_files,
            "allow_partial_stage": self.allow_partial_stage,
            "next_stage": self.next_stage,
        }


# ---------------------------------------------------------------------------
# TaskModuleResolution
# ---------------------------------------------------------------------------

@dataclass
class TaskModuleResolution:
    """Concrete resolution of a module contract for a specific task."""

    selected_module: str
    confidence: float
    reason: str
    target_transport: str | None = None
    allowed_files: list[str] = field(default_factory=list)
    required_files: list[str] = field(default_factory=list)
    forbidden_files: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    staged_generation_required: bool = False
    stages: list = field(default_factory=list)  # list[TaskModuleStage]

    def to_dict(self) -> dict:
        return {
            "selected_module": self.selected_module,
            "confidence": self.confidence,
            "reason": self.reason,
            "target_transport": self.target_transport,
            "allowed_files": self.allowed_files,
            "required_files": self.required_files,
            "forbidden_files": self.forbidden_files,
            "required_evidence": self.required_evidence,
            "warnings": self.warnings,
            "staged_generation_required": self.staged_generation_required,
            "stages": [s.to_dict() for s in self.stages],
        }


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------

def _build_stages_for_transport_runtime() -> list[TaskModuleStage]:
    """Build the 4-stage pipeline for transport_runtime tasks.

    Each stage has narrow file boundaries so the LLM produces small,
    focused patches instead of one unreliable 8-file mega-patch.
    """
    stage_1 = TaskModuleStage(
        stage_name="runtime_core",
        description="Implement or upgrade the transport runtime core file ONLY",
        allowed_edit_patterns=[
            "src/transport/{name}_transport.py",
        ],
        required_edit_patterns=[
            "src/transport/{name}_transport.py",
        ],
        forbidden_patterns=[
            "src/transport/{name}_full_transport.py",
            "src/transport/{name}_runtime_transport.py",
            "src/transport/{name}_new_transport.py",
            "src/transport/factory.py",
            "src/common/config.py",
            "tests/test_{name}_transport.py",
            "docs/transports/{name}.md",
            "config/examples/{name}_transport.yaml",
        ],
        required_evidence=[
            "runtime_connect",
            "runtime_send_recv",
            "syntax_valid",
        ],
        validation_commands=[
            "python3 -m py_compile src/transport/{name}_transport.py",
        ],
        max_output_files=1,
        allow_partial_stage=False,
        next_stage="integration_wiring",
    )

    stage_2 = TaskModuleStage(
        stage_name="integration_wiring",
        description="Wire runtime transport into factory, config, and config example",
        allowed_edit_patterns=[
            "src/transport/factory.py",
            "src/common/config.py",
            "config/examples/{name}_transport.yaml",
        ],
        required_edit_patterns=[
            "src/transport/factory.py",
            "src/common/config.py",
        ],
        required_create_patterns=[
            "config/examples/{name}_transport.yaml",
        ],
        forbidden_patterns=[
            "src/transport/{name}_transport.py",
            "src/transport/{name}_full_transport.py",
            "tests/test_{name}_transport.py",
            "docs/transports/{name}.md",
        ],
        required_evidence=[
            "factory_registered",
            "config_allowed",
            "config_example_exists",
        ],
        validation_commands=[
            "python3 -m py_compile src/transport/factory.py src/common/config.py",
        ],
        max_output_files=3,
        allow_partial_stage=False,
        next_stage="tests_docs_config",
    )

    stage_3 = TaskModuleStage(
        stage_name="tests_docs_config",
        description="Add roundtrip tests and documentation",
        allowed_edit_patterns=[
            "tests/test_{name}_transport.py",
            "docs/transports/{name}.md",
        ],
        allowed_create_patterns=[
            "tests/test_{name}_transport.py",
            "docs/transports/{name}.md",
        ],
        required_edit_patterns=[],
        required_create_patterns=[
            "tests/test_{name}_transport.py",
            "docs/transports/{name}.md",
        ],
        forbidden_patterns=[
            "src/transport/{name}_transport.py",
            "src/transport/factory.py",
            "src/common/config.py",
            "config/examples/{name}_transport.yaml",
        ],
        required_evidence=[
            "roundtrip_test",
            "docs_updated",
        ],
        validation_commands=[
            "python3 -m pytest tests/test_{name}_transport.py -v",
        ],
        max_output_files=2,
        allow_partial_stage=False,
        next_stage="final_validation",
    )

    stage_4 = TaskModuleStage(
        stage_name="final_validation",
        description="Run all validation gates and tunnel smoke",
        allowed_edit_patterns=[],
        required_edit_patterns=[],
        forbidden_patterns=[],
        required_evidence=[
            "compileall",
            "full_pytest",
            "tunnel_smoke",
        ],
        validation_commands=[
            "python3 -m compileall src tests",
            "python3 -m pytest tests/ -q",
        ],
        max_output_files=0,
        allow_partial_stage=False,
        next_stage=None,
    )

    return [stage_1, stage_2, stage_3, stage_4]


# ---------------------------------------------------------------------------
# StageContext builder
# ---------------------------------------------------------------------------

def build_stage_context(
    stage: TaskModuleStage,
    transport_name: str,
    repo_root: str = ".",
) -> StageContext:
    """Build a StageContext with target file content for a stage.

    Reads target files from disk and populates content_excerpt, full_content,
    stable_anchors, and recommended_strategy for each resolved target file.

    Rules:
      - tests/docs files: provide file tail excerpt + class/heading anchors.
        If file is small (< 200 lines), include full_content.
        Default strategy: append if no stable anchor found.
      - integration_wiring files: provide relevant registration snippets.
      - runtime_core: only provide the transport file content.
      - If a file doesn't exist, mark exists=False and recommend create strategy.
    """
    context = StageContext(stage_name=stage.stage_name)
    target_paths: set[str] = set()

    # Collect target paths from allowed_edit + allowed_create patterns
    for pat in stage.allowed_edit_patterns + stage.allowed_create_patterns:
        resolved = pat.replace("{name}", transport_name)
        target_paths.add(resolved)

    for pat in stage.required_edit_patterns + stage.required_create_patterns:
        resolved = pat.replace("{name}", transport_name)
        target_paths.add(resolved)

    for path in sorted(target_paths):
        tf = _build_target_file(path, repo_root)
        context.target_files.append(tf)

    return context


def _build_target_file(path: str, repo_root: str) -> StageTargetFile:
    """Build a StageTargetFile for a single file path.

    Reads content from disk and determines the best edit strategy.
    """
    import os as _os
    full_path = _os.path.join(repo_root, path)
    exists = _os.path.isfile(full_path)
    full_content = None
    content_excerpt = ""
    stable_anchors: list[str] = []
    recommended_strategy = "exact_replace"
    reason = ""

    if exists:
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                full_content = f.read()
        except Exception:
            full_content = None

        if full_content:
            lines = full_content.split("\n")
            line_count = len(lines)

            # For small files, keep full content
            if line_count < 200:
                content_excerpt = full_content
            else:
                # First 30 + last 40 lines
                content_excerpt = "\n".join(lines[:30]) + "\n...\n" + "\n".join(lines[-40:])

            # Extract stable anchors
            stable_anchors = _extract_stable_anchors(path, full_content)

            # Determine strategy
            if path.startswith("tests/") or path.startswith("docs/"):
                if any(a.startswith("class Test") or a.startswith("# ") or a.startswith("## ")
                       for a in stable_anchors):
                    recommended_strategy = "append"
                    reason = "tests/docs files: append new sections, avoid fragile FIND on existing content"
                elif line_count < 200:
                    recommended_strategy = "whole_file_replace"
                    reason = "file is small enough for safe whole-file replacement"
                else:
                    recommended_strategy = "append"
                    reason = "large file without clear anchors, append recommended"
            elif path.startswith("src/transport/factory.py") or path.startswith("src/common/config.py"):
                recommended_strategy = "exact_replace"
                reason = "registration files: use exact FIND/REPLACE with provided anchors"
            else:
                recommended_strategy = "exact_replace"
                reason = "use provided anchors for exact matching"
    else:
        recommended_strategy = "create"
        reason = "file does not exist yet, use ACTION: create"

    return StageTargetFile(
        path=path,
        exists=exists,
        content_excerpt=content_excerpt,
        full_content=full_content,
        stable_anchors=stable_anchors,
        recommended_strategy=recommended_strategy,
        reason=reason,
    )


def _extract_stable_anchors(path: str, content: str) -> list[str]:
    """Extract stable anchor points from file content.

    For Python files: class/function definitions, import lines.
    For markdown files: headings.
    """
    import re as _re
    anchors: list[str] = []

    if path.endswith(".py"):
        # class definitions
        for m in _re.finditer(r'^class (\w+)', content, _re.MULTILINE):
            anchors.append(f"class {m.group(1)}")
        # function definitions
        for m in _re.finditer(r'^def (test_\w+|setUp|tearDown|setUpClass|tearDownClass)', content, _re.MULTILINE):
            anchors.append(f"def {m.group(1)}")
        # import lines (first 5)
        import_lines = [l.strip() for l in content.split("\n") if l.startswith("import ") or l.startswith("from ")]
        anchors.extend(import_lines[:5])

    elif path.endswith(".md"):
        for m in _re.finditer(r'^(#{1,4})\s+(.+)', content, _re.MULTILINE):
            anchors.append(f"{m.group(1)} {m.group(2).strip()}")

    return anchors


def build_stage_context_prompt_section(ctx: StageContext) -> str:
    """Build a prompt section describing target files and edit strategies.

    Injected into the Stage prompt so the LLM knows what files exist,
    what their content looks like, and how to edit them.
    """
    if not ctx.target_files:
        return ""

    lines = [
        "STAGE TARGET FILES",
        "These files exist on disk. Use EXACT content below for FIND anchors.",
        "",
    ]

    for tf in ctx.target_files:
        lines.append(f"--- {tf.path}")
        lines.append(f"Status: {'EXISTS' if tf.exists else 'NEW FILE'}")
        lines.append(f"Recommended strategy: {tf.recommended_strategy}")

        if tf.stable_anchors:
            lines.append("Stable anchors:")
            for a in tf.stable_anchors[:15]:
                lines.append(f"  - {a}")

        if tf.content_excerpt:
            if tf.full_content and len(tf.full_content) < 4000:
                lines.append("FULL FILE CONTENT:")
            else:
                lines.append("File content excerpt:")
            lines.append("```")
            lines.append(tf.content_excerpt[:6000])
            lines.append("```")

        lines.append("")

    # Strategy guidance
    lines.append("EDIT STRATEGY GUIDANCE:")
    for tf in ctx.target_files:
        if tf.recommended_strategy == "append":
            lines.append(f"  {tf.path}: PREFER append at end of file. Do NOT use FIND on unverified content.")
        elif tf.recommended_strategy == "whole_file_replace":
            lines.append(f"  {tf.path}: Use whole-file replace with the full content provided above.")
        elif tf.recommended_strategy == "exact_replace":
            lines.append(f"  {tf.path}: Use exact FIND/REPLACE with anchors listed above.")
        elif tf.recommended_strategy == "create":
            lines.append(f"  {tf.path}: Use ACTION: create (file does not exist yet).")

    return "\n".join(lines)


def _build_module_registry() -> dict[str, TaskModuleContract]:
    """Build and return the module registry.

    Each module defines the boundary box for one class of tasks.
    """

    # -- transport_runtime --
    transport_runtime = TaskModuleContract(
        module_name="transport_runtime",
        description="Add or upgrade a transport to full runtime capability",
        task_types=["transport_addition", "feature_addition", "transport_change"],
        implementation_levels=["runtime"],
        # Template patterns — {name} is substituted with target_transport
        required_edit_patterns=[
            "src/transport/{name}_transport.py",
            "src/transport/factory.py",
            "src/common/config.py",
            "tests/test_{name}_transport.py",
            "docs/transports/{name}.md",
        ],
        required_create_patterns=[
            "config/examples/{name}_transport.yaml",
        ],
        forbidden_patterns=[
            "src/transport/{name}_full_transport.py",
            "src/transport/{name}_runtime_transport.py",
            "src/transport/{name}_new_transport.py",
        ],
        require_tests=True,
        require_docs=True,
        require_config=True,
        require_tunnel_smoke=True,
        allow_stub=False,
        allow_default_change=False,
        allow_completed_with_warnings=False,
        extra_files_require_reason=True,
        required_evidence=[
            "factory_registered",
            "config_allowed",
            "runtime_connect",
            "runtime_send_recv",
            "roundtrip_test",
            "tunnel_smoke",
            "docs_updated",
            "config_example_exists",
        ],
        forbidden_degradations=[
            "skeleton_only_implementation",
            "isolated_runtime_file",
            "missing_factory_registration",
            "missing_roundtrip_test",
            "default_switch_without_runtime_gate",
        ],
        validation_commands=[
            "python3 -m pytest tests/ -q",
            "python3 -m compileall src tests",
        ],
        # ---- Staged generation ----
        staged_generation_required=True,
        stages=_build_stages_for_transport_runtime(),
    )

    # -- transport_skeleton --
    transport_skeleton = TaskModuleContract(
        module_name="transport_skeleton",
        description="Add a transport skeleton/stub (no runtime behavior)",
        task_types=["transport_addition", "feature_addition"],
        implementation_levels=["skeleton"],
        require_tests=True,
        require_docs=True,
        require_config=True,
        require_tunnel_smoke=False,
        allow_stub=True,
        allow_default_change=False,
        allow_completed_with_warnings=True,
        extra_files_require_reason=True,
        required_evidence=[
            "importable",
            "constructable",
            "factory_registered",
            "config_allowed",
            "skeleton_error",
            "docs_updated",
        ],
        forbidden_degradations=[
            "default_switch_to_skeleton",
        ],
    )

    # -- default_transport_change --
    default_transport_change = TaskModuleContract(
        module_name="default_transport_change",
        description="Switch the default transport to a different protocol",
        task_types=["transport_change", "config_change"],
        implementation_levels=["runtime"],
        require_tests=True,
        require_docs=True,
        require_config=True,
        require_tunnel_smoke=True,
        allow_stub=False,
        allow_default_change=True,
        allow_completed_with_warnings=False,
        extra_files_require_reason=True,
        required_evidence=[
            "runtime_connect",
            "runtime_send_recv",
            "roundtrip_test",
            "tunnel_smoke",
            "default_config_load",
            "runtime_gate_for_default",
        ],
        forbidden_degradations=[
            "default_switch_to_skeleton",
        ],
    )

    # -- docs_only --
    docs_only = TaskModuleContract(
        module_name="docs_only",
        description="Documentation-only changes, no source code modifications",
        task_types=["docs_update"],
        implementation_levels=["docs_only"],
        allowed_edit_patterns=["README.md", "docs/**"],
        forbidden_patterns=["src/**", "config/**", "scripts/**", "tests/**"],
        require_tests=False,
        require_docs=True,
        require_config=False,
        require_tunnel_smoke=False,
        allow_stub=False,
        allow_default_change=False,
        allow_completed_with_warnings=True,
        extra_files_require_reason=True,
        required_evidence=["no_source_changes"],
        forbidden_degradations=[
            "SOURCE CODE CHANGE: Must not modify .py files in docs-only task",
        ],
    )

    # -- llm_workflow --
    llm_workflow = TaskModuleContract(
        module_name="llm_workflow",
        description="Changes to the LLM agent pipeline itself (planner, validator, patch generator, retry)",
        task_types=["bugfix", "feature_addition", "refactor"],
        implementation_levels=["runtime", "bugfix", "refactor"],
        allowed_edit_patterns=["src/llm/**", "scripts/llm_task.py"],
        allowed_create_patterns=["src/llm/**", "tests/test_llm*.py", "tests/test_patch*.py"],
        required_edit_patterns=[],
        required_context_patterns=["src/llm/", "scripts/llm_task.py"],
        require_tests=True,
        require_docs=False,
        require_config=False,
        require_tunnel_smoke=False,
        allow_stub=False,
        allow_default_change=False,
        allow_completed_with_warnings=True,
        extra_files_require_reason=True,
        required_evidence=["existing_tests_pass"],
        forbidden_degradations=[
            "BEHAVIOR CHANGE: Must not change transport implementations or config defaults",
        ],
    )

    registry = {
        "transport_runtime": transport_runtime,
        "transport_skeleton": transport_skeleton,
        "default_transport_change": default_transport_change,
        "docs_only": docs_only,
        "llm_workflow": llm_workflow,
    }

    return registry


_MODULE_REGISTRY: dict[str, TaskModuleContract] | None = None


def get_module_registry() -> dict[str, TaskModuleContract]:
    global _MODULE_REGISTRY
    if _MODULE_REGISTRY is None:
        _MODULE_REGISTRY = _build_module_registry()
    return _MODULE_REGISTRY


# ---------------------------------------------------------------------------
# TaskModuleResolver
# ---------------------------------------------------------------------------

def resolve_task_module(
    intent_contract,
    repo_root: str = ".",
) -> TaskModuleResolution:
    """Resolve which TaskModuleContract applies to an IntentContract.

    Args:
        intent_contract: IntentContract from the Planner.
        repo_root: Repository root directory for file-existence checks.

    Returns:
        TaskModuleResolution with selected module and expanded file lists.
    """
    ic = intent_contract
    target = ic.target_transport
    level = ic.implementation_level
    task_type = ic.task_type

    registry = get_module_registry()
    warnings: list[str] = []

    # ---- Rule 1: runtime_required + target_transport → transport_runtime ----
    if ic.runtime_required and target and not ic.requires_default_change:
        module = registry["transport_runtime"]
        resolution = _expand_module(module, target, repo_root)
        resolution.selected_module = "transport_runtime"
        resolution.confidence = 0.95
        resolution.reason = (
            f"runtime_required=true, target_transport={target}, "
            f"implementation_level={level}"
        )
        resolution.warnings = warnings
        return resolution

    # ---- Rule 2: skeleton + target_transport → transport_skeleton ----
    if level == "skeleton" and target and not ic.runtime_required:
        module = registry["transport_skeleton"]
        resolution = _expand_module(module, target, repo_root)
        resolution.selected_module = "transport_skeleton"
        resolution.confidence = 0.90
        resolution.reason = (
            f"implementation_level=skeleton, target_transport={target}"
        )
        resolution.warnings = warnings
        return resolution

    # ---- Rule 3: requires_default_change → default_transport_change ----
    if ic.requires_default_change:
        module = registry["default_transport_change"]
        resolution = _expand_module(module, target, repo_root)
        resolution.selected_module = "default_transport_change"
        resolution.confidence = 0.90
        resolution.reason = f"requires_default_change=true, target={target}"
        if ic.runtime_required:
            resolution.required_evidence = list(module.required_evidence)
        resolution.warnings = warnings
        return resolution

    # ---- Rule 4: docs_only → docs_only ----
    if level == "docs_only" or ic.requires_no_behavior_change and not ic.runtime_required:
        module = registry["docs_only"]
        resolution = _expand_module(module, target, repo_root)
        resolution.selected_module = "docs_only"
        resolution.confidence = 0.90
        resolution.reason = f"implementation_level=docs_only"
        resolution.warnings = warnings
        return resolution

    # ---- Rule 5: llm workflow tasks ----
    _LLM_TASK_KEYWORDS = [
        "llm", "agent", "planner", "patch", "validator", "retry",
        "intent", "prompt", "patch_generator", "task_planner",
    ]
    is_llm_workflow = (
        task_type in ("bugfix", "feature_addition", "refactor")
        and any(
            kw in (ic.original_request or "").lower()
            for kw in _LLM_TASK_KEYWORDS
        )
    )
    if is_llm_workflow:
        module = registry["llm_workflow"]
        resolution = _expand_module(module, target, repo_root)
        resolution.selected_module = "llm_workflow"
        resolution.confidence = 0.85
        resolution.reason = "LLM workflow keywords detected in request"
        resolution.warnings = warnings
        return resolution

    # ---- Rule 6: transport_addition without runtime → transport_skeleton (fallback) ----
    if task_type in ("transport_addition", "feature_addition", "transport_change") and target:
        if level == "runtime":
            module = registry["transport_runtime"]
            resolution = _expand_module(module, target, repo_root)
            resolution.selected_module = "transport_runtime"
            resolution.confidence = 0.85
            resolution.reason = f"transport task type with target={target}, level={level}"
            resolution.warnings = warnings
            return resolution
        else:
            module = registry["transport_skeleton"]
            resolution = _expand_module(module, target, repo_root)
            resolution.selected_module = "transport_skeleton"
            resolution.confidence = 0.80
            resolution.reason = f"transport task type with target={target}, level={level}"
            resolution.warnings = warnings
            return resolution

    # ---- Fallback: no specific module ----
    resolution = TaskModuleResolution(
        selected_module="general",
        confidence=0.5,
        reason="No specific module matched; using general/unconstrained mode",
        target_transport=target,
        warnings=["No TaskModuleContract matched — task is unconstrained"],
    )
    return resolution


def _expand_module(
    module: TaskModuleContract,
    target_transport: str | None,
    repo_root: str = ".",
) -> TaskModuleResolution:
    """Expand a TaskModuleContract into concrete file lists for a target transport.

    Substitutes {name} placeholders in patterns with the actual transport name.
    Checks file existence to decide must_edit vs must_create.
    """
    t = target_transport
    resolution = TaskModuleResolution(
        selected_module=module.module_name,
        confidence=0.0,
        reason="",
        target_transport=t,
        required_evidence=list(module.required_evidence),
        staged_generation_required=module.staged_generation_required,
        stages=list(module.stages),
    )

    # Expand patterns with transport name
    required_edit: list[str] = []
    required_create: list[str] = []
    forbidden: list[str] = []
    allowed_edit: list[str] = []

    for pat in module.required_edit_patterns:
        expanded = pat.format(name=t) if t and "{name}" in pat else pat
        full = os.path.join(repo_root, expanded)
        if os.path.isfile(full):
            required_edit.append(expanded)
        else:
            required_create.append(expanded)

    for pat in module.required_create_patterns:
        expanded = pat.format(name=t) if t and "{name}" in pat else pat
        full = os.path.join(repo_root, expanded)
        if os.path.isfile(full):
            required_edit.append(expanded)
        else:
            required_create.append(expanded)

    for pat in module.forbidden_patterns:
        expanded = pat.format(name=t) if t and "{name}" in pat else pat
        forbidden.append(expanded)

    for pat in module.allowed_edit_patterns:
        expanded = pat.format(name=t) if t and "{name}" in pat else pat
        allowed_edit.append(expanded)

    if module.allowed_create_patterns:
        for pat in module.allowed_create_patterns:
            expanded = pat.format(name=t) if t and "{name}" in pat else pat
            allowed_edit.append(expanded)

    resolution.required_files = sorted(set(required_edit + required_create))
    resolution.allowed_files = sorted(set(allowed_edit))
    resolution.forbidden_files = sorted(set(forbidden))

    return resolution


# ---------------------------------------------------------------------------
# Module contract lookup helpers
# ---------------------------------------------------------------------------

def get_module_contract(module_name: str) -> TaskModuleContract | None:
    """Look up a module contract by name."""
    return get_module_registry().get(module_name)


def list_module_names() -> list[str]:
    """List all registered module names."""
    return sorted(get_module_registry().keys())


def get_transport_runtime_contract(target_transport: str, repo_root: str = ".") -> TaskModuleContract:
    """Build a resolved transport_runtime contract for a specific transport.

    Expands {name} placeholders and checks file existence to produce
    a fully concrete contract ready for ImpactExpander consumption.
    """
    base = get_module_contract("transport_runtime")
    if base is None:
        raise ValueError("transport_runtime module not found in registry")

    t = target_transport

    # Build resolved patterns for this specific transport
    contract = TaskModuleContract(
        module_name="transport_runtime",
        description=f"Runtime transport implementation for {t}",
        task_types=list(base.task_types),
        implementation_levels=list(base.implementation_levels),
        require_tests=base.require_tests,
        require_docs=base.require_docs,
        require_config=base.require_config,
        require_tunnel_smoke=base.require_tunnel_smoke,
        allow_stub=base.allow_stub,
        allow_default_change=base.allow_default_change,
        allow_completed_with_warnings=base.allow_completed_with_warnings,
        extra_files_require_reason=base.extra_files_require_reason,
        required_evidence=list(base.required_evidence),
        forbidden_degradations=list(base.forbidden_degradations),
        forbidden_evidence_failures=list(base.forbidden_evidence_failures),
        validation_commands=list(base.validation_commands),
    )

    # --- Transport-specific required files ---
    contract.required_edit_patterns = [
        f"src/transport/{t}_transport.py",
        "src/transport/factory.py",
        "src/common/config.py",
        f"tests/test_{t}_transport.py",
        f"docs/transports/{t}.md",
    ]

    contract.required_create_patterns = [
        f"config/examples/{t}_transport.yaml",
    ]

    # Move existing files from create to edit
    for pat in list(contract.required_create_patterns):
        full = os.path.join(repo_root, pat)
        if os.path.isfile(full):
            contract.required_create_patterns.remove(pat)
            contract.required_edit_patterns.append(pat)

    # --- Forbidden bypass patterns ---
    contract.forbidden_patterns = [
        f"src/transport/{t}_full_transport.py",
        f"src/transport/{t}_runtime_transport.py",
        f"src/transport/{t}_new_transport.py",
    ]

    return contract


def check_module_boundary(
    resolution: TaskModuleResolution | None,
    patch_file_paths: list[str],
) -> dict:
    """Check whether patch file paths respect the module boundary.

    Args:
        resolution: TaskModuleResolution from resolve_task_module(), or None.
        patch_file_paths: Files referenced in the generated patch.

    Returns:
        Dict with boundary check results.
    """
    if resolution is None:
        return {
            "module_boundary_status": "not_run",
            "forbidden_file_changes": [],
            "missing_required_files": [],
            "missing_required_evidence": [],
            "extra_files_without_reason": [],
            "module_completion_allowed": True,
        }

    forbidden_hits: list[str] = []
    missing_required: list[str] = []
    extra_files: list[str] = []

    # Check forbidden patterns
    for f in patch_file_paths:
        for forbidden_pat in resolution.forbidden_files:
            if fnmatch.fnmatch(f, forbidden_pat) or f == forbidden_pat:
                forbidden_hits.append(f)
                break

    # Check required files
    for req in resolution.required_files:
        found = any(
            fnmatch.fnmatch(p, req) or p == req
            for p in patch_file_paths
        )
        if not found:
            missing_required.append(req)

    # Check extra files (not in allowed or required)
    if patch_file_paths and (resolution.allowed_files or resolution.required_files):
        for f in patch_file_paths:
            is_allowed = any(
                fnmatch.fnmatch(f, pat) or f == pat
                for pat in (resolution.allowed_files + resolution.required_files)
            )
            if not is_allowed:
                extra_files.append(f)

    # Determine boundary status
    if forbidden_hits:
        status = "failed"
    elif missing_required:
        status = "failed"
    else:
        status = "passed"

    module_ok = len(forbidden_hits) == 0 and len(missing_required) == 0

    return {
        "module_boundary_status": status,
        "forbidden_file_changes": forbidden_hits,
        "missing_required_files": missing_required,
        "missing_required_evidence": [],  # filled in by UserIntentValidator
        "extra_files_without_reason": extra_files,
        "module_completion_allowed": module_ok,
    }
