"""File Selection Consistency Validator.

Runs after ImpactExpander and before ContextBuilder to enforce hard constraints
on the final FileSelection. Catches conflicts, missing required files, and
boundary violations before the selection reaches PatchGenerator.

Design:
  module_resolution / IntentContract hard constraints
    > ImpactExpander final FileSelection
    > FileRetriever candidates
    > ContextBuilder context trimming
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FileSelectionValidationResult:
    """Result of validating a FileSelection against module/intent constraints."""

    success: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=lambda: {
        "conflicts": [],
        "missing_required_files": [],
        "forbidden_overlaps": [],
        "create_existing_files": [],
        "disallowed_create_files": [],
        "docs_only_violations": [],
        "config_only_violations": [],
        "retriever_only_must_edit_files": [],
        "downgraded_retriever_only_files": [],
        "truncated_must_edit_files": [],
    })

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "errors": self.errors,
            "warnings": self.warnings,
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------------------
# Main validation entry point
# ---------------------------------------------------------------------------

def validate_file_selection_consistency(
    file_selection,
    repo_index,
    intent_contract=None,
    module_resolution=None,
    request: str = "",
) -> FileSelectionValidationResult:
    """Validate that a FileSelection respects module boundaries and intent constraints.

    Args:
        file_selection: FileSelection from ImpactExpander.
        repo_index: RepoIndex for file-existence checks.
        intent_contract: IntentContract (optional).
        module_resolution: TaskModuleResolution (optional).
        request: Original user request string.

    Returns:
        FileSelectionValidationResult with errors, warnings, and evidence.
    """
    result = FileSelectionValidationResult()

    # ---- Rule 1: forbidden overlap with must_edit / must_create ----
    _check_forbidden_overlap(result, file_selection)

    # ---- Rule 2: module required files coverage ----
    _check_module_required_coverage(result, file_selection, module_resolution)

    # ---- Rule 3: must_create files already exist on disk ----
    _check_must_create_existing(result, file_selection, repo_index)

    # ---- Rule 4: must_create covered by allowed create paths ----
    _check_create_allowed(result, file_selection)

    # ---- Rule 5: docs_only boundary ----
    _check_docs_only_boundary(result, file_selection, intent_contract, module_resolution)

    # ---- Rule 6: config_only boundary ----
    _check_config_only_boundary(result, file_selection, intent_contract, module_resolution)

    # ---- Rule 7: retriever-only candidates may not enter must_edit ----
    _check_retriever_only_escalation(result, file_selection, module_resolution)

    # ---- Rule 8: ContextBuilder truncation check (evidence only at this stage) ----
    _check_context_truncation_preflight(result, file_selection)

    result.success = len(result.errors) == 0
    return result


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

def _check_forbidden_overlap(result: FileSelectionValidationResult, fs) -> None:
    """Rule 1: A file cannot be in both must_edit/must_create and forbidden."""
    must_set = set(fs.must_edit_files) | set(fs.must_create_files)
    forbidden_set = set(fs.forbidden_files)
    overlaps = must_set & forbidden_set
    for path in sorted(overlaps):
        msg = f"File selection conflict: path appears in both must_edit/must_create and forbidden_files: {path}"
        result.errors.append(msg)
        result.evidence["conflicts"].append(path)
        result.evidence["forbidden_overlaps"].append(path)


def _check_module_required_coverage(
    result: FileSelectionValidationResult, fs, module_resolution
) -> None:
    """Rule 2: module_resolution.required_files must be covered by FileSelection."""
    if module_resolution is None:
        return
    required = set(getattr(module_resolution, "required_files", []) or [])
    if not required:
        return
    covered = set(fs.must_edit_files) | set(fs.must_create_files) | set(fs.must_review_files)
    for rf in sorted(required):
        # Glob-style matching for patterns like "src/shaping/*.py"
        matched = any(
            fnmatch.fnmatch(c, rf) or c == rf for c in covered
        )
        if not matched:
            msg = f"Required module file missing from FileSelection: {rf}"
            result.errors.append(msg)
            result.evidence["missing_required_files"].append(rf)


def _check_must_create_existing(
    result: FileSelectionValidationResult, fs, repo_index
) -> None:
    """Rule 3: Files in must_create_files that already exist on disk."""
    to_remove: list[str] = []
    for path in list(fs.must_create_files):
        exists = _file_exists(path, repo_index)
        if exists:
            msg = f"must_create file already exists; converted to must_edit: {path}"
            result.warnings.append(msg)
            result.evidence["create_existing_files"].append(path)
            to_remove.append(path)
    for path in to_remove:
        if path in fs.must_create_files:
            fs.must_create_files.remove(path)
        if path not in fs.must_edit_files:
            fs.must_edit_files.append(path)


def _check_create_allowed(result: FileSelectionValidationResult, fs) -> None:
    """Rule 4: must_create_files must be covered by allowed_create_paths or patterns."""
    if not fs.must_create_files:
        return
    allowed_dirs = set(fs.allowed_create_paths or [])
    allowed_patterns = list(fs.allowed_create_patterns or [])

    action_sources = getattr(fs, "action_sources", {}) or {}

    for path in fs.must_create_files:
        # Module-contract-required files are implicitly authorized for creation
        src = action_sources.get(path, "")
        if src.startswith("module_contract:"):
            continue

        covered = False
        # Check directory prefixes
        for ad in allowed_dirs:
            if path.startswith(ad.rstrip("/") + "/") or path.startswith(ad):
                covered = True
                break
        # Check fnmatch patterns
        if not covered:
            for pat in allowed_patterns:
                if fnmatch.fnmatch(path, pat):
                    covered = True
                    break

        if not covered and allowed_dirs:
            msg = f"Create path is not allowed by module contract: {path}"
            result.errors.append(msg)
            result.evidence["disallowed_create_files"].append(path)


def _check_docs_only_boundary(
    result: FileSelectionValidationResult, fs, intent_contract, module_resolution
) -> None:
    """Rule 5: docs_only tasks may not edit source files."""
    is_docs_only = _is_docs_only(intent_contract, module_resolution)
    if not is_docs_only:
        return

    for path in fs.must_edit_files:
        if _is_source_file(path):
            msg = f"docs_only task cannot edit source file: {path}"
            result.errors.append(msg)
            result.evidence["docs_only_violations"].append(path)


def _check_config_only_boundary(
    result: FileSelectionValidationResult, fs, intent_contract, module_resolution
) -> None:
    """Rule 6: config_only tasks should not edit runtime core/transport files."""
    is_config_only = _is_config_only(intent_contract, module_resolution)
    if not is_config_only:
        return

    for path in fs.must_edit_files:
        if _is_runtime_core_file(path):
            # If IntentContract explicitly forbids runtime changes, it's an error
            forbids_runtime = (
                intent_contract is not None
                and getattr(intent_contract, "requires_no_behavior_change", False)
            )
            if forbids_runtime:
                msg = f"config_only task cannot edit runtime core file: {path}"
                result.errors.append(msg)
                result.evidence["config_only_violations"].append(path)
            else:
                msg = f"config_only task editing runtime core file (review needed): {path}"
                result.warnings.append(msg)
                result.evidence["config_only_violations"].append(path)


def _check_retriever_only_escalation(
    result: FileSelectionValidationResult, fs, module_resolution
) -> None:
    """Rule 7: Files sourced only from retriever should not become must_edit."""
    action_sources = getattr(fs, "action_sources", {}) or {}
    module_required = set(getattr(module_resolution, "required_files", []) or [])

    for path in fs.must_edit_files:
        source = action_sources.get(path, "")
        is_retriever_only = (
            "candidate_promoted" in source
            or "retriever" in source.lower()
        )
        is_module_required = any(
            fnmatch.fnmatch(path, mr) or path == mr for mr in module_required
        )
        is_hardcoded = "must_edit_rule" in source or "hardcoded" in source.lower()
        is_test_or_doc = path.startswith("tests/") or path.startswith("docs/")
        is_user_path = "explicit" in source.lower()

        if is_retriever_only and not (
            is_module_required or is_hardcoded or is_test_or_doc or is_user_path
        ):
            # Downgrade to must_review
            result.evidence["retriever_only_must_edit_files"].append(path)
            result.evidence["downgraded_retriever_only_files"].append(path)
            if path in fs.must_edit_files:
                fs.must_edit_files.remove(path)
            if path not in fs.must_review_files:
                fs.must_review_files.append(path)
            msg = (
                f"Retriever-only candidate demoted from must_edit to must_review "
                f"(no module/hardcoded/user justification): {path}"
            )
            result.warnings.append(msg)


def _check_context_truncation_preflight(
    result: FileSelectionValidationResult, fs
) -> None:
    """Rule 8: Pre-flight — warn if must_edit list is large (ContextBuilder may truncate).

    The actual truncation is detected post-ContextBuilder.build() by examining
    ContextSummary.files_truncated. This check provides a pre-flight warning.
    """
    must_edit_count = len(fs.must_edit_files)
    if must_edit_count > 20:
        msg = (
            f"Large must_edit list ({must_edit_count} files); "
            f"ContextBuilder may truncate some files."
        )
        result.warnings.append(msg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file_exists(path: str, repo_index) -> bool:
    """Check if a file exists, via RepoIndex or os.path."""
    # Try RepoIndex first
    if repo_index is not None:
        for f in getattr(repo_index, "files", []) or []:
            if isinstance(f, str):
                if f == path:
                    return True
            elif hasattr(f, "path") and f.path == path:
                return True
    # Fall back to os.path
    return os.path.isfile(path)


def _is_source_file(path: str) -> bool:
    """Check if path is a Python source file under src/ (not tests/)."""
    return bool(
        path.endswith(".py")
        and (path.startswith("src/") and not path.startswith("tests/"))
    )


def _is_runtime_core_file(path: str) -> bool:
    """Check if path is a runtime core or transport implementation file."""
    runtime_prefixes = (
        "src/core/", "src/transport/", "src/forwarding/",
        "src/tun/", "src/shaping/",
    )
    return any(path.startswith(p) for p in runtime_prefixes) and path.endswith(".py")


def _is_docs_only(intent_contract, module_resolution) -> bool:
    """Determine if the task is docs-only."""
    if intent_contract is not None:
        if getattr(intent_contract, "implementation_level", "") == "docs_only":
            return True
    if module_resolution is not None:
        if getattr(module_resolution, "selected_module", "") == "docs_only":
            return True
    return False


def _is_config_only(intent_contract, module_resolution) -> bool:
    """Determine if the task is config-only."""
    if intent_contract is not None:
        if getattr(intent_contract, "implementation_level", "") == "config_only":
            return True
    if module_resolution is not None:
        if getattr(module_resolution, "selected_module", "") == "config_only":
            return True
    return False
