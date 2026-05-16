"""ContextBuilder — builds repository context for PatchGenerator.

Reads must_edit_files and must_review_files from a FileSelection, caps each
file at 12KB, and produces:
- repo_context: a single string for the LLM prompt
- context_summary: a dict with metadata for task records
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from src.llm.impact_expander import FileSelection


# Maximum bytes to read per file (12 KB)
_MAX_FILE_BYTES = 12 * 1024

# Maximum number of must_review files to include in context
_MAX_REVIEW_FILES = 20

# Maximum total context size (bytes)
_MAX_TOTAL_BYTES = 100 * 1024

# Priority prefixes for sorting review files (lower index = higher priority)
_REVIEW_PRIORITY_PREFIXES = [
    "src/core/",
    "src/common/",
    "src/transport/",
    "config/",
    "tests/",
    "docs/",
    "src/llm/",
    "src/tun/",
    "scripts/",
]


def _prioritize_review_files(filepaths: list[str]) -> list[str]:
    """Sort review files by relevance priority, then alphabetically.

    Files matching higher-priority prefixes come first.
    """
    def _priority(path: str) -> int:
        for i, prefix in enumerate(_REVIEW_PRIORITY_PREFIXES):
            if path.startswith(prefix):
                return i
        return len(_REVIEW_PRIORITY_PREFIXES)  # lowest priority

    return sorted(filepaths, key=lambda p: (_priority(p), p))


@dataclass
class ContextSummary:
    """Summary of what was included in the repository context."""

    files_included: list[str] = field(default_factory=list)
    files_truncated: list[str] = field(default_factory=list)
    total_bytes: int = 0
    edit_files: list[str] = field(default_factory=list)
    review_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    doc_files: list[str] = field(default_factory=list)
    allowed_create_paths: list[str] = field(default_factory=list)
    allowed_create_patterns: list[str] = field(default_factory=list)
    rejected_hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "files_included": self.files_included,
            "files_truncated": self.files_truncated,
            "total_bytes": self.total_bytes,
            "edit_files": self.edit_files,
            "review_files": self.review_files,
            "test_files": self.test_files,
            "doc_files": self.doc_files,
            "allowed_create_paths": self.allowed_create_paths,
            "allowed_create_patterns": self.allowed_create_patterns,
            "rejected_hints": self.rejected_hints,
        }


class ContextBuilder:
    """Reads selected files and builds a repository context string.

    Usage:
        builder = ContextBuilder(root_dir="/path/to/repo")
        repo_context, summary = builder.build(file_selection)
        # Pass repo_context to PatchGenerator.generate()
    """

    def __init__(self, root_dir: str = "."):
        self._root_dir = os.path.abspath(root_dir)

    def build(self, file_selection: FileSelection) -> tuple[str, ContextSummary]:
        """Build repository context from a FileSelection.

        Args:
            file_selection: FileSelection from ImpactExpander.

        Returns:
            Tuple of (repo_context_string, ContextSummary).
        """
        summary = ContextSummary(
            edit_files=list(file_selection.must_edit_files),
            review_files=list(file_selection.must_review_files),
            test_files=list(file_selection.test_files),
            doc_files=list(file_selection.doc_files),
            allowed_create_paths=list(file_selection.allowed_create_paths),
            allowed_create_patterns=list(file_selection.allowed_create_patterns),
            rejected_hints=list(file_selection.rejected_hints),
        )

        lines: list[str] = []
        total_bytes = 0

        # Read must_edit_files first (most important)
        lines.append("=" * 40)
        lines.append("MUST EDIT FILES")
        lines.append("=" * 40)
        for fpath in file_selection.must_edit_files:
            content, size, truncated = self._read_file(fpath)
            total_bytes += size
            summary.files_included.append(fpath)
            if truncated:
                summary.files_truncated.append(fpath)
            lines.append(f"\n--- {fpath} ({size} bytes)")
            if truncated:
                lines.append("... (truncated at 12KB)")
            lines.append(content)

        # Read must_review_files (capped at _MAX_REVIEW_FILES, prioritized by relevance)
        if file_selection.must_review_files:
            lines.append("")
            lines.append("=" * 40)
            lines.append("MUST REVIEW FILES")
            lines.append("=" * 40)

            # Prioritize: transport/config/tests/docs > core/llm/tun > scripts/other
            prioritized = _prioritize_review_files(file_selection.must_review_files)
            capped = prioritized[:_MAX_REVIEW_FILES]
            skipped = len(file_selection.must_review_files) - len(capped)

            for fpath in capped:
                # Stop adding review files when total context approaches _MAX_TOTAL_BYTES
                if total_bytes >= _MAX_TOTAL_BYTES:
                    skipped += 1
                    continue
                content, size, truncated = self._read_file(fpath)
                total_bytes += size
                summary.files_included.append(fpath)
                if truncated:
                    summary.files_truncated.append(fpath)
                lines.append(f"\n--- {fpath} ({size} bytes)")
                if truncated:
                    lines.append("... (truncated at 12KB)")
                lines.append(content)

            if skipped > 0:
                lines.append(f"\n... ({skipped} more review files omitted "
                             f"due to size/file limits)")

        # Add test and doc file listings (paths only, not content)
        if file_selection.test_files:
            lines.append("")
            lines.append("=" * 40)
            lines.append("TEST FILES (paths only)")
            lines.append("=" * 40)
            for fpath in file_selection.test_files:
                lines.append(f"  - {fpath}")

        if file_selection.doc_files:
            lines.append("")
            lines.append("=" * 40)
            lines.append("DOC FILES (paths only)")
            lines.append("=" * 40)
            for fpath in file_selection.doc_files:
                lines.append(f"  - {fpath}")

        # Add candidate details (reasons/sources/action)
        if file_selection.candidates:
            lines.append("")
            lines.append("=" * 40)
            lines.append("CANDIDATE FILE DETAILS")
            lines.append("=" * 40)
            for c in file_selection.candidates[:30]:
                lines.append(
                    f"  {c.path} | score={c.score:.2f} | action={c.action} | "
                    f"reasons={c.reasons[:2]} | sources={c.sources}"
                )

        # Allowed create paths
        if file_selection.allowed_create_paths:
            lines.append("")
            lines.append("=" * 40)
            lines.append("ALLOWED CREATE PATHS (directories)")
            lines.append("=" * 40)
            for p in file_selection.allowed_create_paths:
                lines.append(f"  - {p}/")
        # Allowed create patterns (globs)
        if file_selection.allowed_create_patterns:
            lines.append("")
            lines.append("=" * 40)
            lines.append("ALLOWED CREATE PATTERNS (filename globs)")
            lines.append("=" * 40)
            for p in file_selection.allowed_create_patterns:
                lines.append(f"  - {p}")
            lines.append("  README.md is NOT allowed to create (edit only).")
            lines.append("  Hidden files, path traversal, blocked extensions are forbidden.")

        # Action sources (why each file got its classification)
        if file_selection.action_sources:
            lines.append("")
            lines.append("=" * 40)
            lines.append("ACTION SOURCES (why each file was classified)")
            lines.append("=" * 40)
            for fpath, reason in sorted(file_selection.action_sources.items()):
                lines.append(f"  {fpath}: {reason}")

        # Rejected hints
        if file_selection.rejected_hints:
            lines.append("")
            lines.append("=" * 40)
            lines.append("REJECTED LLM HINTS (not found on disk)")
            lines.append("=" * 40)
            for hint in file_selection.rejected_hints:
                lines.append(f"  - {hint}")

        summary.total_bytes = total_bytes
        return "\n".join(lines), summary

    def _read_file(self, rel_path: str) -> tuple[str, int, bool]:
        """Read a file, returning (content, size, was_truncated)."""
        full_path = os.path.join(self._root_dir, rel_path)
        try:
            size = os.path.getsize(full_path)
            with open(full_path, "r", encoding="utf-8") as f:
                if size > _MAX_FILE_BYTES:
                    content = f.read(_MAX_FILE_BYTES)
                    return content, size, True
                else:
                    return f.read(), size, False
        except Exception:
            return f"(unable to read {rel_path})", 0, False
