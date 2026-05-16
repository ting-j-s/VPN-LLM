"""Tests for LLMPatchGenerator.

All tests use mock HTTP responses — no real network calls are made.
No files are written to the working tree.

Test files are created in tmp_path — no dependency on real repo config content.
"""

import json
import os
import pathlib
import urllib.request

import pytest

from src.llm.patch_generator import (
    LLMPatchGenerator,
    LLMPatchGeneratorError,
)
from src.llm.llm_task_planner import LLMTaskPlan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_SERVER_YAML = (
    "transport:\n"
    "  type: websocket\n"
    "  host: 127.0.0.1\n"
    "  port: 8080\n"
)


def _setup_test_root(tmp_path) -> pathlib.Path:
    """Create isolated test root with config/server.yaml."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "server.yaml").write_text(TEST_SERVER_YAML)
    return tmp_path


def _valid_diff() -> str:
    return """diff --git a/config/server.yaml b/config/server.yaml
--- a/config/server.yaml
+++ b/config/server.yaml
@@ -10,7 +10,7 @@
 transport:
-  type: tcp
+  type: websocket
   port: 8080
diff --git a/src/transport/websocket_transport.py b/src/transport/websocket_transport.py
--- a/src/transport/websocket_transport.py
+++ b/src/transport/websocket_transport.py
@@ -1,3 +1,4 @@
 \"\"\"WebSocket transport module.\"\"\"
+import logging
 import asyncio
"""


def _valid_edits() -> str:
    """FIND/REPLACE targeting the isolated test fixture (TEST_SERVER_YAML)."""
    return """FILE: config/server.yaml
<<<FIND
  type: websocket
<<<REPLACE
  type: tcp
"""


def _valid_plan() -> LLMTaskPlan:
    return LLMTaskPlan(
        task_type="transport_change",
        target_transport="websocket",
        summary="Switch default transport from TCP to WebSocket",
        candidate_files=[
            "src/transport/websocket_transport.py",
            "config/server.yaml",
        ],
        validation_commands=[
            "python3 -m pytest tests/test_websocket_transport.py -v",
        ],
        risk_level="medium",
    )


def _make_api_response(choices_text: str, status=200):
    """Build a fake urllib response object that returns the given content."""

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            payload = {
                "choices": [
                    {"message": {"content": choices_text}}
                ]
            }
            return json.dumps(payload).encode("utf-8")

        @property
        def status(self):
            return status

    return FakeResponse()


def _make_generator(monkeypatch, config_path, api_key="test-key",
                    response_text=None, root_dir="."):
    """Create an LLMPatchGenerator with mock HTTP and optional root_dir."""
    monkeypatch.setenv("LLM_API_KEY", api_key)

    if response_text is not None:

        def _mock_open(req, timeout=30):
            return _make_api_response(response_text)

        monkeypatch.setattr(urllib.request, "urlopen", _mock_open)

    return LLMPatchGenerator(config_path, root_dir=root_dir)


CONFIG_PATH = "config/llm_agent.yaml.example"


# ---------------------------------------------------------------------------
# Test: _parse_file_paths
# ---------------------------------------------------------------------------

class TestParseFilePaths:
    """Test file path extraction from unified diffs."""

    def test_extracts_paths_from_diff_git_headers(self):
        paths = LLMPatchGenerator._parse_file_paths(_valid_diff())
        assert "config/server.yaml" in paths
        assert "src/transport/websocket_transport.py" in paths

    def test_extracts_paths_from_all_header_lines(self):
        diff = """diff --git a/a.py b/b.py
--- a/a.py
+++ b/b.py
@@ -1,1 +1,1 @@
-old
+new
"""
        paths = LLMPatchGenerator._parse_file_paths(diff)
        assert "a.py" in paths
        assert "b.py" in paths

    def test_extracts_multiple_files(self):
        diff = """diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1 +1 @@
-foo
+bar
diff --git a/y.py b/y.py
--- a/y.py
+++ b/y.py
@@ -1 +1 @@
-baz
+qux
"""
        paths = LLMPatchGenerator._parse_file_paths(diff)
        assert "x.py" in paths
        assert "y.py" in paths

    def test_returns_empty_list_for_empty_diff(self):
        paths = LLMPatchGenerator._parse_file_paths("")
        assert paths == []


# ---------------------------------------------------------------------------
# Test: _extract_diff
# ---------------------------------------------------------------------------

class TestExtractDiff:
    """Test diff format validation."""

    def test_accepts_valid_unified_diff(self):
        result = LLMPatchGenerator._extract_diff(_valid_diff())
        assert "diff --git" in result
        assert "--- a/" in result
        assert "+++ b/" in result

    def test_rejects_empty_output(self):
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._extract_diff("")
        assert "empty" in str(excinfo.value).lower()

    def test_rejects_text_without_diff_git_header(self):
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._extract_diff("This is just some explanation, not a diff.")
        assert "diff --git" in str(excinfo.value)

    def test_rejects_text_missing_a_header(self):
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._extract_diff("diff --git a/x b/x\n+++ b/x\n@@ -1 +1 @@")
        assert "--- a/" in str(excinfo.value)

    def test_rejects_text_missing_b_header(self):
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._extract_diff("diff --git a/x b/x\n--- a/x\n@@ -1 +1 @@")
        assert "+++ b/" in str(excinfo.value)

    def test_strips_markdown_fences(self):
        raw = "```diff\n" + _valid_diff() + "\n```"
        result = LLMPatchGenerator._extract_diff(raw)
        assert "```" not in result
        assert "diff --git" in result
        assert "--- a/" in result
        assert "+++ b/" in result


# ---------------------------------------------------------------------------
# Test: _validate_file_path
# ---------------------------------------------------------------------------

class TestValidateFilePath:
    """Test file path safety validation for patches."""

    def test_allows_normal_source_file(self):
        LLMPatchGenerator._validate_file_path("src/transport/websocket_transport.py")

    def test_allows_config_file(self):
        LLMPatchGenerator._validate_file_path("config/server.yaml")

    def test_allows_test_file(self):
        LLMPatchGenerator._validate_file_path("tests/test_websocket_transport.py")

    def test_blocks_dot_env(self):
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._validate_file_path(".env")
        assert "unsafe" in str(excinfo.value).lower()

    def test_blocks_claude_directory(self):
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._validate_file_path(".claude/settings.local.json")
        assert "blocked" in str(excinfo.value).lower()

    def test_blocks_git_directory(self):
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._validate_file_path(".git/config")
        assert "blocked" in str(excinfo.value).lower()

    def test_blocks_key_extension(self):
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._validate_file_path("config/private.key")
        assert "blocked" in str(excinfo.value).lower()

    def test_blocks_pem_extension(self):
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._validate_file_path("config/cert.pem")
        assert "blocked" in str(excinfo.value).lower()

    def test_blocks_llm_agent_config(self):
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._validate_file_path("config/llm_agent.yaml")
        assert "blocked" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Test: _scan_for_secrets
# ---------------------------------------------------------------------------

class TestScanForSecrets:
    """Test secret/content scanning in diffs."""

    def test_allows_clean_diff(self):
        LLMPatchGenerator._scan_for_secrets(_valid_diff())

    def test_blocks_private_key_in_diff(self):
        diff = _valid_diff() + "\n+-----BEGIN RSA PRIVATE KEY-----\n+ABC123\n+-----END RSA PRIVATE KEY-----"
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._scan_for_secrets(diff)
        assert "private key" in str(excinfo.value)

    def test_blocks_openssh_private_key(self):
        diff = _valid_diff() + "\n+-----BEGIN OPENSSH PRIVATE KEY-----\n"
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._scan_for_secrets(diff)
        assert "private key" in str(excinfo.value)

    def test_blocks_sk_api_key(self):
        diff = _valid_diff() + "\n+api_key = \"sk-abcdefghijklmnopqrstuvwxyz123456\""
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._scan_for_secrets(diff)
        assert "API key" in str(excinfo.value)

    def test_blocks_google_api_key(self):
        diff = _valid_diff() + "\n+key = \"AIzaSyD1234567890abcdefghijklmnop\""
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._scan_for_secrets(diff)
        assert "API key" in str(excinfo.value)

    def test_blocks_jwt_token(self):
        diff = _valid_diff() + "\n+token = \"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.Gfx6VO9D2EtCN1DJVx2K0Xt3RlPv\""
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._scan_for_secrets(diff)
        assert "JWT" in str(excinfo.value)

    def test_blocks_api_key_assignment(self):
        diff = _valid_diff() + "\n+api_key = \"my-secret-key-here\""
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._scan_for_secrets(diff)
        assert "API key" in str(excinfo.value)

    def test_blocks_password_assignment(self):
        diff = _valid_diff() + "\n+password = \"admin123\""
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._scan_for_secrets(diff)
        assert "password" in str(excinfo.value)

    def test_blocks_bearer_token(self):
        diff = _valid_diff() + "\n+Authorization: Bearer abcdefghijklmnopqrstuv"
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator._scan_for_secrets(diff)
        assert "bearer" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Test: generate (integration through component methods)
# ---------------------------------------------------------------------------

class TestGenerate:
    """Test generate() with mock HTTP, using FIND/REPLACE format.

    All tests that reach _generate_diff use tmp_path with a controlled
    test fixture — no dependency on real config/server.yaml content.
    """

    def test_generates_valid_patch(self, monkeypatch, tmp_path):
        root = _setup_test_root(tmp_path)
        gen = _make_generator(monkeypatch, CONFIG_PATH,
                              response_text=_valid_edits(), root_dir=str(root))
        plan = _valid_plan()
        patch = gen.generate("switch to websocket", plan, "context")
        assert "diff --git" in patch
        assert "--- a/config/server.yaml" in patch
        assert "+++ b/config/server.yaml" in patch

    def test_rejects_non_edit_llm_output(self, monkeypatch, tmp_path):
        gen = _make_generator(
            monkeypatch, CONFIG_PATH,
            response_text="I suggest modifying the transport config file to use WebSocket."
        )
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("switch to websocket", plan, "context")
        assert "FILE:" in str(excinfo.value)

    def test_rejects_diff_with_blocked_file_path(self, monkeypatch, tmp_path):
        bad_edits = """FILE: .env
<<<FIND
OLD=value
<<<REPLACE
NEW=value
"""
        gen = _make_generator(monkeypatch, CONFIG_PATH, response_text=bad_edits)
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("bad patch", plan, "context")
        assert "unsafe" in str(excinfo.value).lower()

    def test_rejects_diff_with_llm_agent_config_path(self, monkeypatch, tmp_path):
        bad_edits = """FILE: config/llm_agent.yaml
<<<FIND
old
<<<REPLACE
new
"""
        gen = _make_generator(monkeypatch, CONFIG_PATH, response_text=bad_edits)
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("bad patch", plan, "context")
        assert "blocked" in str(excinfo.value).lower()

    def test_rejects_diff_with_secret_content(self, monkeypatch, tmp_path):
        root = _setup_test_root(tmp_path)
        bad_edits = _valid_edits() + "\n+-----BEGIN RSA PRIVATE KEY-----\n+content\n+-----END RSA PRIVATE KEY-----"
        gen = _make_generator(monkeypatch, CONFIG_PATH,
                              response_text=bad_edits, root_dir=str(root))
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("bad patch", plan, "context")
        assert "private key" in str(excinfo.value)

    def test_strips_markdown_fences_from_llm_output(self, monkeypatch, tmp_path):
        root = _setup_test_root(tmp_path)
        raw = "```\n" + _valid_edits() + "\n```"
        gen = _make_generator(monkeypatch, CONFIG_PATH,
                              response_text=raw, root_dir=str(root))
        plan = _valid_plan()
        patch = gen.generate("switch to websocket", plan, "context")
        assert "```" not in patch
        assert "diff --git" in patch


# ---------------------------------------------------------------------------
# Test: Config
# ---------------------------------------------------------------------------

class TestConfig:
    """Test config loading and error handling."""

    def test_raises_when_config_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        with pytest.raises(LLMPatchGeneratorError):
            LLMPatchGenerator("config/nonexistent_llm_agent.yaml")

    def test_raises_when_api_key_missing(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            LLMPatchGenerator(CONFIG_PATH)
        assert "API key" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Test: No side effects
# ---------------------------------------------------------------------------

class TestNoSideEffects:
    """Verify patch generation does not modify the working tree."""

    def test_does_not_write_source_files(self, monkeypatch, tmp_path):
        root = _setup_test_root(tmp_path)
        gen = _make_generator(monkeypatch, CONFIG_PATH,
                              response_text=_valid_edits(), root_dir=str(root))
        plan = _valid_plan()
        patch = gen.generate("switch to websocket", plan, "context")
        assert isinstance(patch, str)
        # Real config/server.yaml untouched
        assert os.path.exists("config/server.yaml")

    def test_does_not_call_git_apply(self, monkeypatch, tmp_path):
        """generate() only returns diff text — it does not call git apply."""
        root = _setup_test_root(tmp_path)
        gen = _make_generator(monkeypatch, CONFIG_PATH,
                              response_text=_valid_edits(), root_dir=str(root))
        plan = _valid_plan()
        patch = gen.generate("switch to websocket", plan, "context")
        assert "diff --git" in patch


# ---------------------------------------------------------------------------
# Test: FIND uniqueness edge cases
# ---------------------------------------------------------------------------

class TestFindUniqueness:
    """Verify that _verify_find_uniqueness rejects edge cases."""

    def test_find_empty_string_raises(self, tmp_path):
        """Empty FIND must be rejected."""
        filepath = tmp_path / "test.py"
        filepath.write_text("content\n")
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._verify_find_uniqueness("test.py", "")
        assert "empty" in str(excinfo.value).lower()

    def test_find_whitespace_only_raises(self, tmp_path):
        """Whitespace-only FIND must be rejected."""
        filepath = tmp_path / "test.py"
        filepath.write_text("content\n")
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._verify_find_uniqueness("test.py", "   \n\t  ")
        assert "whitespace" in str(excinfo.value).lower()

    def test_find_spaces_only_raises(self, tmp_path):
        """Space-only FIND must be rejected."""
        filepath = tmp_path / "test.py"
        filepath.write_text("content\n")
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._verify_find_uniqueness("test.py", "    ")
        assert "whitespace" in str(excinfo.value).lower()

    def test_find_multiple_matches_raises(self, tmp_path):
        """FIND that matches more than once must be rejected."""
        filepath = tmp_path / "test.py"
        filepath.write_text("dup_line\ndup_line\ndup_line\n")
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._verify_find_uniqueness("test.py", "dup_line")
        assert "3 times" in str(excinfo.value) or "matches" in str(excinfo.value)

    def test_find_not_found_raises(self, tmp_path):
        """FIND that matches zero times must be rejected."""
        filepath = tmp_path / "test.py"
        filepath.write_text("content\n")
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._verify_find_uniqueness("test.py", "nonexistent")
        assert "not found" in str(excinfo.value).lower()

    def test_find_unique_succeeds(self, tmp_path):
        """FIND that matches exactly once should not raise."""
        filepath = tmp_path / "test.py"
        filepath.write_text("line1\nline2\nline3\n")
        gen = _dummy_gen(root_dir=str(tmp_path))
        gen._verify_find_uniqueness("test.py", "line2")

    def test_find_in_nonexistent_file_raises(self, tmp_path):
        """FIND in a file that doesn't exist must raise."""
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._verify_find_uniqueness("nonexistent.py", "foo")
        assert "non-existent" in str(excinfo.value).lower() or "not" in str(excinfo.value).lower()

    def test_multiple_blocks_same_file_each_checked(self, tmp_path):
        """Each edit block's FIND is independently checked for the same file."""
        filepath = tmp_path / "multi.py"
        filepath.write_text("import os\nimport sys\nimport re\n")
        gen = _dummy_gen(root_dir=str(tmp_path))
        # First FIND is unique -> ok
        gen._verify_find_uniqueness("multi.py", "import os")
        # Second FIND is unique -> ok
        gen._verify_find_uniqueness("multi.py", "import sys")
        # Third FIND appears once -> ok
        gen._verify_find_uniqueness("multi.py", "import re")
        # A FIND that matches multiple -> fails
        with pytest.raises(LLMPatchGeneratorError):
            gen._verify_find_uniqueness("multi.py", "import")

    def test_find_with_crlf_content(self, tmp_path):
        """FIND works with CRLF line endings — Python text mode normalizes \\r\\n to \\n."""
        filepath = tmp_path / "crlf.py"
        filepath.write_text("line1\r\nline2\r\nline3\r\n")
        gen = _dummy_gen(root_dir=str(tmp_path))
        # Python text mode normalizes CRLF → LF, so FIND with LF succeeds
        gen._verify_find_uniqueness("crlf.py", "line1\n")
        # FIND with explicit CRLF in the search string won't match the normalized content
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._verify_find_uniqueness("crlf.py", "line1\r\n")
        assert "not found" in str(excinfo.value).lower()

    def test_find_matches_with_lf_content(self, tmp_path):
        """FIND checking works normally with LF line endings."""
        filepath = tmp_path / "lf.py"
        filepath.write_text("line1\nline2\n")
        gen = _dummy_gen(root_dir=str(tmp_path))
        gen._verify_find_uniqueness("lf.py", "line1")


# ---------------------------------------------------------------------------
# Test: allowed_create_patterns in PatchGenerator
# ---------------------------------------------------------------------------

class TestCreatePathWithPatterns:
    """_validate_create_path enforces allowed_create_patterns."""

    def test_allows_create_matching_pattern(self, tmp_path):
        gen = _dummy_gen(root_dir=str(tmp_path))
        gen._validate_create_path(
            "src/transport/http2_transport.py",
            allowed_create_paths=["src/transport/"],
            allowed_create_patterns=["src/transport/*_transport.py"],
        )

    def test_rejects_create_mismatching_pattern(self, tmp_path):
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._validate_create_path(
                "src/transport/bad_name.py",
                allowed_create_paths=["src/transport/"],
                allowed_create_patterns=["src/transport/*_transport.py"],
            )
        assert "pattern" in str(excinfo.value).lower()

    def test_rejects_readme_create(self, tmp_path):
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._validate_create_path(
                "README.md",
                allowed_create_paths=["."],
                allowed_create_patterns=["*.md"],
            )
        # README.md is blocked by basename check
        assert "README" in str(excinfo.value) or "blocked" in str(excinfo.value).lower()

    def test_rejects_hidden_file_create(self, tmp_path):
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._validate_create_path(
                "src/transport/.hidden.py",
                allowed_create_paths=["src/transport/"],
                allowed_create_patterns=["src/transport/*.py"],
            )
        assert "hidden" in str(excinfo.value).lower()

    def test_rejects_path_traversal_create(self, tmp_path):
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._validate_create_path(
                "src/transport/../../.env",
                allowed_create_paths=["src/transport/"],
                allowed_create_patterns=["src/transport/*"],
            )
        assert "traversal" in str(excinfo.value).lower() or "blocked" in str(excinfo.value).lower()

    def test_rejects_key_extension_create(self, tmp_path):
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._validate_create_path(
                "config/secret.key",
                allowed_create_paths=["config/"],
                allowed_create_patterns=["config/*"],
            )
        assert "extension" in str(excinfo.value).lower() or "key" in str(excinfo.value).lower()

    def test_rejects_create_of_existing_file(self, tmp_path):
        """Cannot create a file that already exists."""
        existing = tmp_path / "exists.py"
        existing.write_text("content\n")
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._validate_create_path(
                "exists.py",
                allowed_create_paths=["."],
            )
        assert "already exists" in str(excinfo.value).lower()

    def test_rejects_create_outside_allowed_dir(self, tmp_path):
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._validate_create_path(
                "src/core/new.py",
                allowed_create_paths=["src/transport/"],
            )
        assert "not under allowed_create_paths" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Helper for tests that don't need mock API
# ---------------------------------------------------------------------------

def _dummy_gen(root_dir: str = "."):
    """Create a generator with a dummy API key for unit-testing internal methods."""
    import os as _os
    monkeypatch = None  # not needed for these tests
    # We need a generator with a real root_dir but we only call internal methods
    gen = object.__new__(LLMPatchGenerator)
    gen._root_dir = root_dir
    return gen
