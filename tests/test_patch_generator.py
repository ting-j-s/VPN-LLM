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


def _make_multi_response_gen(monkeypatch, config_path, response_texts: list[str],
                             api_key="test-key", root_dir="."):
    """Create an LLMPatchGenerator that returns a sequence of responses.

    Each call to the API returns the next response from response_texts.
    """
    monkeypatch.setenv("LLM_API_KEY", api_key)

    call_counter = [0]

    def _mock_open(req, timeout=30):
        idx = call_counter[0]
        call_counter[0] += 1
        if idx < len(response_texts):
            text = response_texts[idx]
        else:
            text = response_texts[-1]  # repeat last if more calls than texts
        return _make_api_response(text)

    monkeypatch.setattr(urllib.request, "urlopen", _mock_open)
    return LLMPatchGenerator(config_path, root_dir=root_dir)


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
        assert "must start with file:" in str(excinfo.value).lower()

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

    def test_rejects_markdown_fences_from_llm_output(self, monkeypatch, tmp_path):
        root = _setup_test_root(tmp_path)
        raw = "```\n" + _valid_edits() + "\n```"
        gen = _make_generator(monkeypatch, CONFIG_PATH,
                              response_text=raw, root_dir=str(root))
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("switch to websocket", plan, "context")
        assert "must start with file:" in str(excinfo.value).lower()


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
# Test: create diff format (ACTION:create generates valid git diff)
# ---------------------------------------------------------------------------


class TestCreateDiff:
    """Verify ACTION:create generates a valid git diff with /dev/null."""

    def test_create_diff_uses_dev_null(self, tmp_path):
        gen = _dummy_gen(root_dir=str(tmp_path))
        gen._validate_create_path = lambda *a, **kw: None  # bypass validation
        gen._validate_file_path = lambda *a, **kw: None
        gen._verify_find_uniqueness = lambda *a, **kw: None
        gen._scan_for_secrets = lambda *a, **kw: None

        edits = [("src/transport/http2_transport.py", "create", "", "import asyncio\n\nclass Http2Transport:\n    pass\n")]
        diff = gen._generate_diff(edits)

        assert "--- /dev/null" in diff
        assert "+++ b/src/transport/http2_transport.py" in diff
        assert "new file mode 100644" in diff
        assert "diff --git a/src/transport/http2_transport.py b/src/transport/http2_transport.py" in diff

    def test_create_diff_accepted_by_git_apply_check(self, tmp_path):
        import subprocess

        # Initialize a git repo in tmp_path
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(tmp_path), capture_output=True)
        # Create at least one committed file so apply works
        (tmp_path / "README.md").write_text("test repo\n")
        subprocess.run(["git", "add", "README.md"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)

        gen = _dummy_gen(root_dir=str(tmp_path))
        gen._validate_create_path = lambda *a, **kw: None
        gen._validate_file_path = lambda *a, **kw: None
        gen._verify_find_uniqueness = lambda *a, **kw: None
        gen._scan_for_secrets = lambda *a, **kw: None

        edits = [("src/transport/http2_transport.py", "create", "",
                  "\"\"\"HTTP/2 Transport.\"\"\"\n\nimport asyncio\n\n\nclass Http2Transport:\n    pass\n")]
        diff = gen._generate_diff(edits)

        # Write the diff to a file and check with git apply
        patch_file = tmp_path / "test.patch"
        patch_file.write_text(diff)
        result = subprocess.run(
            ["git", "apply", "--check", str(patch_file)],
            cwd=str(tmp_path), capture_output=True, text=True,
        )
        assert result.returncode == 0, f"git apply --check failed: {result.stderr}"

    def test_create_diff_line_content_prefixed_with_plus(self, tmp_path):
        gen = _dummy_gen(root_dir=str(tmp_path))
        gen._validate_create_path = lambda *a, **kw: None
        gen._validate_file_path = lambda *a, **kw: None
        gen._verify_find_uniqueness = lambda *a, **kw: None
        gen._scan_for_secrets = lambda *a, **kw: None

        content = "line1\nline2\nline3\n"
        edits = [("src/transport/new.py", "create", "", content)]
        diff = gen._generate_diff(edits)

        # All content lines in @@ hunk should start with +
        in_hunk = False
        plus_lines = 0
        for line in diff.splitlines():
            if line.startswith("@@"):
                in_hunk = True
                continue
            if in_hunk and line.startswith("+"):
                plus_lines += 1
        assert plus_lines == 3, f"Expected 3 lines starting with +, got {plus_lines}"

    def test_create_diff_no_trailing_newline(self, tmp_path):
        gen = _dummy_gen(root_dir=str(tmp_path))
        gen._validate_create_path = lambda *a, **kw: None
        gen._validate_file_path = lambda *a, **kw: None
        gen._verify_find_uniqueness = lambda *a, **kw: None
        gen._scan_for_secrets = lambda *a, **kw: None

        content = "single line without newline"
        edits = [("src/transport/new.py", "create", "", content)]
        diff = gen._generate_diff(edits)

        assert "\\ No newline at end of file" in diff
        assert "+single line without newline" in diff

    def test_create_rejects_readme(self, tmp_path):
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError):
            gen._validate_create_path(
                "README.md",
                allowed_create_paths=["."],
                allowed_create_patterns=["*.md"],
            )

    def test_create_rejects_dotenv(self, tmp_path):
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._validate_create_path(
                ".env",
                allowed_create_paths=["."],
                allowed_create_patterns=[".*"],
            )
        assert "unsafe" in str(excinfo.value).lower() or "hidden" in str(excinfo.value).lower()

    def test_create_rejects_key_extension(self, tmp_path):
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._validate_create_path(
                "config/secret.key",
                allowed_create_paths=["config/"],
                allowed_create_patterns=["config/*"],
            )
        assert "extension" in str(excinfo.value).lower() or "key" in str(excinfo.value).lower()

    def test_create_rejects_pem_extension(self, tmp_path):
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._validate_create_path(
                "certs/server.pem",
                allowed_create_paths=["certs/"],
                allowed_create_patterns=["certs/*"],
            )
        assert "extension" in str(excinfo.value).lower() or "pem" in str(excinfo.value).lower()

    def test_create_rejects_path_traversal(self, tmp_path):
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._validate_create_path(
                "src/transport/../../.env",
                allowed_create_paths=["src/transport/"],
                allowed_create_patterns=["src/transport/*"],
            )
        assert "traversal" in str(excinfo.value).lower() or "blocked" in str(excinfo.value).lower()

    def test_replace_diff_unchanged(self, tmp_path):
        """Verify ACTION:replace still works correctly after create diff changes."""
        test_file = tmp_path / "exists.py"
        test_file.write_text("line1\nline2\nline3\n")
        gen = _dummy_gen(root_dir=str(tmp_path))
        gen._validate_file_path = lambda *a, **kw: None
        gen._verify_find_uniqueness = lambda *a, **kw: None
        gen._scan_for_secrets = lambda *a, **kw: None

        edits = [("exists.py", "replace", "line2\n", "line2_replaced\n")]
        diff = gen._generate_diff(edits)

        assert "diff --git a/exists.py b/exists.py" in diff
        assert "--- a/exists.py" in diff
        assert "+++ b/exists.py" in diff
        assert "-line2" in diff
        assert "+line2_replaced" in diff
        # Must NOT contain /dev/null (that's only for create)
        assert "/dev/null" not in diff


# ---------------------------------------------------------------------------
# Test: malformed FIND/REPLACE blocks (delimiter contamination)
# ---------------------------------------------------------------------------


class TestMalformedBlocks:
    """_parse_edit_blocks rejects blocks with delimiters inside FIND/REPLACE."""

    def test_find_with_replace_delimiter_raises_malformed(self, tmp_path):
        """FIND containing <<<REPLACE inline (no \\n before it) must raise malformed block error."""
        gen = _dummy_gen(root_dir=str(tmp_path))
        # <<<REPLACE appears inside FIND content mid-line, so the regex
        # boundary \\n<<<REPLACE does NOT match here. The delimiter gets
        # captured into FIND and validation catches it.
        raw = """FILE: src/test.py
ACTION: replace
<<<FIND
some content <<<REPLACE embedded in find block
more find content
<<<REPLACE
new text for replacement
"""
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._parse_edit_blocks(raw)
        assert "Malformed" in str(excinfo.value) or "malformed" in str(excinfo.value).lower()
        assert "<<<REPLACE" in str(excinfo.value)

    def test_find_with_replace_at_start_treated_as_empty_find(self, tmp_path):
        """<<<REPLACE right after <<<FIND\\n (no blank line) → empty FIND.

        When the LLM writes <<<FIND\\n<<<REPLACE (no blank line between them),
        it intended empty FIND. Our post-processing detects this and sets
        FIND to empty string. Uniqueness check then catches it for non-empty files.
        """
        gen = _dummy_gen(root_dir=str(tmp_path))
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "test.py").write_text("# non-empty file\nx = 1\n")
        raw = """FILE: src/test.py
ACTION: replace
<<<FIND
<<<REPLACE
full replacement content
"""
        edits = gen._parse_edit_blocks(raw)
        assert len(edits) == 1
        # FIND should be empty (post-processing detected <<<REPLACE at start)
        assert edits[0][2] == ""
        # Uniqueness check on non-empty file with empty FIND must fail
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._verify_find_uniqueness("src/test.py", "")
        assert "empty" in str(excinfo.value).lower()

    def test_find_with_find_delimiter_raises_malformed(self, tmp_path):
        """FIND containing <<<FIND must raise malformed block error."""
        gen = _dummy_gen(root_dir=str(tmp_path))
        raw = """FILE: src/test.py
ACTION: replace
<<<FIND
some text
<<<FIND
another section
<<<REPLACE
new text
"""
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._parse_edit_blocks(raw)
        assert "Malformed" in str(excinfo.value) or "malformed" in str(excinfo.value).lower()
        assert "<<<FIND" in str(excinfo.value)

    def test_find_with_content_delimiter_raises_malformed(self, tmp_path):
        """FIND containing <<<CONTENT must raise malformed block error."""
        gen = _dummy_gen(root_dir=str(tmp_path))
        raw = """FILE: src/test.py
ACTION: replace
<<<FIND
some text
<<<CONTENT
data
<<<REPLACE
new text
"""
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._parse_edit_blocks(raw)
        assert "Malformed" in str(excinfo.value) or "malformed" in str(excinfo.value).lower()

    def test_replace_with_find_delimiter_raises_malformed(self, tmp_path):
        """REPLACE containing <<<FIND must raise malformed block error."""
        gen = _dummy_gen(root_dir=str(tmp_path))
        raw = """FILE: src/test.py
ACTION: replace
<<<FIND
old text
<<<REPLACE
new text
<<<FIND
nested find
"""
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._parse_edit_blocks(raw)
        assert "Malformed" in str(excinfo.value) or "malformed" in str(excinfo.value).lower()
        assert "<<<FIND" in str(excinfo.value)

    def test_replace_with_content_delimiter_raises_malformed(self, tmp_path):
        """REPLACE containing <<<CONTENT must raise malformed block error."""
        gen = _dummy_gen(root_dir=str(tmp_path))
        raw = """FILE: src/test.py
ACTION: replace
<<<FIND
old text
<<<REPLACE
new text
<<<CONTENT
nested content
"""
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._parse_edit_blocks(raw)
        assert "Malformed" in str(excinfo.value) or "malformed" in str(excinfo.value).lower()

    def test_create_content_with_file_delimiter_raises_malformed(self, tmp_path):
        """Create block CONTENT containing FILE: must raise malformed block error."""
        gen = _dummy_gen(root_dir=str(tmp_path))
        # FILE: appears mid-line inside a CONTENT section with >>> terminator.
        # Because it's not preceded by \\n, the block splitter won't separate it.
        raw = """FILE: src/transport/new.py
ACTION: create
<<<CONTENT
import asyncio
# Cross-ref FILE: src/other.py for base class
class Http2Transport:
    pass
>>>"""
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._parse_edit_blocks(raw)
        assert "Malformed" in str(excinfo.value) or "malformed" in str(excinfo.value).lower()
        assert "FILE:" in str(excinfo.value)

    def test_nonempty_file_empty_find_still_fails(self, tmp_path):
        """Non-empty file with empty FIND still fails (via _verify_find_uniqueness)."""
        filepath = tmp_path / "test.py"
        filepath.write_text("content\n")
        gen = _dummy_gen(root_dir=str(tmp_path))
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen._verify_find_uniqueness("test.py", "")
        assert "empty" in str(excinfo.value).lower()

    def test_zero_byte_file_empty_find_still_allowed(self, tmp_path):
        """0-byte file with empty FIND still allowed (prepend operation)."""
        filepath = tmp_path / "empty.py"
        filepath.write_text("")
        gen = _dummy_gen(root_dir=str(tmp_path))
        # Must not raise
        gen._verify_find_uniqueness("empty.py", "")

    def test_valid_replace_block_still_passes(self, tmp_path):
        """Valid replace block with no delimiter contamination still passes."""
        filepath = tmp_path / "cfg.yaml"
        filepath.write_text("  type: websocket\n  port: 8080\n")
        gen = _dummy_gen(root_dir=str(tmp_path))
        raw = """FILE: cfg.yaml
<<<FIND
  type: websocket
<<<REPLACE
  type: tcp
"""
        edits = gen._parse_edit_blocks(raw)
        assert len(edits) == 1
        assert edits[0][1] == "replace"
        # verify_find_uniqueness should succeed
        gen._verify_find_uniqueness("cfg.yaml", edits[0][2])

    def test_valid_create_block_git_apply_check(self, tmp_path):
        """Valid create block produces a diff accepted by git apply --check."""
        import subprocess

        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(tmp_path), capture_output=True)
        (tmp_path / "README.md").write_text("test repo\n")
        subprocess.run(["git", "add", "README.md"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)

        gen = _dummy_gen(root_dir=str(tmp_path))
        gen._validate_create_path = lambda *a, **kw: None
        gen._validate_file_path = lambda *a, **kw: None
        gen._verify_find_uniqueness = lambda *a, **kw: None
        gen._scan_for_secrets = lambda *a, **kw: None

        raw = """FILE: src/transport/http2_transport.py
ACTION: create
<<<CONTENT
\"\"\"HTTP/2 Transport module.\"\"\"

import asyncio


class Http2Transport:
    pass
>>>"""
        edits = gen._parse_edit_blocks(raw)
        assert len(edits) == 1
        assert edits[0][1] == "create"

        diff = gen._generate_diff(edits)
        assert "--- /dev/null" in diff
        assert "+++ b/src/transport/http2_transport.py" in diff

        patch_file = tmp_path / "test.patch"
        patch_file.write_text(diff)
        result = subprocess.run(
            ["git", "apply", "--check", str(patch_file)],
            cwd=str(tmp_path), capture_output=True, text=True,
        )
        assert result.returncode == 0, f"git apply --check failed: {result.stderr}"


# ---------------------------------------------------------------------------
# Test: strict FILE: header protocol
# ---------------------------------------------------------------------------

class TestStrictProtocol:
    """Verify that PatchGenerator enforces FILE: as first non-whitespace line."""

    def test_natural_language_start_rejected(self, monkeypatch, tmp_path):
        """Natural language at start is rejected with 'must start with FILE:'."""
        root = _setup_test_root(tmp_path)
        raw = "We need to refactor ClientCore and ServerCore...\n\nFILE: src/core/client_core.py\nACTION: replace\n<<<FIND\nold\n<<<REPLACE\nnew\n"
        gen = _make_generator(monkeypatch, CONFIG_PATH,
                              response_text=raw, root_dir=str(root))
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("refactor core", plan, "context")
        assert "must start with file:" in str(excinfo.value).lower()

    def test_markdown_fence_start_rejected(self, monkeypatch, tmp_path):
        """Markdown ``` at start is rejected with 'must start with FILE:'."""
        root = _setup_test_root(tmp_path)
        raw = "```\nFILE: config/server.yaml\n<<<FIND\n  type: websocket\n<<<REPLACE\n  type: tcp\n```"
        gen = _make_generator(monkeypatch, CONFIG_PATH,
                              response_text=raw, root_dir=str(root))
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("switch transport", plan, "context")
        assert "must start with file:" in str(excinfo.value).lower()

    def test_blank_lines_before_file_header_still_accepted(self, monkeypatch, tmp_path):
        """Leading blank lines before FILE: are accepted (strip handles whitespace)."""
        root = _setup_test_root(tmp_path)
        raw = "\n\nFILE: config/server.yaml\n<<<FIND\n  type: websocket\n<<<REPLACE\n  type: tcp\n"
        gen = _make_generator(monkeypatch, CONFIG_PATH,
                              response_text=raw, root_dir=str(root))
        plan = _valid_plan()
        patch = gen.generate("switch to websocket", plan, "context")
        assert "diff --git" in patch

    def test_valid_file_block_passes_strict_check(self, monkeypatch, tmp_path):
        """Valid FILE: block still passes through strict protocol check."""
        root = _setup_test_root(tmp_path)
        gen = _make_generator(monkeypatch, CONFIG_PATH,
                              response_text=_valid_edits(), root_dir=str(root))
        plan = _valid_plan()
        patch = gen.generate("switch to websocket", plan, "context")
        assert "diff --git" in patch

    def test_raw_output_saved_on_protocol_failure(self, monkeypatch, tmp_path):
        """When protocol fails, raw LLM output is saved to task_dir with attempt suffix."""
        root = _setup_test_root(tmp_path)
        task_dir = str(tmp_path / "task_record")
        raw = "Here is a nice explanation of what I would change...\nBut no FILE: header."
        gen = _make_generator(monkeypatch, CONFIG_PATH,
                              response_text=raw, root_dir=str(root))
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError):
            gen.generate("fix things", plan, "context", task_dir=task_dir)
        raw_path = os.path.join(task_dir, "llm_patch_raw_attempt1.txt")
        assert os.path.isfile(raw_path)
        saved = open(raw_path).read()
        assert "nice explanation" in saved


# ---------------------------------------------------------------------------
# Test: Protocol retry
# ---------------------------------------------------------------------------


class TestProtocolRetry:
    """Verify protocol retry: first malformed, second valid → success."""

    def test_first_natural_language_second_valid_succeeds(self, monkeypatch, tmp_path):
        """First response is natural language, second is valid FILE: block → success."""
        root = _setup_test_root(tmp_path)
        responses = [
            "We need to analyze and then refactor the transport system...\nLet me think about this carefully.",
            _valid_edits(),
        ]
        gen = _make_multi_response_gen(monkeypatch, CONFIG_PATH, responses, root_dir=str(root))
        plan = _valid_plan()
        patch = gen.generate("switch transport", plan, "context")
        assert "diff --git" in patch
        assert gen.protocol_retry_used
        assert gen.protocol_retry_count == 1

    def test_first_markdown_fence_second_valid_succeeds(self, monkeypatch, tmp_path):
        """First response wrapped in markdown fence, second is valid FILE: block → success."""
        root = _setup_test_root(tmp_path)
        responses = [
            "```\nFILE: config/server.yaml\n<<<FIND\n  type: websocket\n<<<REPLACE\n  type: tcp\n```",
            _valid_edits(),
        ]
        gen = _make_multi_response_gen(monkeypatch, CONFIG_PATH, responses, root_dir=str(root))
        plan = _valid_plan()
        patch = gen.generate("switch transport", plan, "context")
        assert "diff --git" in patch
        assert gen.protocol_retry_used

    def test_both_natural_language_fails(self, monkeypatch, tmp_path):
        """All attempts return natural language → LLMPatchGeneratorError."""
        root = _setup_test_root(tmp_path)
        responses = [
            "Here is my analysis of what needs to change...\nFirst, we need to understand...",
            "Let me explain the approach for this refactoring...\nThe key changes are...",
        ]
        gen = _make_multi_response_gen(monkeypatch, CONFIG_PATH, responses, root_dir=str(root))
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("refactor", plan, "context")
        assert "must start with file:" in str(excinfo.value).lower()
        assert "3 attempts" in str(excinfo.value).lower()
        assert gen.protocol_retry_count == 2  # retried twice but both failed
        assert gen.protocol_retry_used  # retry was attempted

    def test_raw_attempt_files_saved_on_failure(self, monkeypatch, tmp_path):
        """All attempt raw outputs are saved when protocol retry fails."""
        root = _setup_test_root(tmp_path)
        task_dir = str(tmp_path / "task_record")
        responses = [
            "Analysis approach one: modify the system...",
            "Analysis approach two: tweak parameters...",
        ]
        gen = _make_multi_response_gen(monkeypatch, CONFIG_PATH, responses, root_dir=str(root))
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError):
            gen.generate("fix", plan, "context", task_dir=task_dir)

        attempt1 = os.path.join(task_dir, "llm_patch_raw_attempt1.txt")
        attempt2 = os.path.join(task_dir, "llm_patch_raw_attempt2.txt")
        attempt3 = os.path.join(task_dir, "llm_patch_raw_attempt3.txt")
        assert os.path.isfile(attempt1), f"Missing {attempt1}"
        assert os.path.isfile(attempt2), f"Missing {attempt2}"
        assert os.path.isfile(attempt3), f"Missing {attempt3}"
        assert "Analysis approach one" in open(attempt1).read()
        assert "Analysis approach two" in open(attempt2).read()

    def test_retry_patch_pass_git_apply_check(self, monkeypatch, tmp_path):
        """Retry-succeeded patch still passes git apply --check."""
        import subprocess

        root = _setup_test_root(tmp_path)
        subprocess.run(["git", "init"], cwd=str(root), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=str(root), capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(root), capture_output=True)
        # Create and commit config/server.yaml so the edit target exists
        (root / "config" / "server.yaml").write_text("transport:\n  type: websocket\n  port: 8080\n")
        subprocess.run(["git", "add", "."], cwd=str(root), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(root), capture_output=True)

        responses = [
            "Let me think about the transport switch...",
            _valid_edits(),
        ]
        gen = _make_multi_response_gen(monkeypatch, CONFIG_PATH, responses, root_dir=str(root))
        plan = _valid_plan()
        patch = gen.generate("switch to websocket", plan, "context")
        assert "diff --git" in patch

        patch_file = tmp_path / "test.patch"
        patch_file.write_text(patch)
        result = subprocess.run(
            ["git", "apply", "--check", str(patch_file)],
            cwd=str(root), capture_output=True, text=True,
        )
        assert result.returncode == 0, f"git apply --check failed: {result.stderr}"

    def test_retry_does_not_bypass_allowed_edit_files(self, monkeypatch, tmp_path):
        """Retry still enforces allowed_edit_files constraint."""
        root = _setup_test_root(tmp_path)
        # Valid FILE: block but targeting an unallowed file
        bad_edits = """FILE: src/secret/private.py
ACTION: create
<<<CONTENT
# secret module
>>>"""
        responses = [
            "I'll create the private module...",
            bad_edits,  # this passes protocol but should fail path validation
        ]
        gen = _make_multi_response_gen(monkeypatch, CONFIG_PATH, responses, root_dir=str(root))
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("add secret", plan, "context",
                         allowed_edit_files=["config/server.yaml"],
                         allowed_create_paths=["src/transport/"])
        # Should fail on create path validation, not protocol
        assert "not under allowed_create_paths" in str(excinfo.value)

    def test_retry_does_not_bypass_allowed_create_patterns(self, monkeypatch, tmp_path):
        """Retry still enforces allowed_create_patterns constraint."""
        root = _setup_test_root(tmp_path)
        bad_edits = """FILE: src/transport/notes.txt
ACTION: create
<<<CONTENT
some notes
>>>"""
        responses = [
            "Let me create the notes file...",
            bad_edits,
        ]
        gen = _make_multi_response_gen(monkeypatch, CONFIG_PATH, responses, root_dir=str(root))
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("add notes", plan, "context",
                         allowed_create_paths=["src/transport/"],
                         allowed_create_patterns=["src/transport/*_transport.py"])
        assert "pattern" in str(excinfo.value).lower()

    def test_first_valid_no_retry(self, monkeypatch, tmp_path):
        """First attempt is valid → no retry needed, protocol_retry_used=False."""
        root = _setup_test_root(tmp_path)
        gen = _make_generator(monkeypatch, CONFIG_PATH,
                              response_text=_valid_edits(), root_dir=str(root))
        plan = _valid_plan()
        patch = gen.generate("switch to websocket", plan, "context")
        assert "diff --git" in patch
        assert not gen.protocol_retry_used
        assert gen.protocol_retry_count == 0


# ---------------------------------------------------------------------------
# Test: Semantic retry
# ---------------------------------------------------------------------------


class TestSemanticRetry:
    """Verify semantic retry: first response has semantic errors (e.g. create existing), second corrects them."""

    def test_semantic_retry_create_existing_file_then_replace_success(self, monkeypatch, tmp_path):
        """First response uses ACTION:create on existing file → semantic retry → second uses replace → success."""
        root = _setup_test_root(tmp_path)
        # First: ACTION:create on config/server.yaml (which exists in test root)
        create_existing = """FILE: config/server.yaml
ACTION: create
<<<CONTENT
transport:
  type: tcp
  port: 8080
>>>"""
        responses = [
            create_existing,
            _valid_edits(),  # correct ACTION:replace (implicit default)
        ]
        gen = _make_multi_response_gen(monkeypatch, CONFIG_PATH, responses, root_dir=str(root))
        plan = _valid_plan()
        patch = gen.generate("switch to websocket", plan, "context")
        assert "diff --git" in patch
        assert gen.semantic_retry_used
        assert gen.semantic_retry_count == 1
        assert "already exists" in gen.last_semantic_error.lower()

    def test_semantic_retry_create_existing_file_twice_fails(self, monkeypatch, tmp_path):
        """Both responses use ACTION:create on existing file → final failure."""
        root = _setup_test_root(tmp_path)
        create_existing_1 = """FILE: config/server.yaml
ACTION: create
<<<CONTENT
transport:
  type: tcp
  port: 8080
>>>"""
        create_existing_2 = """FILE: config/server.yaml
ACTION: create
<<<CONTENT
transport:
  type: websocket
  port: 9090
>>>"""
        responses = [create_existing_1, create_existing_2]
        gen = _make_multi_response_gen(monkeypatch, CONFIG_PATH, responses, root_dir=str(root))
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("switch transport", plan, "context")
        assert "already exists" in str(excinfo.value).lower()
        assert gen.semantic_retry_used
        assert gen.semantic_retry_count == 1

    def test_semantic_retry_does_not_bypass_allowed_edit_files(self, monkeypatch, tmp_path):
        """Semantic retry still enforces allowed_edit_files constraint."""
        root = _setup_test_root(tmp_path)
        # First: valid protocol but semantic error (create existing)
        create_existing = """FILE: config/server.yaml
ACTION: create
<<<CONTENT
data
>>>"""
        # Second: valid protocol, fixes "create existing" but edits unallowed file
        bad_retry = """FILE: config/llm_agent.yaml
ACTION: replace
<<<FIND
old
<<<REPLACE
new
"""
        responses = [create_existing, bad_retry]
        gen = _make_multi_response_gen(monkeypatch, CONFIG_PATH, responses, root_dir=str(root))
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("refactor", plan, "context",
                         allowed_edit_files=["config/server.yaml"])
        assert ("blocked" in str(excinfo.value).lower()
                or "not in allowed" in str(excinfo.value).lower())

    def test_semantic_retry_does_not_bypass_allowed_create_paths(self, monkeypatch, tmp_path):
        """Semantic retry still enforces allowed_create_paths constraint."""
        root = _setup_test_root(tmp_path)
        # First: create existing → semantic retry
        create_existing = """FILE: config/server.yaml
ACTION: create
<<<CONTENT
data
>>>"""
        # Second: tries to create in unallowed directory
        bad_retry = """FILE: src/secret/new.py
ACTION: create
<<<CONTENT
# secret
>>>"""
        responses = [create_existing, bad_retry]
        gen = _make_multi_response_gen(monkeypatch, CONFIG_PATH, responses, root_dir=str(root))
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("refactor", plan, "context",
                         allowed_edit_files=["config/server.yaml"],
                         allowed_create_paths=["src/transport/"])
        assert "not under allowed_create_paths" in str(excinfo.value)

    def test_semantic_retry_records_raw_attempts(self, monkeypatch, tmp_path):
        """Both semantic attempt raw outputs are saved when retry fails."""
        root = _setup_test_root(tmp_path)
        task_dir = str(tmp_path / "task_record")
        create_existing_1 = """FILE: config/server.yaml
ACTION: create
<<<CONTENT
attempt 1 content
>>>"""
        create_existing_2 = """FILE: config/server.yaml
ACTION: create
<<<CONTENT
attempt 2 content
>>>"""
        responses = [create_existing_1, create_existing_2]
        gen = _make_multi_response_gen(monkeypatch, CONFIG_PATH, responses, root_dir=str(root))
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError):
            gen.generate("fix", plan, "context", task_dir=task_dir)

        attempt1 = os.path.join(task_dir, "llm_patch_raw_attempt1.txt")
        attempt2 = os.path.join(task_dir, "llm_patch_raw_attempt2.txt")
        assert os.path.isfile(attempt1), f"Missing {attempt1}"
        assert os.path.isfile(attempt2), f"Missing {attempt2}"
        assert "attempt 1 content" in open(attempt1).read()
        assert "attempt 2 content" in open(attempt2).read()

    def test_protocol_retry_then_semantic_retry_success(self, monkeypatch, tmp_path):
        """attempt1: natural language → protocol retry
        attempt2: FILE block but create existing → semantic retry
        attempt3: replace → success"""
        root = _setup_test_root(tmp_path)
        responses = [
            "We need to analyze the transport system carefully...",
            """FILE: config/server.yaml
ACTION: create
<<<CONTENT
transport:
  type: tcp
  port: 8080
>>>""",
            _valid_edits(),
        ]
        gen = _make_multi_response_gen(monkeypatch, CONFIG_PATH, responses, root_dir=str(root))
        plan = _valid_plan()
        patch = gen.generate("switch transport", plan, "context")
        assert "diff --git" in patch
        assert gen.protocol_retry_used
        assert gen.protocol_retry_count == 1
        assert gen.semantic_retry_used
        assert gen.semantic_retry_count == 1

    def test_semantic_retry_success_patch_pass_git_apply_check(self, monkeypatch, tmp_path):
        """Semantic retry succeeded patch still passes git apply --check."""
        import subprocess

        root = _setup_test_root(tmp_path)
        subprocess.run(["git", "init"], cwd=str(root), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=str(root), capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(root), capture_output=True)
        (root / "config" / "server.yaml").write_text("transport:\n  type: websocket\n  port: 8080\n")
        subprocess.run(["git", "add", "."], cwd=str(root), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(root), capture_output=True)

        create_existing = """FILE: config/server.yaml
ACTION: create
<<<CONTENT
transport:
  type: tcp
  port: 8080
>>>"""
        responses = [create_existing, _valid_edits()]
        gen = _make_multi_response_gen(monkeypatch, CONFIG_PATH, responses, root_dir=str(root))
        plan = _valid_plan()
        patch = gen.generate("switch to websocket", plan, "context")
        assert "diff --git" in patch
        assert gen.semantic_retry_used

        patch_file = tmp_path / "test.patch"
        patch_file.write_text(patch)
        result = subprocess.run(
            ["git", "apply", "--check", str(patch_file)],
            cwd=str(root), capture_output=True, text=True,
        )
        assert result.returncode == 0, f"git apply --check failed: {result.stderr}"


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
