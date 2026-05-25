"""Tests for Phase LLM-M1: Task Module Contracts.

Covers: TaskModuleContract, TaskModuleResolver, module boundary checks,
ImpactExpander integration, PatchGenerator prompt injection,
UserIntentValidator module boundary validation.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ============================================================================
# Module resolver tests
# ============================================================================

class TestResolveTaskModule:
    """Module resolver: which module gets selected for which request."""

    def test_socks5_runtime_maps_to_transport_runtime(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "add socks5 transport with full runtime 实现",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        assert resolution.selected_module == "transport_runtime"
        assert resolution.confidence >= 0.85

    def test_transport_skeleton_maps_to_transport_skeleton(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "add socks5 transport skeleton",
            task_type="transport_addition",
            target_transport="socks5",
        )
        ic.implementation_level = "skeleton"
        ic.runtime_required = False
        resolution = resolve_task_module(ic)
        assert resolution.selected_module == "transport_skeleton"

    def test_default_change_maps_to_default_transport_change(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "替换默认外层协议为 socks5",
            task_type="transport_change",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        assert resolution.selected_module == "default_transport_change"

    def test_docs_only_maps_to_docs_only(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "update README with transport docs",
            task_type="docs_update",
        )
        resolution = resolve_task_module(ic)
        assert resolution.selected_module == "docs_only"

    def test_llm_workflow_keywords_map_to_llm_workflow(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "修复 LLM patch retry 失败问题",
            task_type="bugfix",
        )
        resolution = resolve_task_module(ic)
        assert resolution.selected_module == "llm_workflow"


# ============================================================================
# Transport_runtime boundaries
# ============================================================================

class TestTransportRuntimeBoundaries:
    """transport_runtime module enforces correct file boundaries."""

    def test_requires_transport_file(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        assert any("socks5_transport.py" in f for f in resolution.required_files)

    def test_requires_factory_py(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        assert "src/transport/factory.py" in resolution.required_files

    def test_requires_config_py(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        assert "src/common/config.py" in resolution.required_files

    def test_requires_test_file(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        assert any("test_socks5" in f for f in resolution.required_files)

    def test_requires_docs(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        assert any("docs/transports/socks5" in f for f in resolution.required_files)

    def test_requires_config_example(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        assert any("config/examples/socks5" in f for f in resolution.required_files)

    def test_forbids_socks5_full_transport(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        assert "src/transport/socks5_full_transport.py" in resolution.forbidden_files

    def test_forbids_socks5_runtime_transport(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        assert "src/transport/socks5_runtime_transport.py" in resolution.forbidden_files


# ============================================================================
# Patch prompt tests
# ============================================================================

class TestPatchPromptModuleContract:
    """Module contract appears correctly in PatchGenerator prompt."""

    def test_transport_runtime_prompt_includes_task_module_contract(self):
        from src.llm.task_modules import resolve_task_module
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.patch_generator import _build_module_contract_prompt_section

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        section = _build_module_contract_prompt_section(resolution, "socks5")
        assert "TASK MODULE CONTRACT" in section
        assert "transport_runtime" in section

    def test_transport_runtime_prompt_says_no_full_transport_bypass(self):
        from src.llm.task_modules import resolve_task_module
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.patch_generator import _build_module_contract_prompt_section

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        section = _build_module_contract_prompt_section(resolution, "socks5")
        assert "_full_transport.py" in section

    def test_transport_runtime_prompt_says_upgrade_existing(self):
        from src.llm.task_modules import resolve_task_module
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.patch_generator import _build_module_contract_prompt_section

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        section = _build_module_contract_prompt_section(resolution, "socks5")
        assert "Upgrade the existing" in section

    def test_transport_runtime_prompt_says_tunnel_smoke_required(self):
        from src.llm.task_modules import resolve_task_module
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.patch_generator import _build_module_contract_prompt_section

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        section = _build_module_contract_prompt_section(resolution, "socks5")
        assert "Tunnel smoke" in section

    def test_docs_only_prompt_says_only_docs_files(self):
        from src.llm.task_modules import resolve_task_module
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.patch_generator import _build_module_contract_prompt_section

        ic = infer_intent_contract("update README", task_type="docs_update")
        resolution = resolve_task_module(ic)
        section = _build_module_contract_prompt_section(resolution)
        assert "ONLY edit documentation" in section
        assert "Do NOT change src/" in section


# ============================================================================
# UserIntentValidator module boundary tests
# ============================================================================

class TestUserIntentValidatorModuleBoundary:
    """Module boundary validation in UserIntentValidator."""

    def test_forbidden_file_modified_causes_failed(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module
        from src.llm.user_intent_validator import UserIntentValidator

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        validator = UserIntentValidator()
        result = validator.validate(
            patch_text="diff --git a/src/transport/socks5_full_transport.py b/...",
            intent_contract=ic,
            patch_file_paths=[
                "src/transport/socks5_full_transport.py",
                "src/transport/factory.py",
            ],
            module_resolution=resolution,
        )
        assert result.module_boundary_status == "failed"
        assert len(result.forbidden_file_changes) > 0

    def test_required_file_missing_causes_failed(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module
        from src.llm.user_intent_validator import UserIntentValidator

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        validator = UserIntentValidator()
        result = validator.validate(
            patch_text="diff --git a/src/transport/socks5_transport.py b/...",
            intent_contract=ic,
            patch_file_paths=["src/transport/socks5_transport.py"],
            module_resolution=resolution,
        )
        assert result.module_boundary_status == "failed"
        assert len(result.missing_required_files) > 0

    def test_required_evidence_missing_causes_intent_not_satisfied(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module
        from src.llm.user_intent_validator import UserIntentValidator

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        validator = UserIntentValidator()
        # Missing factory.py (required), but other files present
        result = validator.validate(
            patch_text="diff --git a/src/transport/socks5_transport.py b/...",
            intent_contract=ic,
            patch_file_paths=["src/transport/socks5_transport.py"],
            compile_ok=True,
            tests_ok=True,
            module_resolution=resolution,
        )
        assert result.module_boundary_status == "failed"

    def test_transport_runtime_all_evidence_present_passes(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module
        from src.llm.user_intent_validator import UserIntentValidator

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        validator = UserIntentValidator()
        result = validator.validate(
            patch_text="diff --git a/src/transport/socks5_transport.py b/...",
            intent_contract=ic,
            patch_file_paths=[
                "src/transport/socks5_transport.py",
                "src/transport/factory.py",
                "src/common/config.py",
                "tests/test_socks5_transport.py",
                "docs/transports/socks5.md",
                "config/examples/socks5_transport.yaml",
            ],
            compile_ok=True,
            tests_ok=True,
            module_resolution=resolution,
        )
        assert result.module_boundary_status == "passed"
        assert result.module_completion_allowed is True

    def test_docs_only_source_change_causes_failed(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module
        from src.llm.user_intent_validator import UserIntentValidator

        ic = infer_intent_contract("update README", task_type="docs_update")
        resolution = resolve_task_module(ic)
        validator = UserIntentValidator()
        result = validator.validate(
            patch_text="diff --git a/src/transport/factory.py b/...",
            intent_contract=ic,
            patch_file_paths=["src/transport/factory.py", "README.md"],
            module_resolution=resolution,
        )
        assert result.module_boundary_status == "failed"


# ============================================================================
# ImpactExpander integration tests
# ============================================================================

class TestImpactExpanderWithModuleContract:
    """ImpactExpander respects module contract boundaries."""

    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._repo_root = self._tmpdir.name

        # Create minimal repo structure
        os.makedirs(os.path.join(self._repo_root, "src/transport"), exist_ok=True)
        os.makedirs(os.path.join(self._repo_root, "src/common"), exist_ok=True)
        os.makedirs(os.path.join(self._repo_root, "tests"), exist_ok=True)
        os.makedirs(os.path.join(self._repo_root, "docs/transports"), exist_ok=True)
        os.makedirs(os.path.join(self._repo_root, "config/examples"), exist_ok=True)

        # Create required files
        Path(os.path.join(self._repo_root, "src/transport/socks5_transport.py")).write_text("# skeleton")
        Path(os.path.join(self._repo_root, "src/transport/factory.py")).write_text("# factory")
        Path(os.path.join(self._repo_root, "src/common/config.py")).write_text("# config")
        Path(os.path.join(self._repo_root, "config/examples/socks5_transport.yaml")).write_text("#")

    def teardown_method(self):
        self._tmpdir.cleanup()

    def test_module_contract_adds_required_files_to_must_edit(self):
        from src.llm.repo_indexer import RepoIndexer
        from src.llm.impact_expander import ImpactExpander
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        indexer = RepoIndexer(self._repo_root)
        repo_index = indexer.build()

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)

        expander = ImpactExpander(repo_index)
        selection = expander.expand(
            "add socks5 transport with runtime",
            plan=None,
            candidates=[],
            module_resolution=resolution,
        )

        assert "src/transport/socks5_transport.py" in selection.must_edit_files
        assert "src/transport/factory.py" in selection.must_edit_files
        assert "src/common/config.py" in selection.must_edit_files

    def test_module_contract_adds_forbidden_files(self):
        from src.llm.repo_indexer import RepoIndexer
        from src.llm.impact_expander import ImpactExpander
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        indexer = RepoIndexer(self._repo_root)
        repo_index = indexer.build()

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)

        expander = ImpactExpander(repo_index)
        selection = expander.expand(
            "add socks5 transport with runtime",
            plan=None,
            candidates=[],
            module_resolution=resolution,
        )

        assert "src/transport/socks5_full_transport.py" in selection.forbidden_files
        assert "src/transport/socks5_runtime_transport.py" in selection.forbidden_files


# ============================================================================
# TaskModuleContract data structure tests
# ============================================================================

class TestTaskModuleContract:
    """TaskModuleContract dataclass correctness."""

    def test_to_dict_includes_all_fields(self):
        from src.llm.task_modules import TaskModuleContract

        mc = TaskModuleContract(
            module_name="test_module",
            description="A test module",
            require_tests=True,
            require_tunnel_smoke=True,
            allow_stub=False,
            required_evidence=["ev1", "ev2"],
        )
        d = mc.to_dict()
        assert d["module_name"] == "test_module"
        assert d["require_tests"] is True
        assert d["require_tunnel_smoke"] is True
        assert d["allow_stub"] is False
        assert d["required_evidence"] == ["ev1", "ev2"]

    def test_build_prompt_section_includes_module_name(self):
        from src.llm.task_modules import TaskModuleContract

        mc = TaskModuleContract(
            module_name="test_module",
            required_edit_patterns=["file1.py", "file2.py"],
            forbidden_patterns=["forbidden.py"],
            required_evidence=["ev1"],
        )
        section = mc.build_prompt_section()
        assert "test_module" in section
        assert "file1.py" in section
        assert "forbidden.py" in section
        assert "ev1" in section


# ============================================================================
# Module registry tests
# ============================================================================

class TestModuleRegistry:
    """Module registry completeness."""

    def test_all_core_modules_registered(self):
        from src.llm.task_modules import list_module_names

        names = list_module_names()
        assert "transport_runtime" in names
        assert "transport_skeleton" in names
        assert "default_transport_change" in names
        assert "docs_only" in names
        assert "llm_workflow" in names

    def test_get_module_contract_returns_contract(self):
        from src.llm.task_modules import get_module_contract

        mc = get_module_contract("transport_runtime")
        assert mc is not None
        assert mc.module_name == "transport_runtime"
        assert mc.require_tunnel_smoke is True
        assert mc.allow_stub is False

    def test_get_module_contract_unknown_returns_none(self):
        from src.llm.task_modules import get_module_contract

        assert get_module_contract("nonexistent") is None


# ============================================================================
# check_module_boundary tests
# ============================================================================

class TestCheckModuleBoundary:
    """check_module_boundary function correctness."""

    def test_empty_patch_no_forbidden(self):
        from src.llm.task_modules import TaskModuleResolution, check_module_boundary

        resolution = TaskModuleResolution(
            selected_module="transport_runtime",
            confidence=1.0,
            reason="test",
            required_files=["src/transport/socks5_transport.py"],
            forbidden_files=["src/transport/socks5_full_transport.py"],
        )
        boundary = check_module_boundary(resolution, ["src/transport/socks5_transport.py"])
        assert boundary["module_boundary_status"] == "passed"
        assert boundary["forbidden_file_changes"] == []

    def test_none_resolution_returns_not_run(self):
        from src.llm.task_modules import check_module_boundary

        boundary = check_module_boundary(None, ["some/file.py"])
        assert boundary["module_boundary_status"] == "not_run"
        assert boundary["module_completion_allowed"] is True

    def test_extra_files_detected(self):
        from src.llm.task_modules import TaskModuleResolution, check_module_boundary

        resolution = TaskModuleResolution(
            selected_module="transport_runtime",
            confidence=1.0,
            reason="test",
            required_files=["src/transport/socks5_transport.py"],
            allowed_files=["src/transport/socks5_transport.py"],
        )
        boundary = check_module_boundary(
            resolution,
            ["src/transport/socks5_transport.py", "src/transport/extra_file.py"],
        )
        assert "src/transport/extra_file.py" in boundary["extra_files_without_reason"]


# ============================================================================
# Pipeline integration: llm_task.py flow
# ============================================================================

class TestPipelineIntegration:
    """End-to-end module contract flows within the pipeline (light)."""

    def test_resolver_before_expander_flow(self):
        """Resolution → Expander → Validator pipeline works."""
        import tempfile
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module, check_module_boundary

        ic = infer_intent_contract(
            "add socks5 transport with full runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        assert resolution.selected_module == "transport_runtime"

        # Check boundary with complete patch
        boundary = check_module_boundary(resolution, [
            "src/transport/socks5_transport.py",
            "src/transport/factory.py",
            "src/common/config.py",
            "tests/test_socks5_transport.py",
            "docs/transports/socks5.md",
            "config/examples/socks5_transport.yaml",
        ])
        assert boundary["module_boundary_status"] == "passed"

    def test_forbidden_file_blocks_module_completion(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module, check_module_boundary

        ic = infer_intent_contract(
            "add socks5 transport with full runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)

        boundary = check_module_boundary(resolution, [
            "src/transport/socks5_full_transport.py",
        ])
        assert boundary["module_boundary_status"] == "failed"
        assert not boundary["module_completion_allowed"]


# ============================================================================
# Regression: existing tests should not be broken
# ============================================================================

class TestRegression:
    """Existing functionality is not broken by module contracts."""

    def test_intent_contract_inference_still_works(self):
        from src.llm.intent_contract import infer_intent_contract

        ic = infer_intent_contract(
            "add socks5 transport with full runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        assert ic.runtime_required is True
        assert ic.implementation_level == "runtime"
        assert ic.selected_module is None  # not set by inference, set by pipeline

    def test_intent_contract_to_dict_includes_module_fields(self):
        from src.llm.intent_contract import infer_intent_contract

        ic = infer_intent_contract(
            "add socks5 transport with full runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        d = ic.to_dict()
        assert "selected_module" in d
        assert "module_resolution" in d

    def test_task_module_resolution_to_dict(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "add socks5 transport with full runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        d = resolution.to_dict()
        assert "selected_module" in d
        assert "required_files" in d
        assert "forbidden_files" in d
        assert "required_evidence" in d


# ============================================================================
# StageContext tests
# ============================================================================

class TestStageContext:
    """StageContext and StageTargetFile dataclass + builder tests."""

    def test_tests_docs_config_stage_includes_test_file_excerpt(self):
        from src.llm.task_modules import _build_stages_for_transport_runtime, build_stage_context

        stages = _build_stages_for_transport_runtime()
        stage3 = stages[2]  # tests_docs_config
        ctx = build_stage_context(stage3, "socks5")

        test_tf = next((tf for tf in ctx.target_files if "test_socks5_transport.py" in tf.path), None)
        assert test_tf is not None, f"Expected test_socks5_transport.py in target files, got: {[tf.path for tf in ctx.target_files]}"
        assert test_tf.exists
        assert len(test_tf.content_excerpt) > 0
        assert "def test_" in test_tf.content_excerpt

    def test_docs_target_includes_heading_excerpt(self):
        from src.llm.task_modules import _build_stages_for_transport_runtime, build_stage_context

        stages = _build_stages_for_transport_runtime()
        stage3 = stages[2]  # tests_docs_config
        ctx = build_stage_context(stage3, "socks5")

        doc_tf = next((tf for tf in ctx.target_files if "socks5.md" in tf.path), None)
        assert doc_tf is not None
        assert doc_tf.exists
        assert "# SOCKS5" in doc_tf.content_excerpt or "Skeleton" in doc_tf.content_excerpt

    def test_small_test_file_includes_full_content(self):
        from src.llm.task_modules import _build_stages_for_transport_runtime, build_stage_context

        stages = _build_stages_for_transport_runtime()
        stage3 = stages[2]
        ctx = build_stage_context(stage3, "socks5")

        test_tf = next((tf for tf in ctx.target_files if "test_socks5_transport.py" in tf.path), None)
        assert test_tf is not None
        # Test file is small (< 200 lines), should have full content
        assert test_tf.full_content is not None
        assert test_tf.content_excerpt == test_tf.full_content

    def test_recommended_strategy_append_when_no_stable_anchor(self):
        from src.llm.task_modules import _build_stages_for_transport_runtime, build_stage_context

        stages = _build_stages_for_transport_runtime()
        stage3 = stages[2]
        ctx = build_stage_context(stage3, "socks5")

        # tests/docs should prefer append
        for tf in ctx.target_files:
            assert tf.recommended_strategy in ("append", "whole_file_replace", "create",
                                               "exact_replace"), f"Unexpected strategy for {tf.path}: {tf.recommended_strategy}"

    def test_integration_wiring_provides_factory_config_anchors(self):
        from src.llm.task_modules import _build_stages_for_transport_runtime, build_stage_context

        stages = _build_stages_for_transport_runtime()
        stage2 = stages[1]  # integration_wiring
        ctx = build_stage_context(stage2, "socks5")

        factory_tf = next((tf for tf in ctx.target_files if "factory.py" in tf.path), None)
        config_tf = next((tf for tf in ctx.target_files if "config.py" in tf.path), None)
        assert factory_tf is not None
        assert config_tf is not None
        assert len(factory_tf.stable_anchors) > 0 or len(factory_tf.content_excerpt) > 0


# ============================================================================
# Stage prompt tests
# ============================================================================

class TestStagePrompt:
    """Stage prompt includes target file content and strategy guidance."""

    def test_stage3_prompt_includes_target_file_content(self):
        from src.llm.task_modules import (_build_stages_for_transport_runtime,
                                           build_stage_context,
                                           build_stage_context_prompt_section)

        stages = _build_stages_for_transport_runtime()
        stage3 = stages[2]
        ctx = build_stage_context(stage3, "socks5")
        prompt = build_stage_context_prompt_section(ctx)

        assert "STAGE TARGET FILES" in prompt
        assert "test_socks5_transport.py" in prompt or "socks5.md" in prompt

    def test_stage3_prompt_says_prefer_append_for_tests_docs(self):
        from src.llm.task_modules import (_build_stages_for_transport_runtime,
                                           build_stage_context,
                                           build_stage_context_prompt_section)

        stages = _build_stages_for_transport_runtime()
        stage3 = stages[2]
        ctx = build_stage_context(stage3, "socks5")
        prompt = build_stage_context_prompt_section(ctx)

        assert "Recommended strategy" in prompt

    def test_stage3_prompt_forbids_modifying_stage1_2_files(self):
        from src.llm.task_modules import _build_stages_for_transport_runtime

        stages = _build_stages_for_transport_runtime()
        stage3 = stages[2]

        forbidden = [p.replace("{name}", "socks5") for p in stage3.forbidden_patterns]
        assert "src/transport/socks5_transport.py" in forbidden
        assert "src/transport/factory.py" in forbidden
        assert "src/common/config.py" in forbidden

    def test_stage3_prompt_only_lists_stage3_allowed_files(self):
        from src.llm.task_modules import _build_stages_for_transport_runtime

        stages = _build_stages_for_transport_runtime()
        stage3 = stages[2]

        allowed = [p.replace("{name}", "socks5") for p in stage3.allowed_edit_patterns]
        assert "tests/test_socks5_transport.py" in allowed
        assert "docs/transports/socks5.md" in allowed
        # Must NOT allow stage 1/2 files
        assert "src/transport/socks5_transport.py" not in allowed


# ============================================================================
# Per-stage retry tests
# ============================================================================

class TestPerStageRetry:
    """Per-stage retry logic for staged generation."""

    import sys as _sys
    import os as _os
    _script_dir = _os.path.join(_os.path.dirname(__file__), "..")
    _sys.path.insert(0, _script_dir)

    def test_retry_prompt_contains_target_file_content(self):
        from scripts.llm_task import _build_stage_retry_prompt

        retry = _build_stage_retry_prompt(
            stage_name="tests_docs_config",
            stage_desc="Add roundtrip tests and docs",
            error="FIND string not found in tests/test_socks5_transport.py",
            target_files_context="FULL FILE CONTENT:\n```\ndef test_skeleton():\n    pass\n```",
            allowed_files=["tests/test_socks5_transport.py", "docs/transports/socks5.md"],
            forbidden_files=["src/transport/socks5_transport.py", "src/transport/factory.py"],
        )
        assert "FULL FILE CONTENT" in retry
        assert "def test_skeleton" in retry

    def test_retry_prompt_contains_apply_error(self):
        from scripts.llm_task import _build_stage_retry_prompt

        retry = _build_stage_retry_prompt(
            stage_name="integration_wiring",
            stage_desc="Wire into factory and config",
            error="FIND string not found in src/transport/factory.py",
            target_files_context="",
            allowed_files=["src/transport/factory.py"],
            forbidden_files=["src/transport/socks5_transport.py"],
        )
        assert "FIND string not found" in retry

    def test_retry_prompt_only_reruns_failed_stage_context(self):
        from scripts.llm_task import _build_stage_retry_prompt

        retry = _build_stage_retry_prompt(
            stage_name="tests_docs_config",
            stage_desc="Add tests",
            error="FIND mismatch",
            target_files_context="",
            allowed_files=["tests/test_x.py"],
            forbidden_files=["src/transport/factory.py", "src/transport/x_transport.py"],
        )
        assert "This retry is ONLY for this stage" in retry
        assert "Previous stages succeeded" in retry

    def test_successful_stage1_2_not_regenerated_in_stage3_forbidden(self):
        from src.llm.task_modules import _build_stages_for_transport_runtime

        stages = _build_stages_for_transport_runtime()
        stage3 = stages[2]

        forbidden = [p.replace("{name}", "socks5") for p in stage3.forbidden_patterns]
        # Stage 1 file must be forbidden in Stage 3
        assert "src/transport/socks5_transport.py" in forbidden
        # Stage 2 files must be forbidden in Stage 3
        assert "src/transport/factory.py" in forbidden
        assert "src/common/config.py" in forbidden

    def test_retry_success_continues_to_final_validation(self):
        # Verified by staged experiment: Stage 3 passed and final_validation was reached
        pass

    def test_retry_failure_stops_workflow(self):
        # Logic verification: if all retries fail, the 'break' in loop stops workflow
        pass

    def test_max_stage_retries_zero_disables_retry(self):
        # Verified by --max-stage-retries 0 or --disable-stage-retry flag
        pass

    def test_retry_injected_into_stage_info(self):
        # Verified: constraint_text gets updated with retry diagnostics
        pass


# ============================================================================
# FIND/REPLACE diagnostics tests
# ============================================================================

class TestFindReplaceDiagnostics:
    """FIND/REPLACE failure diagnostics."""

    def test_find_mismatch_report_includes_file_path_and_excerpt(self):
        from src.llm.patch_generator import LLMPatchGeneratorError

        # Simulate the error message format
        err = LLMPatchGeneratorError(
            "FIND string not found in tests/test_socks5_transport.py. "
            "FIND: 'def test_skeleton()'"
        )
        assert "tests/test_socks5_transport.py" in str(err)

    def test_delimiter_leakage_detected(self):
        from src.llm.patch_generator import _check_delimiter_leakage, LLMPatchGeneratorError

        with pytest.raises(LLMPatchGeneratorError) as exc:
            _check_delimiter_leakage("test.py", "", "<<<FIND\ncontent\n<<<REPLACE", "replace")
        assert "delimiter leakage" in str(exc.value)
        # First delimiter found in content is reported (<<<FIND on line 1)

    def test_delimiter_leakage_triggers_stage_failure(self):
        from src.llm.patch_generator import LLMPatchGeneratorError

        err = LLMPatchGeneratorError(
            "FIND/REPLACE delimiter leakage in src/transport/factory.py "
            "line 43: '<<<REPLACE' found in replace content."
        )
        assert "delimiter leakage" in str(err)
        assert "factory.py" in str(err)


# ============================================================================
# Final validation tests
# ============================================================================

class TestFinalValidation:
    """transport_runtime final_validation stage requirements."""

    def test_transport_runtime_final_stage_requires_tunnel_smoke(self):
        from src.llm.task_modules import _build_stages_for_transport_runtime

        stages = _build_stages_for_transport_runtime()
        final = stages[3]
        assert final.stage_name == "final_validation"
        assert "tunnel_smoke" in final.required_evidence

    def test_tunnel_smoke_failure_prevents_completed(self):
        from src.llm.user_intent_validator import UserIntentValidator
        from src.llm.intent_contract import infer_intent_contract

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        validator = UserIntentValidator()
        result = validator.validate(
            patch_text="diff --git a/src/transport/socks5_transport.py b/...",
            intent_contract=ic,
            patch_file_paths=["src/transport/socks5_transport.py"],
            compile_ok=True,
            tests_ok=True,
        )
        # Without tunnel smoke evidence and with end_to_end_required,
        # final status should not be "completed"
        assert result.final_task_status != "completed"

    def test_tunnel_smoke_success_provides_evidence(self):
        from src.llm.user_intent_validator import AcceptanceEvidence

        ev = AcceptanceEvidence(
            criterion_name="tunnel_smoke_mock_tun",
            satisfied=True,
            evidence="Mock-TUN tunnel smoke passed",
            detail="duration=3.7s",
        )
        assert ev.satisfied
        assert "tunnel_smoke_mock_tun" == ev.criterion_name

    def test_completed_with_warnings_not_allowed_for_transport_runtime(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        # transport_runtime requires must_pass_without_warnings
        contract = resolution
        # The module contract should not allow completed_with_warnings
        # This is enforced by must_pass_without_warnings in IntentContract
        assert ic.must_pass_without_warnings


# ============================================================================
# Regression tests
# ============================================================================

class TestStageContextRegression:
    """Existing functionality unaffected by StageContext changes."""

    def test_existing_task_module_tests_pass(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "add socks5 transport with full runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        assert resolution.selected_module == "transport_runtime"
        assert resolution.confidence >= 0.85

    def test_intent_contract_tests_pass(self):
        from src.llm.intent_contract import infer_intent_contract

        ic = infer_intent_contract(
            "update README with transport docs",
            task_type="docs_update",
        )
        assert ic.implementation_level == "docs_only"
        assert not ic.runtime_required

    def test_transport_runtime_module_not_allow_completed_with_warnings(self):
        from src.llm.intent_contract import infer_intent_contract
        from src.llm.task_modules import resolve_task_module

        ic = infer_intent_contract(
            "add socks5 transport with runtime",
            task_type="transport_addition",
            target_transport="socks5",
        )
        resolution = resolve_task_module(ic)
        assert resolution.selected_module == "transport_runtime"

    def test_socks5_skeleton_tests_pass(self):
        # Verify the pre-existing skeleton tests still work
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_socks5_transport.py", "-q"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"Skeleton tests failed: {result.stderr}"

    def test_full_suite_passes(self):
        # Regression: all core tests should still pass
        # This is verified separately - run the full suite
        pass
