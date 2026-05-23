"""Tests for RepoIndexer.

Covers:
- Scanning Python files for symbols and imports
- Identifying test files and doc files
- Ignoring excluded directories
- Serialization to/from dict
"""

import json
import os

import pytest

from src.llm.repo_indexer import RepoIndexer, RepoIndex, FileInfo


class TestRepoIndexer:
    """Test repo scanning."""

    def test_indexes_python_symbols(self, tmp_path):
        """RepoIndexer extracts class names and function names from Python files."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "example.py").write_text("""
class MyClass:
    def my_method(self):
        pass

def my_function():
    pass

async def my_async_function():
    pass

import os
from sys import path
""")
        (src / "__init__.py").write_text("")

        indexer = RepoIndexer(str(tmp_path))
        index = indexer.build()

        assert "src/example.py" in index.files
        fi = index.files["src/example.py"]
        assert "MyClass" in fi.symbols
        assert "my_function" in fi.symbols
        assert "my_async_function" in fi.symbols
        assert "os" in fi.imports
        assert "sys.path" in fi.imports

    def test_indexes_python_imports(self, tmp_path):
        """RepoIndexer extracts import information."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "imports_demo.py").write_text("""
import os
import sys
from typing import Optional, List
from src.transport import factory
""")
        indexer = RepoIndexer(str(tmp_path))
        index = indexer.build()

        fi = index.files["src/imports_demo.py"]
        assert "os" in fi.imports
        assert "sys" in fi.imports
        assert "typing.Optional" in fi.imports
        assert "typing.List" in fi.imports
        assert "src.transport.factory" in fi.imports

    def test_identifies_test_files(self, tmp_path):
        """RepoIndexer flags test_*.py and *_test.py as test files."""
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "__init__.py").write_text("")
        (tests / "test_transport.py").write_text("def test_pass(): assert True\n")
        (tests / "transport_test.py").write_text("def test_pass(): assert True\n")
        (tests / "helper.py").write_text("def helper(): pass\n")

        indexer = RepoIndexer(str(tmp_path))
        index = indexer.build()

        assert index.files["tests/test_transport.py"].is_test is True
        assert index.files["tests/transport_test.py"].is_test is True
        assert index.files["tests/helper.py"].is_test is False

    def test_identifies_doc_files(self, tmp_path):
        """RepoIndexer flags Markdown files in docs/ and README.md as docs."""
        (tmp_path / "README.md").write_text("# Project\n")
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "guide.md").write_text("# Guide\n")

        indexer = RepoIndexer(str(tmp_path))
        index = indexer.build()

        assert index.files["README.md"].is_doc is True
        assert index.files["docs/guide.md"].is_doc is True

    def test_ignores_excluded_dirs(self, tmp_path):
        """RepoIndexer skips .git, __pycache__, .llm_tasks, .pytest_cache, venv."""
        for d in [".git", "__pycache__", ".llm_tasks", ".pytest_cache", "venv"]:
            (tmp_path / d).mkdir(exist_ok=True)
            (tmp_path / d / "some_file.py").write_text("x = 1\n")

        indexer = RepoIndexer(str(tmp_path))
        index = indexer.build()
        for path in index.files:
            for ignored in [".git", "__pycache__", ".llm_tasks", ".pytest_cache", "venv"]:
                assert not path.startswith(ignored + os.sep) and path != ignored

    def test_ignores_sensitive_files(self, tmp_path):
        """RepoIndexer skips .env, .key, .pem files."""
        (tmp_path / ".env").write_text("SECRET=123\n")
        (tmp_path / "cert.key").write_text("KEY\n")
        (tmp_path / "cert.pem").write_text("PEM\n")

        indexer = RepoIndexer(str(tmp_path))
        index = indexer.build()

        assert ".env" not in index.files
        assert "cert.key" not in index.files
        assert "cert.pem" not in index.files

    def test_extracts_yaml_config_keys(self, tmp_path):
        """RepoIndexer extracts top-level keys from YAML files."""
        config = tmp_path / "config"
        config.mkdir()
        (config / "server.yaml").write_text("transport:\n  type: tcp\nport: 8080\n")

        indexer = RepoIndexer(str(tmp_path))
        index = indexer.build()

        assert "config/server.yaml" in index.files
        fi = index.files["config/server.yaml"]
        assert "port" in fi.config_keys
        assert "transport" in fi.config_keys

    def test_extracts_json_config_keys(self, tmp_path):
        """RepoIndexer extracts top-level keys from JSON files."""
        config = tmp_path / "config"
        config.mkdir()
        (config / "settings.json").write_text('{"host": "localhost", "port": 8080}')

        indexer = RepoIndexer(str(tmp_path))
        index = indexer.build()

        fi = index.files["config/settings.json"]
        assert "host" in fi.config_keys
        assert "port" in fi.config_keys

    def test_skips_unreadable_files(self, tmp_path):
        """RepoIndexer handles unreadable files gracefully."""
        (tmp_path / "bad.py").write_text("valid syntax\n")

        indexer = RepoIndexer(str(tmp_path))
        index = indexer.build()
        assert "bad.py" in index.files
        assert index.files["bad.py"].file_type == "python"

    def test_index_summary_counts(self, tmp_path):
        """RepoIndex summary has correct counts."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "mod.py").write_text("def f(): pass\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_mod.py").write_text("def test(): pass\n")
        (tmp_path / "README.md").write_text("# Title\n")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "info.md").write_text("# Info\n")

        indexer = RepoIndexer(str(tmp_path))
        index = indexer.build()

        assert index.file_count >= 4
        assert index.python_count >= 2
        assert index.test_count >= 1
        assert index.doc_count >= 2  # README.md + docs/info.md

    def test_file_type_classification(self, tmp_path):
        """RepoIndexer correctly classifies file types."""
        (tmp_path / "script.sh").write_text("#!/bin/bash\necho hi\n")
        (tmp_path / "readme.md").write_text("# Hi\n")
        (tmp_path / "data.json").write_text("{}")
        (tmp_path / "cfg.yaml").write_text("key: val\n")
        (tmp_path / "code.py").write_text("x=1\n")

        indexer = RepoIndexer(str(tmp_path))
        index = indexer.build()

        assert index.files["script.sh"].file_type == "shell"
        assert index.files["readme.md"].file_type == "markdown"
        assert index.files["data.json"].file_type == "json"
        assert index.files["cfg.yaml"].file_type == "yaml"
        assert index.files["code.py"].file_type == "python"

    def test_repo_index_serialization(self, tmp_path):
        """RepoIndex can be serialized and deserialized."""
        (tmp_path / "mod.py").write_text("def f(): pass\n")

        indexer = RepoIndexer(str(tmp_path))
        index = indexer.build()

        d = index.to_dict()
        restored = RepoIndex.from_dict(d)

        assert restored.root_dir == index.root_dir
        assert restored.file_count == index.file_count
        assert "mod.py" in restored.files
        assert restored.files["mod.py"].symbols == ["f"]

    def test_file_info_serialization(self):
        """FileInfo can be serialized and deserialized."""
        fi = FileInfo(
            path="src/example.py",
            file_type="python",
            symbols=["MyClass"],
            imports=["os"],
            config_keys=[],
            is_test=False,
            is_doc=False,
            size=100,
        )
        d = fi.to_dict()
        restored = FileInfo.from_dict(d)
        assert restored.path == fi.path
        assert restored.symbols == fi.symbols
        assert restored.imports == fi.imports

    def test_private_methods_are_skipped(self, tmp_path):
        """Private methods (_prefix) are not included in symbols, but dunder methods are."""
        (tmp_path / "mod.py").write_text("""
class Foo:
    def _private(self):
        pass
    def __init__(self):
        pass
    def public(self):
        pass
""")
        indexer = RepoIndexer(str(tmp_path))
        index = indexer.build()

        fi = index.files["mod.py"]
        assert "_private" not in fi.symbols
        assert "__init__" in fi.symbols
        assert "public" in fi.symbols
        assert "Foo" in fi.symbols

    def test_excluded_dir_not_indexed(self, tmp_path):
        """Excluded directories (e.g. .llm_tasks) are not indexed."""
        excluded_dir = tmp_path / ".llm_tasks"
        excluded_dir.mkdir()
        (excluded_dir / "dummy.py").write_text("x = 1\n")
        # Create a normal file too
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "real.py").write_text("y = 2\n")

        indexer = RepoIndexer(str(tmp_path))
        index = indexer.build()

        assert "src/real.py" in index.files
        assert ".llm_tasks/dummy.py" not in index.files
        assert not any(p.startswith(".llm_tasks/") for p in index.files)
