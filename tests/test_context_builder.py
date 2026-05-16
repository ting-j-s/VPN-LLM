"""Tests for ContextBuilder.

Covers:
- Building context from FileSelection
- Truncation at 12KB
- ContextSummary has correct metadata
"""

import os

from src.llm.repo_indexer import FileInfo
from src.llm.file_retriever import CandidateFile
from src.llm.impact_expander import FileSelection
from src.llm.context_builder import ContextBuilder, ContextSummary


class TestContextSummary:
    """Test ContextSummary dataclass."""

    def test_context_summary_fields(self):
        cs = ContextSummary(
            files_included=["a.py"],
            files_truncated=["b.py"],
            total_bytes=5000,
            edit_files=["a.py"],
            review_files=["c.py"],
            test_files=["test_a.py"],
            doc_files=["README.md"],
            allowed_create_paths=["src/transport/"],
            rejected_hints=["bad.py"],
        )
        d = cs.to_dict()
        assert d["files_included"] == ["a.py"]
        assert d["files_truncated"] == ["b.py"]
        assert d["total_bytes"] == 5000
        assert d["edit_files"] == ["a.py"]
        assert d["rejected_hints"] == ["bad.py"]


class TestContextBuilder:
    """Test context building."""

    def test_builds_context_from_file_selection(self, tmp_path):
        """ContextBuilder reads files from FileSelection and produces context."""
        (tmp_path / "a.py").write_text("# File A\nx = 1\n")
        (tmp_path / "b.py").write_text("# File B\ny = 2\n")
        (tmp_path / "README.md").write_text("# Project\n")

        fs = FileSelection(
            must_edit_files=["a.py"],
            must_review_files=["b.py"],
            test_files=[],
            doc_files=["README.md"],
            allowed_create_paths=["src/transport/"],
        )

        builder = ContextBuilder(str(tmp_path))
        repo_context, summary = builder.build(fs)

        assert "# File A" in repo_context
        assert "# File B" in repo_context
        assert "MUST EDIT FILES" in repo_context
        assert "MUST REVIEW FILES" in repo_context
        assert "DOC FILES" in repo_context
        assert "ALLOWED CREATE PATHS" in repo_context
        assert "src/transport/" in repo_context
        assert summary.total_bytes > 0
        assert "a.py" in summary.files_included
        assert "b.py" in summary.files_included

    def test_truncates_large_files(self, tmp_path):
        """Files larger than 12KB are truncated."""
        large_content = "x" * 15000
        (tmp_path / "large.py").write_text(large_content)

        fs = FileSelection(
            must_edit_files=["large.py"],
        )

        builder = ContextBuilder(str(tmp_path))
        repo_context, summary = builder.build(fs)

        assert "large.py" in summary.files_truncated
        assert "truncated" in repo_context.lower()

    def test_handles_missing_files_gracefully(self, tmp_path):
        """Non-existent files produce an error message in context, not an exception."""
        fs = FileSelection(
            must_edit_files=["nonexistent.py"],
        )

        builder = ContextBuilder(str(tmp_path))
        repo_context, summary = builder.build(fs)

        assert "unable to read" in repo_context.lower()

    def test_includes_candidate_details(self, tmp_path):
        """Context includes candidate file reasons/sources/actions."""
        (tmp_path / "a.py").write_text("x = 1\n")

        c = CandidateFile(
            path="a.py", score=0.9,
            reasons=["keyword match"], sources=["keyword", "symbol"],
            action="edit",
        )
        fs = FileSelection(
            must_edit_files=["a.py"],
            candidates=[c],
        )

        builder = ContextBuilder(str(tmp_path))
        repo_context, summary = builder.build(fs)

        assert "CANDIDATE FILE DETAILS" in repo_context
        assert "score=0.90" in repo_context
        assert "keyword" in repo_context

    def test_includes_rejected_hints(self, tmp_path):
        """Context mentions rejected LLM hints."""
        fs = FileSelection(
            rejected_hints=["nonexistent.py", "bad_guess.py"],
        )

        builder = ContextBuilder(str(tmp_path))
        repo_context, summary = builder.build(fs)

        assert "REJECTED LLM HINTS" in repo_context
        assert "nonexistent.py" in repo_context
        assert "bad_guess.py" in repo_context

    def test_context_summary_has_all_sections(self, tmp_path):
        """ContextSummary reflects all sections from FileSelection."""
        (tmp_path / "edit.py").write_text("e = 1\n")
        (tmp_path / "review.py").write_text("r = 1\n")
        (tmp_path / "test_t.py").write_text("def test(): pass\n")

        fs = FileSelection(
            must_edit_files=["edit.py"],
            must_review_files=["review.py"],
            test_files=["test_t.py"],
            doc_files=[],
            allowed_create_paths=["src/core/"],
            rejected_hints=["bad.py"],
        )

        builder = ContextBuilder(str(tmp_path))
        repo_context, summary = builder.build(fs)

        assert summary.edit_files == ["edit.py"]
        assert summary.review_files == ["review.py"]
        assert summary.test_files == ["test_t.py"]
        assert summary.allowed_create_paths == ["src/core/"]
        assert summary.rejected_hints == ["bad.py"]

    def test_include_test_and_doc_listings(self, tmp_path):
        """Context includes test and doc file paths even without content."""
        (tmp_path / "a.py").write_text("x = 1\n")

        fs = FileSelection(
            must_edit_files=["a.py"],
            test_files=["tests/test_a.py", "tests/test_b.py"],
            doc_files=["README.md", "docs/guide.md"],
        )

        builder = ContextBuilder(str(tmp_path))
        repo_context, summary = builder.build(fs)

        assert "TEST FILES" in repo_context
        assert "tests/test_a.py" in repo_context
        assert "tests/test_b.py" in repo_context
        assert "DOC FILES" in repo_context
        assert "README.md" in repo_context
        assert "docs/guide.md" in repo_context
