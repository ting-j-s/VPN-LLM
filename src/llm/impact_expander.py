"""ImpactExpander — expands candidate files into a full FileSelection.

Takes the raw candidate list from FileRetriever, the TaskPlan, and the RepoIndex,
and produces a FileSelection that partitions files into:
- must_edit_files: files that need code changes
- must_review_files: files to review for context
- test_files: test files to run/update
- doc_files: documentation to update
- allowed_create_paths: directories where new files may be created
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.llm.file_retriever import CandidateFile
from src.llm.repo_indexer import RepoIndex, FileInfo
from src.llm.task_rules import (
    detect_transport_name,
    get_transport_addition_required_files,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FileSelection:
    """Partitioned file selection for patch generation."""

    must_edit_files: list[str] = field(default_factory=list)
    must_create_files: list[str] = field(default_factory=list)
    must_review_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    doc_files: list[str] = field(default_factory=list)
    allowed_create_paths: list[str] = field(default_factory=list)
    allowed_create_patterns: list[str] = field(default_factory=list)
    candidates: list[CandidateFile] = field(default_factory=list)
    rejected_hints: list[str] = field(default_factory=list)
    action_sources: dict[str, str] = field(default_factory=dict)

    @property
    def all_files(self) -> list[str]:
        """All files in the selection (deduplicated)."""
        seen: set[str] = set()
        result: list[str] = []
        for f in (self.must_edit_files + self.must_create_files +
                  self.must_review_files +
                  self.test_files + self.doc_files):
            if f not in seen:
                seen.add(f)
                result.append(f)
        return result

    @property
    def has_any_edits(self) -> bool:
        return len(self.must_edit_files) > 0 or len(self.must_create_files) > 0

    def to_dict(self) -> dict:
        return {
            "must_edit_files": self.must_edit_files,
            "must_create_files": self.must_create_files,
            "must_review_files": self.must_review_files,
            "test_files": self.test_files,
            "doc_files": self.doc_files,
            "allowed_create_paths": self.allowed_create_paths,
            "allowed_create_patterns": self.allowed_create_patterns,
            "candidates": [c.to_dict() for c in self.candidates],
            "rejected_hints": self.rejected_hints,
            "action_sources": self.action_sources,
        }


# ---------------------------------------------------------------------------
# Impact expansion rules per area
# ---------------------------------------------------------------------------

# For each affected area, files/patterns that MUST be edited (if they exist)
_MUST_EDIT_RULES: dict[str, list[str]] = {
    "transport": [
        "src/transport/__init__.py",
        "src/transport/base.py",
        "src/transport/factory.py",
        "src/common/config.py",
    ],
    "transport_addition": [
        "src/transport/__init__.py",
        "src/transport/base.py",
        "src/transport/factory.py",
        "src/common/config.py",
    ],
    "core": [
        "src/core/__init__.py",
        "src/core/client_core.py",
        "src/core/server_core.py",
        "src/common/frame.py",
        "src/common/session.py",
    ],
    "tun": [
        "src/tun/__init__.py",
        "src/tun/tun_device.py",
    ],
    "config": [
        "src/common/config.py",
    ],
    "llm_agent": [
        "src/llm/__init__.py",
        "scripts/llm_task.py",
    ],
    "validation": [
        "src/llm/validation_runner.py",
        "src/llm/replacement_validator.py",
        "scripts/smoke_replacement_matrix.py",
    ],
    "security": [
        "src/llm/safety_guard.py",
    ],
    "fingerprint_evaluation": [
        "src/evaluation/fingerprint/__init__.py",
        "src/evaluation/fingerprint/burst_features.py",
        "src/evaluation/fingerprint/ngram_features.py",
        "src/evaluation/fingerprint/pcap_features.py",
        "src/evaluation/fingerprint/report.py",
    ],
    "llm_detection": [
        "src/llm/detection/__init__.py",
        "src/llm/detection/detector_report.py",
        "src/llm/detection/gate.py",
        "src/llm/detection/countermeasure_policy.py",
        "src/llm/detection/prompt_builder.py",
        "src/llm/detection/patch_loop.py",
    ],
    "traffic_shaping": [],
    "test": [],
    "docs": [],
    "ci": [],
}

# For each affected area, patterns/files that MUST be reviewed (if they exist)
_MUST_REVIEW_RULES: dict[str, list[str]] = {
    "transport": [
        "src/transport/",
        "config/server.yaml",
        "config/client.yaml",
    ],
    "transport_addition": [
        "src/transport/",
    ],
    "core": [
        "src/core/",
        "src/common/",
    ],
    "tun": [
        "src/tun/",
        "src/core/",
    ],
    "config": [
        "config/",
    ],
    "llm_agent": [
        "src/llm/",
        "scripts/llm_task.py",
    ],
    "validation": [
        "src/llm/",
        "scripts/",
    ],
    "security": [
        "src/llm/safety_guard.py",
        "src/llm/patch_generator.py",
    ],
    "fingerprint_evaluation": [
        "src/evaluation/fingerprint/",
        "scripts/trace_capture.py",
        "scripts/run_trace_scenarios.py",
        "scripts/summarize_fingerprint_reports.py",
        "docs/fingerprint_evaluation.md",
        "docs/fingerprint_results.md",
        "docs/trace_capture.md",
    ],
    "llm_detection": [
        "src/llm/detection/",
        "src/llm/",
        "scripts/llm_task.py",
        "docs/llm_detection_adversarial_loop.md",
    ],
    "traffic_shaping": [
        "src/shaping/",
        "src/transport/",
    ],
    "test": [
        "tests/",
    ],
    "docs": [
        "docs/",
        "README.md",
    ],
    "ci": [],
    "mixed_feature": [
        "src/",
        "tests/",
    ],
}

# Allowable directories for creating new files per area
_ALLOW_CREATE_RULES: dict[str, list[str]] = {
    "transport": ["src/transport/"],
    "transport_addition": ["src/transport/", "tests/", "docs/", "config/"],
    "core": ["src/core/", "src/common/", "tests/", "docs/"],
    "tun": ["src/tun/"],
    "config": ["config/"],
    "llm_agent": ["src/llm/"],
    "validation": ["src/llm/", "scripts/"],
    "test": ["tests/"],
    "docs": ["docs/"],
    "security": ["src/llm/"],
    "fingerprint_evaluation": ["src/evaluation/fingerprint/", "scripts/", "tests/", "docs/"],
    "llm_detection": ["src/llm/detection/", "src/llm/", "tests/", "docs/"],
    "traffic_shaping": ["src/shaping/", "tests/", "docs/"],
    "ci": [],
    "mixed_feature": ["src/transport/", "src/core/", "src/common/",
                      "src/llm/", "src/tun/", "tests/", "docs/",
                      "scripts/", "config/"],
}

# File naming patterns for create operations (fnmatch globs).
# README.md is deliberately excluded — it may only be edited, never created.
_ALLOW_CREATE_PATTERNS: dict[str, list[str]] = {
    "transport": ["src/transport/*_transport.py"],
    "transport_addition": [
        "src/transport/*_transport.py",
        "tests/test_*.py",
        "docs/*.md",
        "config/*.yaml", "config/*.yaml.example",
    ],
    "core": ["src/core/*.py", "src/common/*.py", "tests/test_*.py", "docs/*.md"],
    "tun": ["src/tun/*.py"],
    "config": ["config/*.yaml", "config/*.yaml.example"],
    "llm_agent": ["src/llm/*.py"],
    "validation": ["src/llm/*.py", "scripts/*.py", "scripts/*.sh"],
    "test": ["tests/test_*.py", "tests/conftest.py"],
    "docs": ["docs/*.md"],
    "security": ["src/llm/*.py"],
    "fingerprint_evaluation": [
        "src/evaluation/fingerprint/*.py",
        "scripts/*.py",
        "tests/test_*fingerprint*.py",
        "tests/test_*trace*.py",
        "docs/*fingerprint*.md",
    ],
    "llm_detection": [
        "src/llm/detection/*.py",
        "src/llm/*.py",
        "tests/test_*detection*.py",
        "tests/test_*countermeasure*.py",
        "docs/*detection*.md",
    ],
    "traffic_shaping": [
        "src/shaping/*.py",
        "tests/test_*shaping*.py",
        "tests/test_*shaper*.py",
        "docs/*shaping*.md",
        "docs/*shaper*.md",
    ],
    "ci": [],
    "mixed_feature": [
        "src/transport/*_transport.py", "src/transport/*.py",
        "src/core/*.py", "src/common/*.py", "src/llm/*.py",
        "src/tun/*.py", "tests/test_*.py", "docs/*.md",
        "scripts/*.py", "scripts/*.sh",
        "config/*.yaml", "config/*.yaml.example",
    ],
}

# Extensions/suffixes that are NEVER allowed for create, regardless of pattern match
_BLOCKED_CREATE_SUFFIXES: frozenset = {
    ".key", ".pem", ".crt", ".p12", ".pfx", ".jks", ".keystore",
    ".env", ".secret", ".token",
}

# Path prefixes that are NEVER allowed for create (even within allowed dirs)
_BLOCKED_CREATE_PREFIXES: tuple = (
    ".git/", ".claude/", ".llm_tasks/", ".pytest_cache/", "__pycache__/",
)

# File basenames that are NEVER allowed for create
_BLOCKED_CREATE_BASENAMES: frozenset = {
    ".env", ".gitignore", ".gitattributes", ".gitmodules",
    "README.md",
}


# ---------------------------------------------------------------------------
# Expander
# ---------------------------------------------------------------------------

class ImpactExpander:
    """Expand candidate files into a full FileSelection with impact analysis.

    Usage:
        expander = ImpactExpander(repo_index)
        selection = expander.expand(request="add http2 transport",
                                    plan=task_plan,
                                    candidates=retriever_candidates,
                                    affected_areas=["transport", "mixed_feature"])
    """

    def __init__(self, repo_index: RepoIndex):
        self._index = repo_index

    def expand(
        self,
        request: str,
        plan=None,
        candidates: list[CandidateFile] | None = None,
        affected_areas: list[str] | None = None,
    ) -> FileSelection:
        """Expand candidates into a FileSelection.

        Args:
            request: Original user request string.
            plan: TaskPlan or LLMTaskPlan instance.
            candidates: Candidate files from FileRetriever.
            affected_areas: Override for affected area keys.

        Returns:
            FileSelection with partitioned files.
        """
        if candidates is None:
            candidates = []

        # Determine affected area keys
        if affected_areas is not None:
            area_keys = affected_areas
        elif plan is not None:
            area_keys = self._plan_to_area_keys(plan)
        else:
            area_keys = ["mixed_feature"]

        request_lower = request.lower()

        # Check if this is a "new/addition" type request
        is_new_feature = any(kw in request_lower for kw in
                            ("new", "add", "新增", "implement", "create", "添加"))

        selection = FileSelection(candidates=list(candidates))
        action_sources: dict[str, str] = {}

        # Collect must-edit and must-review from rules
        must_edit: set[str] = set()
        must_review: set[str] = set()
        allowed_create: set[str] = set()
        allowed_create_patterns: set[str] = set()

        for area_key in area_keys:
            # Must edit: only exact-path rules (structural necessities)
            for pattern in _MUST_EDIT_RULES.get(area_key, []):
                matched = self._resolve_pattern(pattern)
                must_edit.update(matched)
                for m in matched:
                    action_sources[m] = f"must_edit_rule:{area_key}"
            # Must review: directory-pattern rules
            for pattern in _MUST_REVIEW_RULES.get(area_key, []):
                matched = self._resolve_pattern(pattern)
                for m in matched:
                    if m not in must_edit:
                        must_review.add(m)
                        if m not in action_sources:
                            action_sources[m] = f"must_review_rule:{area_key}"
            # Allowed create directories
            for path in _ALLOW_CREATE_RULES.get(area_key, []):
                allowed_create.add(path)
            # Allowed create patterns (glob-based)
            for pat in _ALLOW_CREATE_PATTERNS.get(area_key, []):
                allowed_create_patterns.add(pat)

        # Promote high-confidence candidates to must_edit.
        # Threshold is 0.95 (requires multiple strong signals; keyword alone is
        # 0.9-0.94, so solo keyword hits stay in review).
        for c in candidates:
            non_hint_sources = [s for s in c.sources if s != "planner_hint"]
            if c.score >= 0.95 and c.action == "edit" and non_hint_sources:
                if c.path in self._index.files:
                    must_edit.add(c.path)
                    must_review.discard(c.path)
                    action_sources[c.path] = f"candidate_promoted(score={c.score:.2f}, sources={c.sources})"

        # Candidates with score < 0.95 or pure planner_hint candidates go to
        # must_review (if they exist on disk). Candidate-based action_sources
        # override rule-based ones since they carry more specific signals.
        for c in candidates:
            if c.path in must_edit:
                continue
            if c.path not in self._index.files:
                continue
            non_hint_sources = [s for s in c.sources if s != "planner_hint"]
            if c.score < 0.95 or not non_hint_sources:
                if c.path not in must_review:
                    must_review.add(c.path)
                source_label = "planner_hint_only" if not non_hint_sources else f"candidate_review(score={c.score:.2f})"
                action_sources[c.path] = source_label

        # For new features, allow creation in relevant dirs
        if is_new_feature:
            for area_key in area_keys:
                for path in _ALLOW_CREATE_RULES.get(area_key, []):
                    allowed_create.add(path)
                for pat in _ALLOW_CREATE_PATTERNS.get(area_key, []):
                    allowed_create_patterns.add(pat)

        # ---- Task-type mandatory file rules ----
        task_type = getattr(plan, "task_type", "") if plan is not None else ""
        must_create_files: set[str] = set()

        if task_type in ("transport_addition", "feature_addition"):
            transport_name = detect_transport_name(request)
            if transport_name:
                required = get_transport_addition_required_files(transport_name)

                # must_edit: enforce that factory.py and config.py are in scope
                for f in required["must_edit"]:
                    if f in self._index.files:
                        must_edit.add(f)
                        action_sources[f] = f"task_rule:transport_addition(must_edit)"

                # may_edit: add if exists, otherwise skip
                for f in required["may_edit"]:
                    if f in self._index.files:
                        must_edit.add(f)
                        action_sources[f] = f"task_rule:transport_addition(may_edit)"

                # must_create: these files don't exist yet
                for f in required["must_create"]:
                    if f not in self._index.files:
                        must_create_files.add(f)
                        action_sources[f] = f"task_rule:transport_addition(must_create)"

        # Identify test files: from candidates + from rule patterns
        test_files: set[str] = set()
        for c in candidates:
            if c.action == "test" or self._index.files.get(c.path, FileInfo(path=c.path, file_type="other")).is_test:
                if c.path in self._index.files:
                    test_files.add(c.path)
                    action_sources[c.path] = f"test:candidate(action={c.action})"
        # Also include test files matching area test_patterns
        for area_key in area_keys:
            from src.llm.file_retriever import _TASK_TYPE_RULES
            area_rules = _TASK_TYPE_RULES.get(area_key, {})
            test_patterns = area_rules.get("test_patterns", [])
            for tp in test_patterns:
                for path, fi in self._index.files.items():
                    if fi.is_test and tp in path:
                        test_files.add(path)
                        if path not in action_sources:
                            action_sources[path] = f"test:area_pattern({area_key})"
        # Broader: include test files that match area_key in path name
        for path, fi in self._index.files.items():
            if fi.is_test:
                for area_key in area_keys:
                    if area_key in path.lower():
                        test_files.add(path)
                        if path not in action_sources:
                            action_sources[path] = f"test:area_key_match({area_key})"

        # Identify doc files: from candidates + index
        doc_files: set[str] = set()
        for c in candidates:
            if c.action == "doc" or self._index.files.get(c.path, FileInfo(path=c.path, file_type="other")).is_doc:
                if c.path in self._index.files:
                    doc_files.add(c.path)
                    action_sources[c.path] = f"doc:candidate(action={c.action})"
        # Always include doc files from index
        for path, fi in self._index.files.items():
            if fi.is_doc:
                doc_files.add(path)
                if path not in action_sources:
                    action_sources[path] = "doc:index"

        # Remove must_edit/must_review files that don't exist on disk
        must_edit = {f for f in must_edit if f in self._index.files}
        must_review = {f for f in must_review if f in self._index.files}

        # Check for LLM planner hints that don't exist on disk
        rejected_hints: list[str] = []
        if plan is not None:
            hints = getattr(plan, "candidate_files", []) or []
            for hint in hints:
                if hint not in self._index.files and not any(
                    hint.startswith(ac) for ac in allowed_create
                ):
                    rejected_hints.append(hint)

        # Remove must_edit/must_review entries that are test or doc files
        # (they belong in test_files/doc_files, not edit/review).
        # Also remove anything under tests/ or docs/ directories from edit/review.
        for f in list(must_edit):
            fi = self._index.files.get(f)
            if fi and (fi.is_test or fi.is_doc):
                must_edit.discard(f)
            elif f.startswith("tests/") or f.startswith("docs/"):
                must_edit.discard(f)
        for f in list(must_review):
            fi = self._index.files.get(f)
            if fi and (fi.is_test or fi.is_doc):
                must_review.discard(f)
            elif f.startswith("tests/") or f.startswith("docs/"):
                must_review.discard(f)

        # Build result
        selection.must_edit_files = sorted(must_edit)
        selection.must_create_files = sorted(must_create_files)
        selection.must_review_files = sorted(must_review - must_edit)
        selection.test_files = sorted(test_files)
        selection.doc_files = sorted(doc_files)
        selection.allowed_create_paths = sorted(allowed_create)
        selection.allowed_create_patterns = sorted(allowed_create_patterns)
        selection.rejected_hints = rejected_hints
        selection.action_sources = {k: action_sources[k] for k in sorted(action_sources)}

        return selection

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_pattern(self, pattern: str) -> list[str]:
        """Resolve a directory pattern or exact path to existing files.

        If pattern ends with /, matches all files under that directory.
        Otherwise, matches the exact file if it exists.
        """
        if pattern.endswith("/"):
            result = []
            for path in self._index.files:
                if path.startswith(pattern):
                    result.append(path)
            return result
        else:
            if pattern in self._index.files:
                return [pattern]
            return []

    @staticmethod
    def _plan_to_area_keys(plan) -> list[str]:
        """Map a TaskPlan to affected area keys."""
        from src.llm.file_retriever import _TASK_TO_AREA
        task_type = getattr(plan, "task_type", "unknown")
        return _TASK_TO_AREA.get(task_type, ["mixed_feature"])


# ---------------------------------------------------------------------------
# Public validation helper (used by PatchGenerator)
# ---------------------------------------------------------------------------

def validate_create_filename(filepath: str, allowed_patterns: list[str] | None = None) -> str | None:
    """Validate a new-file path against allowed patterns and blocked rules.

    Returns None if the path is allowed, or an error message string if blocked.
    """
    import fnmatch
    import os

    normalized = filepath.replace("\\", "/")
    basename = os.path.basename(normalized)

    # Block path traversal (check before hidden file since ../.env is traversal, not hidden)
    if ".." in normalized.split("/"):
        return f"path traversal not allowed: {filepath}"

    # Block hidden files / directories
    if basename.startswith(".") or "/." in normalized:
        return f"hidden file/directory not allowed: {filepath}"

    # Block blocked prefixes
    for prefix in _BLOCKED_CREATE_PREFIXES:
        if normalized.startswith(prefix):
            return f"blocked path prefix: {filepath}"

    # Block blocked basenames (e.g. README.md, .env)
    if basename in _BLOCKED_CREATE_BASENAMES:
        return f"creating '{basename}' is not allowed (edit only): {filepath}"

    # Block blocked suffixes
    _, ext = os.path.splitext(basename)
    if ext.lower() in _BLOCKED_CREATE_SUFFIXES:
        return f"blocked file extension: {filepath}"

    # Must have a file extension (no binary / extensionless files)
    if not ext:
        return f"file must have an extension: {filepath}"

    # Check against allowed patterns (if provided)
    if allowed_patterns:
        for pat in allowed_patterns:
            if fnmatch.fnmatch(normalized, pat):
                return None  # matched an allowed pattern
        return f"does not match any allowed create pattern: {filepath} (patterns: {allowed_patterns})"

    return None
