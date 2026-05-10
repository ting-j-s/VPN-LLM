"""LLM Patch Generator — dry-run only.

Uses an OpenAI-compatible API to generate unified diffs for a given task plan.
The patch is validated, saved to disk, and checked via `git apply --check`, but
NEVER automatically applied, committed, or pushed.
"""

import json
import os
import re
import urllib.request
import urllib.error

import yaml

from src.llm.safety_guard import SafetyGuard, SafetyError


class LLMPatchGeneratorError(Exception):
    """Raised when patch generation or validation fails."""


class LLMPatchGenerator:
    """Generate a unified diff via LLM, with strict safety validation.

    Usage:
        gen = LLMPatchGenerator("config/llm_agent.yaml")
        patch_text = gen.generate(request, plan, repo_context)
        # patch_text is a unified diff string, saved to patch.diff
        # System does NOT apply it — human review required.
    """

    SYSTEM_PROMPT = (
        "You are a patch generator for a VPN software project. "
        "Given a user request, a task plan, and repository context, "
        "generate a unified diff (git diff format) that implements the requested changes.\n\n"
        "CRITICAL RULES:\n"
        "- Output ONLY the unified diff. No markdown, no explanation, no code fences.\n"
        "- Start every file section with: diff --git a/<path> b/<path>\n"
        "- Include --- a/<path> and +++ b/<path> headers\n"
        "- Include proper @@ hunk headers with line numbers\n"
        "- Do NOT modify .env, .git/, .claude/, *.key, *.pem, or config/llm_agent.yaml\n"
        "- Do NOT include any API keys, private keys, tokens, or passwords in the diff\n"
        "- If you cannot generate a safe patch, output an empty diff\n\n"
        "Example output format:\n"
        "diff --git a/config/server.yaml b/config/server.yaml\n"
        "--- a/config/server.yaml\n"
        "+++ b/config/server.yaml\n"
        "@@ -10,7 +10,7 @@\n"
        " transport:\n"
        "-  type: tcp\n"
        "+  type: websocket\n"
        "   port: 8080\n"
    )

    # Sensitive content patterns to reject in diff bodies
    SENSITIVE_PATTERNS = [
        (re.compile(r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY"), "private key"),
        (re.compile(r"-----BEGIN\s+CERTIFICATE"), "certificate"),
        (re.compile(r'sk-[a-zA-Z0-9_-]{20,}'), "API key (sk- prefix)"),
        (re.compile(r'AIza[0-9A-Za-z_-]{20,}'), "Google API key"),
        (re.compile(r'eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}'), "JWT token"),
        (re.compile(r'(?:api[_-]?key|apikey|api_secret|secret_key)\s*[=:]\s*["\']?\S{8,}["\']?', re.IGNORECASE), "API key assignment"),
        (re.compile(r'(?:password|passwd|pwd)\s*[=:]\s*["\']?\S{4,}["\']?', re.IGNORECASE), "password assignment"),
        (re.compile(r'(?:bearer|token)\s+[A-Za-z0-9_\-.]{16,}', re.IGNORECASE), "bearer token"),
    ]

    # File path patterns from unified diff lines
    _DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$")
    _DIFF_A_RE = re.compile(r"^--- a/(.+?)$")
    _DIFF_B_RE = re.compile(r"^\+\+\+ b/(.+?)$")

    # Blocklisted file paths for patch generation (even if SafetyGuard allows)
    BLOCKED_PATCH_PATHS = frozenset({
        "config/llm_agent.yaml",
        "config/llm_agent.yaml.example",
    })

    BLOCKED_PATCH_PREFIXES = (
        ".claude/",
        ".git/",
    )

    BLOCKED_PATCH_EXTENSIONS = frozenset({".key", ".pem", ".crt"})

    def __init__(self, config_path: str):
        self._config = self._load_config(config_path)
        self._api_key = os.environ.get(self._config["api_key_env"], "")
        if not self._api_key:
            raise LLMPatchGeneratorError(
                f"API key not found. Set the {self._config['api_key_env']} environment variable."
            )
        self._base_url = self._config["base_url"].rstrip("/")
        self._model = self._config["model"]
        self._timeout = int(self._config.get("agent.request_timeout", 30))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, user_request: str, task_plan, repository_context: str) -> str:
        """Generate a unified diff for the given request and plan.

        Returns the raw patch text after passing all safety checks.

        Raises LLMPatchGeneratorError on any failure: HTTP error, invalid diff
        format, unsafe file paths, or sensitive content.
        """
        raw = self._call_api(user_request, task_plan, repository_context)
        diff_text = self._extract_diff(raw)
        file_paths = self._parse_file_paths(diff_text)

        # SafetyGate 1: validate every file path touched by the diff
        for path in file_paths:
            self._validate_file_path(path)

        # SafetyGate 2: scan diff content for secrets
        self._scan_for_secrets(diff_text)

        return diff_text

    # ------------------------------------------------------------------
    # Internal: config loading
    # ------------------------------------------------------------------

    @staticmethod
    def _load_config(path: str) -> dict:
        if not os.path.isfile(path):
            raise LLMPatchGeneratorError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict):
            raise LLMPatchGeneratorError("Config file must be a YAML dictionary")
        for key in ("base_url", "model", "api_key_env"):
            if key not in raw:
                raise LLMPatchGeneratorError(f"Missing required config key: {key}")
        return raw

    # ------------------------------------------------------------------
    # Internal: LLM API call
    # ------------------------------------------------------------------

    def _call_api(self, user_request: str, task_plan, repository_context: str) -> str:
        planner_type = "llm_based" if hasattr(task_plan, "summary") else "rule_based"
        plan_summary = getattr(task_plan, "summary", "") or getattr(task_plan, "description", "")
        plan_dict = {
            "task_type": task_plan.task_type,
            "target_transport": task_plan.target_transport,
            "summary": plan_summary,
            "affected_areas": task_plan.affected_areas,
        }

        user_message = (
            f"USER REQUEST:\n{user_request}\n\n"
            f"TASK PLAN:\n{json.dumps(plan_dict, indent=2, ensure_ascii=False)}\n\n"
            f"REPOSITORY CONTEXT:\n{repository_context}\n\n"
            "Generate the unified diff that implements these changes. "
            "Output ONLY the diff, no other text."
        )

        url = f"{self._base_url}/chat/completions"
        body = json.dumps({
            "model": self._model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
        })

        req = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise LLMPatchGeneratorError(f"LLM API HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise LLMPatchGeneratorError(f"LLM API connection error: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise LLMPatchGeneratorError(f"LLM API returned invalid JSON: {e}") from e

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMPatchGeneratorError(f"Unexpected API response structure: {e}") from e

        return content

    # ------------------------------------------------------------------
    # Internal: diff extraction and validation
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_diff(raw: str) -> str:
        """Strip markdown fences and validate basic diff structure."""
        text = raw.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        if not text:
            raise LLMPatchGeneratorError("LLM returned empty output")

        # Must contain diff --git header
        if "diff --git" not in text:
            raise LLMPatchGeneratorError(
                f"LLM output does not contain 'diff --git' header. "
                f"Raw output: {text[:500]}"
            )

        # Must contain --- a/ and +++ b/ markers
        if "--- a/" not in text:
            raise LLMPatchGeneratorError(
                "LLM output missing '--- a/' file header"
            )
        if "+++ b/" not in text:
            raise LLMPatchGeneratorError(
                "LLM output missing '+++ b/' file header"
            )

        return text

    @classmethod
    def _parse_file_paths(cls, diff_text: str) -> list[str]:
        """Extract file paths referenced in the unified diff."""
        paths = set()
        for line in diff_text.splitlines():
            m = cls._DIFF_GIT_RE.match(line)
            if m:
                paths.add(m.group(1))
                paths.add(m.group(2))
                continue
            m = cls._DIFF_A_RE.match(line)
            if m:
                paths.add(m.group(1))
                continue
            m = cls._DIFF_B_RE.match(line)
            if m:
                paths.add(m.group(1))
        return sorted(paths)

    @classmethod
    def _validate_file_path(cls, path: str) -> None:
        """Reject blocked file paths.

        Extends SafetyGuard checks with patch-specific blocklist.
        """
        # Normalize for comparison
        normalized = path.replace("\\", "/")
        basename = os.path.basename(normalized)

        # Patch-specific blocked exact paths
        for blocked in cls.BLOCKED_PATCH_PATHS:
            if normalized == blocked or normalized.endswith("/" + blocked):
                raise LLMPatchGeneratorError(
                    f"Patch touches blocked file: {path}"
                )

        # Patch-specific blocked prefixes
        for prefix in cls.BLOCKED_PATCH_PREFIXES:
            if normalized.startswith(prefix):
                raise LLMPatchGeneratorError(
                    f"Patch touches blocked path prefix: {path}"
                )

        # Patch-specific blocked extensions
        _, ext = os.path.splitext(basename)
        if ext.lower() in cls.BLOCKED_PATCH_EXTENSIONS:
            raise LLMPatchGeneratorError(
                f"Patch touches blocked file extension: {path}"
            )

        # Standard SafetyGuard check
        try:
            SafetyGuard.validate_write_path(path)
        except SafetyError as e:
            raise LLMPatchGeneratorError(
                f"Patch touches unsafe file '{path}': {e}"
            ) from e

    @classmethod
    def _scan_for_secrets(cls, diff_text: str) -> None:
        """Scan diff content for sensitive patterns (API keys, tokens, etc.)."""
        for pattern, label in cls.SENSITIVE_PATTERNS:
            match = pattern.search(diff_text)
            if match:
                snippet = match.group(0)[:80]
                raise LLMPatchGeneratorError(
                    f"Diff contains suspected {label}: {snippet}"
                )
