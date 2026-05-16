"""RepoIndexer — lightweight local repository scanner.

Scans the repository for Python/YAML/JSON/Markdown files, extracts symbols
and metadata, and produces a serializable RepoIndex. The index is used by
FileRetriever and ImpactExpander to ground file selection in on-disk reality.

LLM is NOT used for file discovery — the index is built entirely from local
static analysis (ast, yaml, json, filesystem).
"""

from __future__ import annotations

import ast
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FileInfo:
    """Metadata for a single file in the repository."""

    path: str
    file_type: str  # python, yaml, json, markdown, shell, other
    symbols: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    config_keys: list[str] = field(default_factory=list)
    is_test: bool = False
    is_doc: bool = False
    size: int = 0

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "file_type": self.file_type,
            "symbols": self.symbols,
            "imports": self.imports,
            "config_keys": self.config_keys,
            "is_test": self.is_test,
            "is_doc": self.is_doc,
            "size": self.size,
        }

    @classmethod
    def from_dict(cls, d: dict) -> FileInfo:
        return cls(
            path=d["path"],
            file_type=d["file_type"],
            symbols=d.get("symbols", []),
            imports=d.get("imports", []),
            config_keys=d.get("config_keys", []),
            is_test=d.get("is_test", False),
            is_doc=d.get("is_doc", False),
            size=d.get("size", 0),
        )


@dataclass
class RepoIndex:
    """Lightweight index of all relevant files in the repository."""

    root_dir: str
    files: dict[str, FileInfo] = field(default_factory=dict)
    file_count: int = 0
    python_count: int = 0
    test_count: int = 0
    doc_count: int = 0

    def to_dict(self) -> dict:
        return {
            "root_dir": self.root_dir,
            "file_count": self.file_count,
            "python_count": self.python_count,
            "test_count": self.test_count,
            "doc_count": self.doc_count,
            "files": {p: fi.to_dict() for p, fi in self.files.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> RepoIndex:
        index = cls(
            root_dir=d["root_dir"],
            file_count=d.get("file_count", 0),
            python_count=d.get("python_count", 0),
            test_count=d.get("test_count", 0),
            doc_count=d.get("doc_count", 0),
        )
        for path, fi_dict in d.get("files", {}).items():
            index.files[path] = FileInfo.from_dict(fi_dict)
        return index

    def summary(self) -> dict:
        return {
            "root_dir": self.root_dir,
            "file_count": self.file_count,
            "python_count": self.python_count,
            "test_count": self.test_count,
            "doc_count": self.doc_count,
        }


# ---------------------------------------------------------------------------
# Ignored paths
# ---------------------------------------------------------------------------

_IGNORED_DIRS = frozenset({
    ".git", ".llm_tasks", ".llm_index", ".pytest_cache",
    "__pycache__", "venv", ".venv", ".tox", ".mypy_cache",
    ".claude", "node_modules",
    "vpn_tunnel",
})

_IGNORED_FILES = frozenset({".env", ".gitignore"})

_IGNORED_EXTENSIONS = frozenset({".key", ".pem", ".crt", ".pyc", ".pyo"})

_DOC_PATHS = frozenset({"README.md"})

_TEST_PATTERNS = ("test_", "_test.py")


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------

class RepoIndexer:
    """Scans a repository directory and builds a RepoIndex.

    Usage:
        indexer = RepoIndexer("/path/to/repo")
        index = indexer.build()
        print(index.summary())
    """

    def __init__(self, root_dir: str):
        self._root_dir = os.path.abspath(root_dir)

    @property
    def root_dir(self) -> str:
        return self._root_dir

    def build(self) -> RepoIndex:
        """Scan the repository and return a populated RepoIndex."""
        index = RepoIndex(root_dir=self._root_dir)

        for dirpath, dirnames, filenames in os.walk(self._root_dir):
            # Filter out ignored directories in-place
            dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS]

            rel_dir = os.path.relpath(dirpath, self._root_dir)
            if rel_dir == ".":
                rel_dir = ""

            for fname in filenames:
                if fname in _IGNORED_FILES:
                    continue
                _, ext = os.path.splitext(fname)
                if ext.lower() in _IGNORED_EXTENSIONS:
                    continue

                full_path = os.path.join(dirpath, fname)
                rel_path = os.path.normpath(os.path.join(rel_dir, fname))

                try:
                    file_info = self._analyze_file(full_path, rel_path)
                except Exception:
                    continue  # Skip unreadable files

                if file_info is not None:
                    index.files[rel_path] = file_info

        # Compute counts
        index.file_count = len(index.files)
        index.python_count = sum(1 for fi in index.files.values() if fi.file_type == "python")
        index.test_count = sum(1 for fi in index.files.values() if fi.is_test)
        index.doc_count = sum(1 for fi in index.files.values() if fi.is_doc)

        return index

    def _analyze_file(self, full_path: str, rel_path: str) -> FileInfo | None:
        """Analyze a single file and return FileInfo, or None to skip."""
        try:
            size = os.path.getsize(full_path)
        except OSError:
            return None

        # Determine file type from extension
        _, ext = os.path.splitext(full_path)
        ext_lower = ext.lower()

        if ext_lower == ".py":
            file_type = "python"
        elif ext_lower in (".yaml", ".yml"):
            file_type = "yaml"
        elif ext_lower == ".json":
            file_type = "json"
        elif ext_lower == ".md":
            file_type = "markdown"
        elif ext_lower == ".sh":
            file_type = "shell"
        else:
            file_type = "other"

        # Identify test / doc
        basename = os.path.basename(full_path)
        is_test = basename.startswith("test_") or basename.endswith("_test.py")
        is_doc = (
            file_type == "markdown"
            and (basename in _DOC_PATHS or rel_path.startswith("docs" + os.sep))
        )

        symbols: list[str] = []
        imports: list[str] = []
        config_keys: list[str] = []

        # Extract language-specific symbols
        if file_type == "python":
            symbols, imports = self._extract_python_symbols(full_path)
        elif file_type in ("yaml", "json"):
            config_keys = self._extract_config_keys(full_path, file_type)

        # Skip files > 1MB (unlikely to be source)
        if size > 1_048_576:
            return None

        return FileInfo(
            path=rel_path,
            file_type=file_type,
            symbols=symbols,
            imports=imports,
            config_keys=config_keys,
            is_test=is_test,
            is_doc=is_doc,
            size=size,
        )

    # ------------------------------------------------------------------
    # Python AST extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_python_symbols(filepath: str) -> tuple[list[str], list[str]]:
        """Extract class names, function names, and imports from a Python file."""
        symbols: list[str] = []
        imports: list[str] = []

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=filepath)
        except (SyntaxError, UnicodeDecodeError, OSError):
            return symbols, imports

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                symbols.append(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Skip private methods starting with single underscore
                # (but keep dunder methods)
                if not node.name.startswith("_") or node.name.startswith("__"):
                    symbols.append(node.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    if module:
                        imports.append(f"{module}.{alias.name}")
                    else:
                        imports.append(alias.name)

        return symbols, imports

    # ------------------------------------------------------------------
    # Config key extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_config_keys(filepath: str, file_type: str) -> list[str]:
        """Extract top-level keys from YAML or JSON config files."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                if file_type in ("yaml", "yml"):
                    import yaml as _yaml
                    data = _yaml.safe_load(f)
                else:
                    data = json.load(f)
        except Exception:
            return []

        if isinstance(data, dict):
            return sorted(str(k) for k in data.keys())
        return []
