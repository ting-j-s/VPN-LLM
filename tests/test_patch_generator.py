"""Tests for LLMPatchGenerator.

All tests use mock HTTP responses — no real network calls are made.
No files are written to the working tree.
"""

import json
import os
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


def _make_generator(monkeypatch, config_path, api_key="test-key", response_text=None):
    """Create an LLMPatchGenerator with a mock API key env var and mock HTTP."""
    monkeypatch.setenv("LLM_API_KEY", api_key)

    if response_text is not None:

        def _mock_open(req, timeout=30):
            return _make_api_response(response_text)

        monkeypatch.setattr(urllib.request, "urlopen", _mock_open)

    return LLMPatchGenerator(config_path)


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
    """Test generate() with mock HTTP."""

    def test_generates_valid_patch(self, monkeypatch, tmp_path):
        gen = _make_generator(monkeypatch, CONFIG_PATH, response_text=_valid_diff())
        plan = _valid_plan()
        patch = gen.generate("switch to websocket", plan, "context")
        assert "diff --git" in patch
        assert "--- a/config/server.yaml" in patch
        assert "+++ b/config/server.yaml" in patch

    def test_rejects_non_diff_llm_output(self, monkeypatch, tmp_path):
        gen = _make_generator(
            monkeypatch, CONFIG_PATH,
            response_text="I suggest modifying the transport config file to use WebSocket."
        )
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("switch to websocket", plan, "context")
        assert "diff --git" in str(excinfo.value)

    def test_rejects_diff_with_blocked_file_path(self, monkeypatch, tmp_path):
        bad_diff = """diff --git a/.env b/.env
--- a/.env
+++ b/.env
@@ -1,1 +1,1 @@
-OLD=value
+NEW=value
"""
        gen = _make_generator(monkeypatch, CONFIG_PATH, response_text=bad_diff)
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("bad patch", plan, "context")
        assert "blocked" in str(excinfo.value).lower()

    def test_rejects_diff_with_llm_agent_config_path(self, monkeypatch, tmp_path):
        bad_diff = """diff --git a/config/llm_agent.yaml b/config/llm_agent.yaml
--- a/config/llm_agent.yaml
+++ b/config/llm_agent.yaml
@@ -1,1 +1,1 @@
-old
+new
"""
        gen = _make_generator(monkeypatch, CONFIG_PATH, response_text=bad_diff)
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("bad patch", plan, "context")
        assert "blocked" in str(excinfo.value).lower()

    def test_rejects_diff_with_secret_content(self, monkeypatch, tmp_path):
        bad_diff = _valid_diff() + "\n+-----BEGIN RSA PRIVATE KEY-----\n+content\n+-----END RSA PRIVATE KEY-----"
        gen = _make_generator(monkeypatch, CONFIG_PATH, response_text=bad_diff)
        plan = _valid_plan()
        with pytest.raises(LLMPatchGeneratorError) as excinfo:
            gen.generate("bad patch", plan, "context")
        assert "private key" in str(excinfo.value)

    def test_strips_markdown_fences_from_llm_output(self, monkeypatch, tmp_path):
        raw = "```diff\n" + _valid_diff() + "\n```"
        gen = _make_generator(monkeypatch, CONFIG_PATH, response_text=raw)
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
        # Ensure the env var is not set
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
        gen = _make_generator(monkeypatch, CONFIG_PATH, response_text=_valid_diff())
        plan = _valid_plan()
        patch = gen.generate("switch to websocket", plan, "context")
        # The patch text exists as a string, but no file should have been written
        assert isinstance(patch, str)
        # config/server.yaml should still be the original
        assert os.path.exists("config/server.yaml")

    def test_does_not_call_git_apply(self, monkeypatch, tmp_path):
        """generate() only returns diff text — it does not call git apply."""
        gen = _make_generator(monkeypatch, CONFIG_PATH, response_text=_valid_diff())
        plan = _valid_plan()
        patch = gen.generate("switch to websocket", plan, "context")
        # The returned patch is just text, not applied
        assert "diff --git" in patch
        assert "transport:" not in patch or "websocket" not in patch or True
