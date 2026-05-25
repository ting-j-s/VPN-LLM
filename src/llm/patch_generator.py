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
from dataclasses import dataclass, field

import yaml

from src.llm.safety_guard import SafetyGuard, SafetyError


def _verbose_print(msg: str) -> None:
    """Print verbose output to stderr."""
    import sys
    print(msg, file=sys.stderr, flush=True)


class LLMPatchGeneratorError(Exception):
    """Raised when patch generation or validation fails."""


def _build_expected_artifacts_guidance(user_request: str, task_plan) -> str:
    """Build guidance about expected artifacts for transport_addition tasks.

    When the user request explicitly mentions tests, docs, or config support,
    the LLM must be reminded to generate those file types.
    """
    import re

    task_type = task_plan.task_type if hasattr(task_plan, "task_type") else ""
    if task_type != "transport_addition":
        return ""

    request_lower = user_request.lower()
    has_tests = any(kw in request_lower for kw in ("test", "tests"))
    has_docs = any(kw in request_lower for kw in ("doc", "docs", "documentation"))
    has_config = any(kw in request_lower for kw in ("config", "configuration"))

    if not (has_tests or has_docs or has_config):
        return ""

    # Detect transport name
    transport_name = None
    m = re.search(
        r'(?:add|new|create|implement)\s+(?:a\s+)?(?:new\s+)?(\w+)\s+transport',
        request_lower,
    )
    if m:
        transport_name = m.group(1)

    lines = ["\nEXPECTED ARTIFACTS (you MUST generate ALL of these):"]

    if transport_name:
        lines.append(
            f"  - src/transport/{transport_name}_transport.py "
            f"(transport implementation — MUST be created)"
        )
    else:
        lines.append(
            "  - src/transport/<name>_transport.py "
            "(transport implementation — MUST be created)"
        )

    if has_tests:
        if transport_name:
            lines.append(
                f"  - tests/test_{transport_name}_transport.py "
                f"(unit tests — MUST be created)"
            )
        else:
            lines.append(
                "  - tests/test_<name>_transport.py "
                "(unit tests — MUST be created)"
            )

    if has_docs:
        if transport_name:
            lines.append(
                f"  - docs/{transport_name}_transport.md "
                f"(documentation — MUST be created)"
            )
        else:
            lines.append(
                "  - docs/<name>_transport.md "
                "(documentation — MUST be created)"
            )

    if has_config:
        if transport_name:
            lines.append(
                f"  - config/{transport_name}_transport.yaml "
                f"(configuration example — MUST be created)"
            )
        else:
            lines.append(
                "  - config/<name>_transport.yaml "
                "(configuration example — MUST be created)"
            )
        lines.append(
            "  - src/common/config.py or src/transport/__init__.py "
            "(register the new transport — MUST edit if registration needed)"
        )

    lines.append(
        "  - src/transport/__init__.py or src/transport/factory.py "
        "(register the new transport — MUST edit if registration needed)"
    )
    lines.append(
        "\nIMPORTANT: Use ACTION: create for NEW files and ACTION: replace "
        "for EXISTING files. All expected artifacts listed above MUST appear "
        "in your output. Missing any of them will cause the patch to be "
        "incomplete."
    )

    return "\n".join(lines)


def _check_delimiter_leakage(filepath: str, find_str: str, content: str,
                              action: str) -> None:
    """Check that FIND/REPLACE delimiter lines don't leak into file content.

    The LLM must not write bare <<< or >>> lines into file content —
    those are reserved for FIND/REPLACE block delimiters.
    """
    for line_no, line in enumerate(content.split("\n"), 1):
        stripped = line.strip()
        if stripped in ("<<<", ">>>", "<<<FIND", "<<<REPLACE", "<<<CONTENT"):
            raise LLMPatchGeneratorError(
                f"FIND/REPLACE delimiter leakage in {filepath} line {line_no}: "
                f"'{stripped}' found in {action} content. "
                "The LLM wrote a FIND/REPLACE delimiter into the file content. "
                "Remove these lines from the generated code."
            )


def _build_module_contract_prompt_section(resolution, transport_name: str = "",
                                         stage_name: str = "",
                                         detected_metrics: list[str] | None = None) -> str:
    """Build a prompt section describing the module contract constraints.

    Injected into the LLM prompt so the model understands which files it
    may/must/forbidden edit, and what evidence is required.

    Phase LLM-M2: Also injects the PATCH BLUEPRINT section when a blueprint
    exists for the selected module.
    """
    if resolution is None:
        return ""

    lines = [
        "TASK MODULE CONTRACT",
        f"Module: {resolution.selected_module}",
        "",
    ]

    if resolution.allowed_files:
        lines.append("ALLOWED files (you MAY edit):")
        for f in resolution.allowed_files:
            resolved = f.replace("{name}", transport_name) if transport_name else f
            lines.append(f"  - {resolved}")
        lines.append("")

    if resolution.required_files:
        lines.append("REQUIRED files (you MUST include):")
        for f in resolution.required_files:
            resolved = f.replace("{name}", transport_name) if transport_name else f
            lines.append(f"  - {resolved}")
        lines.append("")

    if resolution.forbidden_files:
        lines.append("FORBIDDEN files (DO NOT create or edit):")
        for f in resolution.forbidden_files:
            resolved = f.replace("{name}", transport_name) if transport_name else f
            lines.append(f"  - {resolved}")
        lines.append("")

    if resolution.required_evidence:
        lines.append("REQUIRED EVIDENCE (must be present in patch):")
        for ev in resolution.required_evidence:
            lines.append(f"  - {ev}")
        lines.append("")

    if resolution.warnings:
        lines.append("WARNINGS:")
        for w in resolution.warnings:
            lines.append(f"  - {w}")
        lines.append("")

    # Add special constraint text based on module
    if resolution.selected_module == "transport_runtime":
        lines.append(
            "Upgrade the existing skeleton transport file with real runtime "
            "implementation. Do NOT create a new separate bypass file "
            "(e.g. {name}_full_transport.py, {name}_new_transport.py)."
        )
        lines.append("Tunnel smoke is mandatory for this task type.")
    elif resolution.selected_module == "docs_only":
        lines.append("ONLY edit documentation files (*.md, docs/).")
        lines.append("Do NOT change src/ or tests/ files.")
    elif resolution.selected_module == "detection_countermeasure":
        lines.append("Implement countermeasure behind config flags (default OFF).")
        lines.append("Do NOT delete or disable detectors. Do NOT lower detection thresholds.")
        lines.append("Provide before/after metric evidence.")

    # ---- No-Delete Policy (Phase LLM-M1D) ----
    from src.llm.task_modules import get_module_contract
    contract = get_module_contract(resolution.selected_module)
    if contract is not None and not contract.deletion_allowed:
        no_del = contract.build_no_delete_policy_text()
        if no_del:
            lines.append("")
            lines.append(no_del)

    # ---- Config-Driven Change Policy (Phase LLM-M1D) ----
    if contract is not None and contract.config_driven_change_required:
        cfg = contract.build_config_driven_policy_text()
        if cfg:
            lines.append("")
            lines.append(cfg)

    # ---- Patch Blueprint (Phase LLM-M2) ----
    from src.llm.patch_blueprints import get_blueprint
    blueprint = get_blueprint(resolution.selected_module)
    if blueprint is not None:
        bp_section = blueprint.build_prompt_section(
            stage_name=stage_name,
            target_transport=transport_name,
            detected_metrics=detected_metrics,
        )
        if bp_section:
            lines.append("")
            lines.append(bp_section)

    return "\n".join(lines)


class LLMPatchGenerator:
    """Generate a unified diff via LLM, with strict safety validation.

    Usage:
        gen = LLMPatchGenerator("config/llm_agent.yaml")
        patch_text = gen.generate(request, plan, repo_context)
        # patch_text is a unified diff string, saved to patch.diff
        # System does NOT apply it — human review required.

    Protocol retry:
        If the LLM response does not start with FILE:, the generator
        sends a correction prompt and retries once (max 2 attempts).
        Both raw outputs are saved for debugging. If the retry also
        fails, LLMPatchGeneratorError is raised.

    Semantic retry:
        If the response starts with FILE: (protocol OK) but fails semantic
        validation (e.g., ACTION:create on existing file, path violations),
        the generator sends a correction prompt and retries once.
        Total LLM attempts: max 3 (1 protocol retry + 1 semantic retry).
        Semantic retry does NOT bypass safety constraints (allowed_edit_files,
        allowed_create_paths, etc.).
    """

    _MAX_PROTOCOL_ATTEMPTS = 2   # total attempts for protocol correctness
    _MAX_SEMANTIC_ATTEMPTS = 1  # additional attempts for semantic validity
    _MAX_TOTAL_ATTEMPTS = _MAX_PROTOCOL_ATTEMPTS + _MAX_SEMANTIC_ATTEMPTS

    SYSTEM_PROMPT = (
        "You are a patch generator for a VPN software project. "
        "Given a user request, a task plan, and repository context, "
        "output edit instructions in FIND/REPLACE format.\n\n"
        "FORMAT for editing EXISTING files (USE THIS for files that already exist):\n"
        "FILE: <path>\n"
        "ACTION: replace\n"
        "<<<FIND\n"
        "exact lines to find in the file (must match exactly, including whitespace)\n"
        "<<<REPLACE\n"
        "replacement lines\n\n"
        "FORMAT for CREATING NEW files (only for files that do NOT exist yet):\n"
        "FILE: <path>\n"
        "ACTION: create\n"
        "<<<CONTENT\n"
        "full file content\n"
        ">>>\n\n"
        "CRITICAL RULES:\n"
        "- Output ONLY edit blocks. Start immediately with FILE:. "
        "No prose before or after edit blocks. No reasoning.\n"
        "- The first non-whitespace line of your response MUST be 'FILE: <path>'.\n"
        "- No markdown fences (```). No explanations, analysis, or comments.\n"
        "- Any output not starting with FILE: is INVALID and will be rejected.\n"
        "- Do NOT include any introductory or concluding text.\n"
        "- Use ACTION: create ONLY for files that do NOT exist in the repository.\n"
        "- For files that ALREADY EXIST (especially __init__.py, factory.py, config.py, "
        "__init__.py): you MUST use ACTION: replace with a FIND block.\n"
        "- If you are unsure whether a file exists, assume it EXISTS and use replace.\n"
        "- The FIND block must be an EXACT substring that appears EXACTLY ONCE in the file.\n"
        "- FIND must be NON-EMPTY for files that already have content.\n"
        "- FIND must NOT contain the delimiters <<<FIND, <<<REPLACE, or <<<CONTENT.\n"
        "- REPLACE must NOT contain the delimiters <<<FIND or <<<CONTENT.\n"
        "- Each FILE: block must be self-contained (do not nest FILE: delimiters).\n"
        "- FIND the smallest specific section that needs changing, not the entire file.\n"
        "- Make ONLY the changes the user requested. Do NOT change unrelated values\n"
        "  (ports, IPs, comments, etc.) unless the request explicitly asks for them.\n"
        "- Preserve the exact indentation of the surrounding code. New lines in REPLACE\n"
        "  must use the same indentation characters (spaces/tabs) as the lines they replace.\n"
        "- You may ONLY edit files listed in 'allowed_edit_files' (if provided).\n"
        "- You may ONLY create files under paths listed in 'allowed_create_paths'.\n"
        "- Do NOT modify .env, .git/, .claude/, *.key, *.pem, or config/llm_agent.yaml\n"
        "- Do NOT include any API keys, private keys, tokens, or passwords\n"
        "- If you cannot generate a safe patch, output nothing.\n"
    )

    # Sensitive content patterns to reject in diff bodies
    SENSITIVE_PATTERNS = [
        (re.compile(r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY"), "private key"),
        (re.compile(r"-----BEGIN\s+CERTIFICATE"), "certificate"),
        (re.compile(r'sk-[a-zA-Z0-9_-]{20,}'), "API key (sk- prefix)"),
        (re.compile(r'AIza[0-9A-Za-z_-]{20,}'), "Google API key"),
        (re.compile(r'eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}'), "JWT token"),
        (re.compile(r'(?:api[_-]?key|apikey|api_secret|secret_key)\s*[=:]\s*["\']?\S{8,}["\']?', re.IGNORECASE), "API key assignment"),
        (re.compile(r'(?:password|passwd|pwd)\s*=\s*["\'][^"\']{8,}["\']', re.IGNORECASE), "password assignment"),
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

    def __init__(self, config_path: str, root_dir: str = "."):
        self._config = self._load_config(config_path)
        self._api_key = os.environ.get(self._config["api_key_env"], "")
        if not self._api_key:
            raise LLMPatchGeneratorError(
                f"API key not found. Set the {self._config['api_key_env']} environment variable."
            )
        self._base_url = self._config["base_url"].rstrip("/")
        self._model = self._config.get("patch_model", self._config["model"])
        self._timeout = int(self._config.get("agent.request_timeout", 30))
        self._root_dir = root_dir
        self.protocol_retry_count = 0  # set by generate() after protocol retry
        self.protocol_retry_used = False
        self.semantic_retry_count = 0  # set by generate() after semantic retry
        self.semantic_retry_used = False
        self.last_semantic_error = None  # set by generate() with last semantic error message

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def _build_correction_prompt(previous_raw: str) -> str:
        """Build a correction prompt when the first response violates the FILE: protocol."""
        preview = previous_raw.strip()[:300]
        return (
            "Your previous response was INVALID because it did not start with "
            "'FILE: <path>'.\n\n"
            f"Your response started with:\n---\n{preview}\n---\n\n"
            "CRITICAL: You MUST output ONLY edit blocks in the correct format. "
            "Start immediately with 'FILE: <path>' on the very first line. "
            "Do NOT include introductions, explanations, markdown fences (```), "
            "analysis, reasoning, or any prose before or after the edit blocks. "
            "The first character of your response MUST be 'F' in 'FILE:'.\n\n"
            "Generate the edit instructions now."
        )

    @staticmethod
    def _build_semantic_correction_prompt(previous_raw: str, semantic_error: str) -> str:
        """Build a correction prompt when the response has semantic errors.

        Args:
            previous_raw: The previous LLM response that had semantic errors.
            semantic_error: The specific semantic validation error message.
        """
        preview = previous_raw.strip()[:500]
        return (
            f"Your previous response had a SEMANTIC ERROR and could not be processed.\n\n"
            f"Error: {semantic_error}\n\n"
            f"Your response preview:\n---\n{preview}\n---\n\n"
            "You MUST fix the above error and generate a corrected patch.\n\n"
            "IMPORTANT rules:\n"
            "- If the error says 'file already exists' or 'ACTION:create cannot be used':\n"
            "  The file ALREADY EXISTS in the repository. You MUST use ACTION: replace instead.\n"
            "  Look at the file's current content and write a proper FIND/REPLACE block.\n"
            "- If the error says 'FIND string not found':\n"
            "  Your FIND block does not match the actual file content. "
            "Read the file carefully and write an exact FIND.\n"
            "- If the error says 'FIND string matches N times':\n"
            "  Your FIND is not unique. Make it more specific.\n"
            "- If the error says 'not under allowed_create_paths' or 'not allowed':\n"
            "  The file path is not permitted. Choose a different path.\n\n"
            "CRITICAL: Output ONLY edit blocks. Start with 'FILE: <path>'. "
            "No explanations or prose. Use ACTION: replace for existing files.\n\n"
            "Generate the corrected edit instructions now."
        )

    def generate(self, user_request: str, task_plan, repository_context: str,
                 allowed_edit_files: list[str] | None = None,
                 allowed_create_paths: list[str] | None = None,
                 allowed_create_patterns: list[str] | None = None,
                 must_create_files: list[str] | None = None,
                 task_dir: str | None = None,
                 module_resolution=None,
                 stage_info: dict | None = None) -> str:
        """Generate a unified diff for the given request and plan.

        Args:
            user_request: Natural language request.
            task_plan: TaskPlan or LLMTaskPlan instance.
            repository_context: Repository context string from ContextBuilder.
            allowed_edit_files: If provided, only these files may be edited.
            allowed_create_paths: If provided, new files may only be created
                under these directory prefixes.
            allowed_create_patterns: If provided, new files must match one of
                these fnmatch glob patterns. Also blocks hidden files, binary
                files, path traversal, and .env/.git/*.key/*.pem.
            task_dir: If provided, raw LLM output is saved to
                task_dir/llm_patch_raw.txt when generation fails
                (for debugging/review).
            stage_info: If provided, stage-specific constraints for staged
                generation. Dict with keys: stage_name, description,
                prompt_section, constraint_text, allowed_edit_files,
                allowed_create_paths, required_edit_files,
                required_create_files, forbidden_files, max_output_files.

        Returns the raw patch text after passing all safety checks.

        Raises LLMPatchGeneratorError on any failure: HTTP error, invalid edit
        format, unsafe file paths, FIND not found / not unique, or sensitive content.
        """
        self.protocol_retry_count = 0
        self.protocol_retry_used = False
        self.semantic_retry_count = 0
        self.semantic_retry_used = False
        self.last_semantic_error = None

        extra_messages = None
        raw = None
        protocol_attempts = 0

        for attempt in range(self._MAX_TOTAL_ATTEMPTS):
            raw = self._call_api(user_request, task_plan, repository_context,
                                allowed_edit_files, allowed_create_paths,
                                allowed_create_patterns,
                                must_create_files=must_create_files,
                                extra_messages=extra_messages,
                                stage_info=stage_info,
                                module_resolution=module_resolution)

            # Phase 1: Strict protocol — response must start with FILE:
            if not raw.strip().startswith("FILE:"):
                protocol_attempts += 1
                self._save_raw_output(raw, task_dir,
                                      filename=f"llm_patch_raw_attempt{attempt + 1}.txt")

                if attempt < self._MAX_TOTAL_ATTEMPTS - 1:
                    self.protocol_retry_count += 1
                    self.protocol_retry_used = True
                    extra_messages = [
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": self._build_correction_prompt(raw)},
                    ]
                    continue

                # All attempts exhausted due to protocol errors
                first_line = raw.strip().split("\n")[0] if raw and raw.strip() else "(empty)"
                raise LLMPatchGeneratorError(
                    f"Patch response must start with FILE: header after "
                    f"{self._MAX_TOTAL_ATTEMPTS} attempts. "
                    f"Last first non-whitespace line: {first_line[:200]}"
                )

            # Protocol passed on this attempt — try semantic validation
            try:
                edits = self._parse_edit_blocks(raw)
                if not edits:
                    raise LLMPatchGeneratorError("LLM returned no valid edit blocks")

                self._validate_edits(edits, allowed_edit_files, allowed_create_paths,
                                    allowed_create_patterns)

                diff_text = self._generate_diff(edits)
                self._scan_for_secrets(diff_text)
                return diff_text

            except LLMPatchGeneratorError as e:
                # Semantic error — save raw, then retry if within limits
                self.last_semantic_error = str(e)
                self._save_raw_output(raw, task_dir,
                                      filename=f"llm_patch_raw_attempt{attempt + 1}.txt")

                if (attempt < self._MAX_TOTAL_ATTEMPTS - 1
                        and self.semantic_retry_count < self._MAX_SEMANTIC_ATTEMPTS):
                    self.semantic_retry_count += 1
                    self.semantic_retry_used = True
                    extra_messages = [
                        {"role": "assistant", "content": raw},
                        {"role": "user",
                         "content": self._build_semantic_correction_prompt(raw, str(e))},
                    ]
                    continue

                # Semantic retries exhausted or no attempts left — raise
                raise

        # Should never reach here — loop should raise or return
        raise LLMPatchGeneratorError(
            f"Patch generation failed after {self._MAX_TOTAL_ATTEMPTS} attempts"
        )

    @staticmethod
    def _save_raw_output(raw: str, task_dir: str | None,
                         filename: str = "llm_patch_raw.txt") -> None:
        """Save raw LLM output to task_dir for debugging."""
        if not task_dir:
            return
        try:
            os.makedirs(task_dir, exist_ok=True)
            filepath = os.path.join(task_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(raw)
        except OSError:
            pass  # best-effort, don't mask the original error

    def _validate_edits(self, edits: list[tuple[str, str, str, str]],
                         allowed_edit_files: list[str] | None,
                         allowed_create_paths: list[str] | None,
                         allowed_create_patterns: list[str] | None) -> None:
        """Validate all edit blocks for semantic correctness.

        Raises LLMPatchGeneratorError on any semantic violation.
        Does NOT catch SafetyError — let it propagate.
        """
        for filepath, action, find_str, replace_str in edits:
            if action == "create":
                self._validate_create_path(filepath, allowed_create_paths,
                                            allowed_create_patterns)
            else:
                self._validate_file_path(filepath)
                if allowed_edit_files is not None and filepath not in allowed_edit_files:
                    raise LLMPatchGeneratorError(
                        f"LLM attempted to edit file not in allowed_edit_files: {filepath}. "
                        f"Allowed: {allowed_edit_files}"
                    )
                self._verify_find_uniqueness(filepath, find_str)
            # Check for FIND/REPLACE delimiter leakage in output content
            _check_delimiter_leakage(filepath, find_str if action == "replace" else "",
                                      replace_str, action)

    def _verify_find_uniqueness(self, filepath: str, find_str: str) -> None:
        """Verify the FIND string appears exactly once in the target file.

        Allows empty FIND only when the target file is empty (zero bytes),
        which means "prepend this content to the empty file."
        """
        # Allow empty FIND for empty files (prepend operation)
        if not find_str:
            full_path = os.path.join(self._root_dir, filepath)
            if os.path.isfile(full_path) and os.path.getsize(full_path) == 0:
                return  # empty FIND on empty file = prepend, always unique
            raise LLMPatchGeneratorError(
                f"FIND string is empty for {filepath}. "
                "FIND must contain the exact lines to replace."
            )
        # Reject whitespace-only FIND
        if not find_str.strip():
            raise LLMPatchGeneratorError(
                f"FIND string is whitespace-only for {filepath}. "
                "FIND must contain non-whitespace content."
            )
        full_path = os.path.join(self._root_dir, filepath)
        if not os.path.isfile(full_path):
            raise LLMPatchGeneratorError(
                f"Cannot patch non-existent file: {filepath}"
            )
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        count = content.count(find_str)
        if count == 0:
            raise LLMPatchGeneratorError(
                f"FIND string not found in {filepath}. "
                f"FIND: {find_str[:200]!r}"
            )
        if count > 1:
            raise LLMPatchGeneratorError(
                f"FIND string matches {count} times in {filepath} (must be unique). "
                f"Make FIND more specific. FIND: {find_str[:200]!r}"
            )

    def _validate_create_path(self, filepath: str, allowed_create_paths: list[str] | None,
                              allowed_create_patterns: list[str] | None = None) -> None:
        """Validate that a new file path is allowed.

        Checks in order:
        1. Standard safety checks (blocked paths, extensions, SafetyGuard)
        2. File must not already exist
        3. Must be under an allowed_create_paths prefix (if provided)
        4. Must match an allowed_create_patterns glob (if provided)
        5. Must pass filename-level checks (no hidden files, path traversal,
           binary, blocked basenames like README.md, blocked extensions)

        Args:
            filepath: The path where the file would be created.
            allowed_create_paths: List of allowed directory prefixes.
            allowed_create_patterns: List of fnmatch glob patterns for filenames.

        Raises:
            LLMPatchGeneratorError: If the path is blocked or not allowed.
        """
        # Always run the standard safety checks first
        self._validate_file_path(filepath)

        # Check if the file already exists
        full_path = os.path.join(self._root_dir, filepath)
        if os.path.exists(full_path):
            raise LLMPatchGeneratorError(
                f"Cannot create {filepath}: file already exists. Use ACTION: replace to edit."
            )

        # Check if creation is allowed under allowed_create_paths
        if allowed_create_paths is not None:
            normalized = filepath.replace("\\", "/")
            allowed = any(
                normalized.startswith(acp.rstrip("/") + "/") or normalized.startswith(acp.rstrip("/"))
                for acp in allowed_create_paths
            )
            if not allowed:
                raise LLMPatchGeneratorError(
                    f"Cannot create {filepath}: not under allowed_create_paths: {allowed_create_paths}"
                )

        # Check filename-level rules via ImpactExpander's validator
        from src.llm.impact_expander import validate_create_filename
        err = validate_create_filename(filepath, allowed_create_patterns)
        if err is not None:
            raise LLMPatchGeneratorError(err)

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

    def _call_api(self, user_request: str, task_plan, repository_context: str,
                  allowed_edit_files: list[str] | None = None,
                  allowed_create_paths: list[str] | None = None,
                  allowed_create_patterns: list[str] | None = None,
                  must_create_files: list[str] | None = None,
                  extra_messages: list[dict] | None = None,
                  stage_info: dict | None = None,
                  module_resolution=None) -> str:
        planner_type = "llm_based" if hasattr(task_plan, "summary") else "rule_based"
        plan_summary = getattr(task_plan, "summary", "") or getattr(task_plan, "description", "")
        plan_dict = {
            "task_type": task_plan.task_type,
            "target_transport": task_plan.target_transport,
            "summary": plan_summary,
            "affected_areas": task_plan.affected_areas,
        }

        # Add file selection constraints to the prompt
        task_type = task_plan.task_type if hasattr(task_plan, "task_type") else ""
        constraints = ""

        # ---- Output budget strategy for large multi-file tasks ----
        planned_edit = len(allowed_edit_files or [])
        planned_create = len(must_create_files or [])
        planned_total = planned_edit + planned_create

        runtime_required = getattr(task_plan, "runtime_required", False)
        allow_skeleton = getattr(task_plan, "allow_skeleton", True)
        requires_default_switch = getattr(task_plan, "requires_default_switch", False)
        implementation_level = getattr(task_plan, "implementation_level", "skeleton")

        if task_type in ("transport_addition", "feature_addition") and planned_total >= 4:
            constraints += (
                "\nOUTPUT BUDGET WARNING: This task requires editing ~"
                f"{planned_edit} files and creating ~{planned_create} files "
                f"({planned_total} total).\n"
                "You MUST generate a complete FILE: block for EVERY required file. "
                "Do NOT truncate any file mid-line. Each FILE block must end with "
                "a complete line and newline.\n"
            )
            if allow_skeleton and not runtime_required:
                constraints += (
                    "For new transport implementations, "
                    "generate a SKELETON only — the class must be importable and "
                    "constructable, but methods like connect() should raise "
                    "TransportError with a clear 'skeleton not yet implemented' message. "
                    "Full protocol implementation belongs in a follow-up task.\n"
                )
            elif runtime_required:
                constraints += (
                    "RUNTIME REQUIREMENT: You MUST generate a runtime-usable "
                    "transport — NOT a skeleton. connect() must establish a real "
                    "connection. send() and recv() must transmit actual data. "
                    "Do NOT raise TransportError in connect/send/recv as a "
                    "'not implemented' placeholder. Implement the minimal "
                    "runtime subset needed for local controlled testing.\n"
                )

        # Anti-bypass: if target transport file exists, must edit it — not create parallel
        target_transport = getattr(task_plan, "target_transport", None)
        if target_transport and task_type in ("transport_addition", "feature_addition", "transport_change"):
            import os as _os
            existing = f"src/transport/{target_transport}_transport.py"
            if _os.path.isfile(existing):
                constraints += (
                    f"\nEXISTING TRANSPORT FILE DETECTED: {existing}\n"
                    f"You MUST upgrade this existing file — do NOT create a parallel file.\n"
                    f"FORBIDDEN file names (do NOT create any of these):\n"
                    f"  - src/transport/{target_transport}_full_transport.py\n"
                    f"  - src/transport/{target_transport}_runtime_transport.py\n"
                    f"  - src/transport/{target_transport}_new_transport.py\n"
                    f"  - src/transport/{target_transport}_v2_transport.py\n"
                    f"Use ACTION: replace on {existing} to upgrade the skeleton to runtime.\n"
                )

        if requires_default_switch:
            default_target = getattr(task_plan, "default_transport_target", None) or "the new transport"
            constraints += (
                f"\nDEFAULT SWITCH REQUESTED: The user wants to set "
                f"'{default_target}' as the default transport. "
                "You must verify that the transport is runtime-usable BEFORE "
                "modifying any default config.\n"
                "DO NOT modify config/client.yaml or config/server.yaml "
                "default transport type unless the transport passes runtime smoke tests.\n"
            )

        if allowed_edit_files:
            constraints += (
                f"\nALLOWED EDIT FILES (you may ONLY edit these):\n"
                + "\n".join(f"  - {f}" for f in allowed_edit_files)
            )
        if must_create_files:
            constraints += (
                f"\nMUST CREATE FILES (you MUST generate ALL of these):\n"
                + "\n".join(f"  - {f}" for f in must_create_files)
                + "\n\nIMPORTANT: Every file listed under MUST CREATE FILES must appear "
                "as a FILE: block with ACTION: create. Missing any of these files "
                "means the patch is INCOMPLETE and will be REJECTED."
            )
        if allowed_create_paths:
            constraints += (
                f"\nALLOWED CREATE DIRECTORIES (new files must be under these):\n"
                + "\n".join(f"  - {p}/" for p in allowed_create_paths)
            )
        if allowed_create_patterns:
            constraints += (
                f"\nALLOWED CREATE PATTERNS (new file names must match one of these globs):\n"
                + "\n".join(f"  - {p}" for p in allowed_create_patterns)
                + "\n  README.md is NOT allowed to create (edit only)."
                + "\n  Hidden files, path traversal (..), and blocked extensions (.key, .pem, .env) are forbidden."
            )

        # Expected artifact guidance for transport_addition requests
        constraints += _build_expected_artifacts_guidance(user_request, task_plan)

        # ---- IntentContract prompt directive (V2, covers all task types) ----
        intent_contract = getattr(task_plan, "intent_contract", None)
        if intent_contract is not None:
            directive = intent_contract.build_prompt_directive()
            constraints += f"\n{directive}\n"
            # Add forbidden degradations as explicit DON'T constraints
            if intent_contract.forbidden_degradations:
                constraints += "\nFORBIDDEN (do NOT do any of these):\n"
                for d in intent_contract.forbidden_degradations:
                    constraints += f"  - {d}\n"
        elif task_type in ("transport_addition", "feature_addition"):
            # Fallback to legacy implementation-level directive
            if runtime_required:
                constraints += (
                    "\nIMPLEMENTATION LEVEL: RUNTIME\n"
                    "You MUST generate a fully functional transport — NOT a skeleton.\n"
                    "- connect() MUST establish a real local connection (TCP socket, etc.)\n"
                    "- send() and recv() MUST work after connect()\n"
                    "- connect/send/recv MUST NOT raise TransportError as a "
                    "'not implemented' placeholder\n"
                    "- Tests MUST include client/server roundtrip with actual data\n"
                    "- If the protocol is too large, implement the minimal runtime "
                    "subset needed for local controlled tests\n"
                )
            elif allow_skeleton:
                constraints += (
                    "\nIMPLEMENTATION LEVEL: SKELETON\n"
                    "Generate a SKELETON implementation only.\n"
                    "- The class must be importable, constructable, and factory-registered\n"
                    "- connect() should raise TransportError with a clear "
                    "'skeleton not yet implemented' message\n"
                    "- send() and recv() should raise TransportError similarly\n"
                    "- close() may be a no-op\n"
                    "- Docs MUST state the transport is NOT runtime usable\n"
                    "- Do NOT change the default transport config\n"
                )

        # ---- Stage-specific constraints (overrides general constraints) ----
        if stage_info is not None:
            stage_section = stage_info.get("prompt_section", "")
            stage_constraint_text = stage_info.get("constraint_text", "")
            constraints += f"\n{stage_section}\n{stage_constraint_text}\n"
            constraints += f"\nSTAGE OUTPUT LIMIT: Generate at most "
            constraints += f"{stage_info.get('max_output_files', 4)} file(s) in this stage.\n"
            constraints += "Do NOT generate files outside this stage's allowed patterns.\n"

            # Replace file constraints with stage-specific ones
            stage_allowed_edit = stage_info.get("allowed_edit_files", [])
            stage_allowed_create = stage_info.get("allowed_create_paths", [])
            stage_required_edit = stage_info.get("required_edit_files", [])
            stage_required_create = stage_info.get("required_create_files", [])
            stage_forbidden = stage_info.get("forbidden_files", [])

            if stage_allowed_edit:
                constraints += (
                    f"\nSTAGE ALLOWED EDIT FILES (ONLY these in this stage):\n"
                    + "\n".join(f"  - {f}" for f in stage_allowed_edit)
                )
            if stage_allowed_create:
                constraints += (
                    f"\nSTAGE ALLOWED CREATE PATHS:\n"
                    + "\n".join(f"  - {f}" for f in stage_allowed_create)
                )
            if stage_required_edit:
                constraints += (
                    f"\nSTAGE REQUIRED EDIT FILES:\n"
                    + "\n".join(f"  - {f}" for f in stage_required_edit)
                )
            if stage_required_create:
                constraints += (
                    f"\nSTAGE REQUIRED CREATE FILES:\n"
                    + "\n".join(f"  - {f}" for f in stage_required_create)
                )
            if stage_forbidden:
                constraints += (
                    f"\nSTAGE FORBIDDEN FILES (do NOT touch):\n"
                    + "\n".join(f"  - {f}" for f in stage_forbidden)
                )

        # ---- Module Contract + Patch Blueprint prompt injection ----
        if module_resolution is not None:
            from src.llm.patch_blueprints import extract_detected_metrics
            transport_name = getattr(task_plan, "target_transport", "") or ""
            stage_name = stage_info.get("stage_name", "") if stage_info else ""
            detected_metrics = extract_detected_metrics(
                user_request,
                module_name=getattr(module_resolution, "selected_module", None),
            )
            module_section = _build_module_contract_prompt_section(
                module_resolution,
                transport_name=transport_name,
                stage_name=stage_name,
                detected_metrics=detected_metrics,
            )
            if module_section:
                constraints += "\n\n" + module_section

        user_message = (
            f"USER REQUEST:\n{user_request}\n\n"
            f"TASK PLAN:\n{json.dumps(plan_dict, indent=2, ensure_ascii=False)}\n\n"
            f"REPOSITORY CONTEXT:\n{repository_context}\n"
            f"{constraints}\n\n"
            "Generate the edit instructions that implement these changes. "
            "Output ONLY edit blocks. Start immediately with 'FILE: <path>'. "
            "No prose before or after edit blocks. No markdown fences. No reasoning."
        )

        url = f"{self._base_url}/chat/completions"
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
        if extra_messages:
            messages.extend(extra_messages)
        body = json.dumps({
            "model": self._model,
            "messages": messages,
            "temperature": 0.1,
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

        if os.environ.get("VPN_LLM_VERBOSE") == "1":
            _verbose_print("=== LLM Call: PatchGenerator ===")
            _verbose_print(f"URL: {url}")
            _verbose_print(f"Model: {self._model}")
            _verbose_print(f"System prompt ({len(self.SYSTEM_PROMPT)} chars): {self.SYSTEM_PROMPT[:200]}...")
            _verbose_print(f"Request body ({len(body)} bytes): {body[:4000]}")
            _verbose_print("--- sending request ---")

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise LLMPatchGeneratorError(f"LLM API HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise LLMPatchGeneratorError(f"LLM API connection error: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise LLMPatchGeneratorError(f"LLM API returned invalid JSON: {e}") from e

        data = json.loads(raw)

        if os.environ.get("VPN_LLM_VERBOSE") == "1":
            _verbose_print(f"--- response ({len(raw)} bytes) ---")
            _verbose_print(raw[:4000])
            _verbose_print("=== End PatchGenerator ===")

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMPatchGeneratorError(f"Unexpected API response structure: {e}") from e

        # Some reasoning models (e.g. deepseek-v4-pro) may put all output in
        # reasoning_content and leave content empty. Only fall back when the
        # reasoning content itself looks like a valid patch (starts with FILE:).
        # Otherwise return content as-is (even empty) so the protocol check in
        # generate() triggers a retry with a correction prompt.
        if not content or not content.strip():
            reasoning = data["choices"][0]["message"].get("reasoning_content", "")
            if reasoning and reasoning.strip():
                stripped = reasoning.strip()
                if stripped.startswith("FILE:"):
                    return stripped
                # Model put reasoning in reasoning_content but didn't produce
                # a valid patch. Return empty to trigger protocol retry.
                return ""

        return content

    # ------------------------------------------------------------------
    # Internal: edit block parsing (FIND/REPLACE format)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_edit_blocks(raw: str) -> list[tuple[str, str, str, str]]:
        """Parse LLM output into (filepath, action, find_str, replace_str) tuples.

        Supports two formats:
        1. Default (replace): FILE: <path>\\n<<<FIND\\n...<<<REPLACE\\n...
        2. Create: FILE: <path>\\nACTION: create\\n<<<CONTENT\\n...>>>
        """
        text = raw.strip()

        if not text:
            raise LLMPatchGeneratorError("LLM returned empty output")

        edits = []
        # Split by FILE: to get blocks
        blocks = re.split(r'\n(?=FILE:\s)', text)

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            # Parse FILE: line
            m = re.match(r'^FILE:\s*(.+)$', block, re.MULTILINE)
            if not m:
                raise LLMPatchGeneratorError(
                    f"Edit block missing FILE: header: {block[:200]}"
                )
            filepath = m.group(1).strip()

            # Detect action (default: replace)
            action_match = re.search(r'^ACTION:\s*(create|replace)\s*$', block, re.MULTILINE)
            if action_match:
                action = action_match.group(1).strip()
            else:
                action = "replace"  # default for backward compatibility

            if action == "create":
                # Parse CONTENT block
                content_match = re.search(r'<<<CONTENT\n(.*?)(?=\n>>>|\Z)', block, re.DOTALL)
                if not content_match:
                    raise LLMPatchGeneratorError(
                        f"Create block for {filepath} missing <<<CONTENT section"
                    )
                content = content_match.group(1)

                # Validate: CONTENT must not contain FILE: delimiter (block nesting)
                if "FILE:" in content:
                    raise LLMPatchGeneratorError(
                        f"Malformed create block for {filepath}: "
                        "CONTENT must not contain FILE: delimiter. "
                        "Each block must be self-contained."
                    )

                edits.append((filepath, "create", "", content))
            else:
                # Parse FIND / REPLACE sections
                find_match = re.search(r'<<<FIND\n(.*?)(?=\n<<<REPLACE|\Z)', block, re.DOTALL)
                replace_match = re.search(r'<<<REPLACE\n(.*?)$', block, re.DOTALL)

                if not find_match:
                    raise LLMPatchGeneratorError(
                        f"Edit block for {filepath} missing <<<FIND section"
                    )
                find_str = find_match.group(1)

                # Post-process: if FIND starts with <<<REPLACE, the LLM intended
                # empty FIND (e.g., for an empty file). The regex captured
                # <<<REPLACE into FIND because there is no \n before it.
                if find_str.startswith("<<<REPLACE"):
                    find_str = ""

                # Validate: FIND must not contain delimiters
                LLMPatchGenerator._validate_find_replace_content(
                    filepath, find_str, "FIND",
                    block_delimiters=("<<<FIND", "<<<REPLACE", "<<<CONTENT"))

                replace_str = ""
                if replace_match:
                    replace_str = replace_match.group(1)
                    # Strip trailing closing delimiters the LLM may incorrectly add
                    # (LLMs often close replace blocks with <<<REPLACE or >>>)
                    replace_str = re.sub(r'\n(?:<<<REPLACE|>>>)\s*$', '', replace_str)
                    # Validate: REPLACE must not contain FIND or CONTENT delimiters
                    LLMPatchGenerator._validate_find_replace_content(
                        filepath, replace_str, "REPLACE",
                        block_delimiters=("<<<FIND", "<<<CONTENT"))

                edits.append((filepath, "replace", find_str, replace_str))

        return edits

    @staticmethod
    def _validate_find_replace_content(filepath: str, content: str, section: str,
                                       block_delimiters: tuple[str, ...]) -> None:
        """Validate that FIND/REPLACE content does not contain block delimiters.

        Raises LLMPatchGeneratorError with a clear message if a delimiter is found.
        """
        for delim in block_delimiters:
            if delim in content:
                raise LLMPatchGeneratorError(
                    f"Malformed {section} block for {filepath}: "
                    f"delimiter '{delim}' found inside {section} content. "
                    f"FIND must contain exact file content to match, "
                    f"REPLACE must contain only the replacement text."
                )

    def _generate_diff(self, edits: list[tuple[str, str, str, str]]) -> str:
        """Generate a unified diff from edit blocks.

        For replace actions: produces a standard unified diff.
        For create actions: produces a diff showing file creation.
        """
        import difflib

        parts = []
        for filepath, action, find_str, replace_str in edits:
            full_path = os.path.join(self._root_dir, filepath)

            if action == "create":
                # Generate a "new file" diff with /dev/null as old path
                # (required by git apply for new files)
                modified = replace_str  # replace_str holds the full content for create
                had_newline = modified.endswith("\n")

                parts.append(f"diff --git a/{filepath} b/{filepath}\n")
                parts.append(f"new file mode 100644\n")
                parts.append(f"index 0000000..0000000\n")
                parts.append(f"--- /dev/null\n")
                parts.append(f"+++ b/{filepath}\n")

                lines = modified.splitlines(keepends=True)
                line_count = len(lines) if lines else 0
                parts.append(f"@@ -0,0 +1,{line_count} @@\n")
                for line in lines:
                    parts.append(f"+{line}")

                if not had_newline:
                    # Ensure the marker is on its own line: if the last
                    # content line lacks \n, prepend one to the marker.
                    if parts and not parts[-1].endswith("\n"):
                        parts.append("\n")
                    parts.append("\\ No newline at end of file\n")
            else:
                # Standard replace
                if not os.path.isfile(full_path):
                    raise LLMPatchGeneratorError(
                        f"Cannot patch non-existent file: {filepath}"
                    )
                with open(full_path, "r", encoding="utf-8") as f:
                    original = f.read()

                if find_str not in original:
                    raise LLMPatchGeneratorError(
                        f"FIND string not found in {filepath}. "
                        f"FIND: {find_str[:200]!r}"
                    )

                modified = original.replace(find_str, replace_str, 1)
                had_newline = original.endswith("\n")

                parts.append(f"diff --git a/{filepath} b/{filepath}\n")
                diff = difflib.unified_diff(
                    original.splitlines(keepends=True),
                    modified.splitlines(keepends=True),
                    fromfile=f"a/{filepath}",
                    tofile=f"b/{filepath}",
                )
                diff_text = "".join(diff)
                if not had_newline:
                    diff_text += "\n\\ No newline at end of file\n"
                elif diff_text and not diff_text.endswith("\n"):
                    diff_text += "\n"
                parts.append(diff_text)

        result = "".join(parts)
        if not result:
            raise LLMPatchGeneratorError("Generated diff is empty")
        return result

    # ------------------------------------------------------------------
    # Internal: legacy diff extraction (kept for test compatibility)
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

        # git apply requires every line (including the last) to end with \n.
        # .strip() above removed trailing whitespace, so add one back.
        return text + "\n"

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


# ---------------------------------------------------------------------------
# Patch completeness validation
# ---------------------------------------------------------------------------

@dataclass
class PatchCompletenessResult:
    """Result of checking a generated patch for completeness."""

    passed: bool
    planned_file_count: int
    actual_file_count: int
    missing_files: list[str] = field(default_factory=list)
    truncated_files: list[str] = field(default_factory=list)
    syntax_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "planned_file_count": self.planned_file_count,
            "actual_file_count": self.actual_file_count,
            "missing_files": self.missing_files,
            "truncated_files": self.truncated_files,
            "syntax_errors": self.syntax_errors,
            "warnings": self.warnings,
        }


def check_patch_completeness(
    patch_text: str,
    must_create_files: list[str] | None = None,
    must_edit_files: list[str] | None = None,
) -> PatchCompletenessResult:
    """Validate a generated patch for completeness and integrity.

    Checks:
    1. File count: planned vs actual FILE blocks
    2. Required files: must_create files are all present
    3. Truncation: no file ends mid-line
    4. Syntax: Python files parse without error

    Args:
        patch_text: The generated unified diff text.
        must_create_files: Files that MUST appear as new-file blocks.
        must_edit_files: Files that SHOULD appear as edit blocks.

    Returns:
        PatchCompletenessResult with pass/fail and details.
    """
    import ast

    must_create = must_create_files or []
    must_edit = must_edit_files or []
    planned_count = len(must_create) + len(must_edit)
    warnings: list[str] = []
    truncated_files: list[str] = []
    syntax_errors: list[str] = []
    missing_files: list[str] = []

    # Extract file paths from the diff
    actual_paths = _extract_diff_file_paths(patch_text)
    actual_count = len(actual_paths)

    # 1. File count check
    if planned_count > 0 and actual_count < planned_count:
        warnings.append(
            f"Planned {planned_count} files but patch only contains {actual_count}"
        )

    # 2. Required files presence
    for f in must_create:
        # Normalize: diff paths use a/ and b/ prefixes
        if f not in actual_paths:
            missing_files.append(f)

    # 3. Truncation check — parse the diff hunks
    new_file_content: dict[str, str] = {}
    current_file = None
    in_hunk = False
    for line in patch_text.splitlines():
        # Detect new file sections
        if line.startswith("--- /dev/null"):
            # Next +++ b/ line names the new file
            continue
        if line.startswith("+++ b/"):
            current_file = line[6:]
            new_file_content[current_file] = ""
            in_hunk = False
            continue
        if line.startswith("@@") and current_file:
            in_hunk = True
            continue
        if in_hunk and current_file and line.startswith("+"):
            # Strip the leading +
            content_line = line[1:]
            # Strip git's "no newline" marker if it appears on the same line
            no_nl_pos = content_line.find("\\ No newline at end of file")
            if no_nl_pos >= 0:
                content_line = content_line[:no_nl_pos]
            new_file_content[current_file] += content_line + "\n"

    for fpath, content in new_file_content.items():
        if not content:
            continue
        # Check for mid-line truncation: last meaningful char is backslash
        stripped = content.rstrip()
        if stripped.endswith("\\"):
            truncated_files.append(f"{fpath} (ends with backslash)")
            continue

        # Check for unclosed triple quotes
        if stripped.count('"""') % 2 != 0:
            truncated_files.append(f"{fpath} (unclosed triple-quoted string)")
            continue
        if stripped.count("'''") % 2 != 0:
            truncated_files.append(f"{fpath} (unclosed triple-quoted string)")
            continue

        # Check for unbalanced brackets
        brackets = {"(": ")", "[": "]", "{": "}"}
        stack = []
        in_string = False
        string_char = ""
        for ch in content:
            if in_string:
                if ch == string_char:
                    in_string = False
                continue
            if ch in ('"', "'"):
                in_string = True
                string_char = ch
                continue
            if ch in brackets:
                stack.append(brackets[ch])
            elif ch in brackets.values():
                if stack and stack[-1] == ch:
                    stack.pop()
        if stack:
            truncated_files.append(
                f"{fpath} (unclosed brackets: {''.join(stack[-3:])})"
            )

        # 4. Syntax check for Python files
        if fpath.endswith(".py"):
            try:
                ast.parse(content)
            except SyntaxError as e:
                syntax_errors.append(f"{fpath}:{e.lineno}: {e.msg}")

    # Determine overall pass/fail
    passed = (
        len(missing_files) == 0
        and len(truncated_files) == 0
        and len(syntax_errors) == 0
    )

    return PatchCompletenessResult(
        passed=passed,
        planned_file_count=planned_count,
        actual_file_count=actual_count,
        missing_files=missing_files,
        truncated_files=truncated_files,
        syntax_errors=syntax_errors,
        warnings=warnings,
    )


def _extract_diff_file_paths(diff_text: str) -> list[str]:
    """Extract file paths referenced in a unified diff.

    Standalone helper for check_patch_completeness (avoids circular import).
    """
    _DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$")
    _DIFF_A_RE = re.compile(r"^--- a/(.+?)$")
    _DIFF_B_RE = re.compile(r"^\+\+\+ b/(.+?)$")

    paths = set()
    for line in diff_text.splitlines():
        m = _DIFF_GIT_RE.match(line)
        if m:
            paths.add(m.group(1))
            paths.add(m.group(2))
            continue
        m = _DIFF_A_RE.match(line)
        if m:
            paths.add(m.group(1))
            continue
        m = _DIFF_B_RE.match(line)
        if m:
            paths.add(m.group(1))
    return sorted(paths)


# ---------------------------------------------------------------------------
# Runtime transport contract validation
# ---------------------------------------------------------------------------

@dataclass
class RuntimeTransportCheckResult:
    """Result of checking a generated transport for runtime capability."""

    passed: bool
    runtime_required: bool
    is_skeleton: bool = False
    has_roundtrip_test: bool = False
    connect_raises_error: bool = False
    send_raises_error: bool = False
    recv_raises_error: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "runtime_required": self.runtime_required,
            "is_skeleton": self.is_skeleton,
            "has_roundtrip_test": self.has_roundtrip_test,
            "connect_raises_error": self.connect_raises_error,
            "send_raises_error": self.send_raises_error,
            "recv_raises_error": self.recv_raises_error,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def check_runtime_transport_contract(
    patch_text: str,
    runtime_required: bool = False,
    allow_skeleton: bool = True,
    requires_default_switch: bool = False,
    default_transport_target: str | None = None,
) -> RuntimeTransportCheckResult:
    """Check whether a generated transport satisfies the runtime contract.

    For runtime_required=True:
    - Transport class's connect/send/recv must NOT just raise TransportError
    - Tests must contain roundtrip or client/server test classes/functions
    - If requires_default_switch, additional checks apply

    For skeleton (runtime_required=False, allow_skeleton=True):
    - Skeleton is acceptable; no errors raised
    - But default switch is still forbidden

    Args:
        patch_text: The generated unified diff text.
        runtime_required: Whether a runtime-capable transport is required.
        allow_skeleton: Whether skeleton-only is acceptable.
        requires_default_switch: Whether the request asks to change default.
        default_transport_target: Which transport to set as default.

    Returns:
        RuntimeTransportCheckResult with pass/fail and details.
    """
    errors: list[str] = []
    warnings: list[str] = []
    is_skeleton = False
    has_roundtrip_test = False
    connect_raises_error = False
    send_raises_error = False
    recv_raises_error = False

    # Extract content of new transport files from the diff
    new_file_content: dict[str, str] = {}
    current_file = None
    in_hunk = False
    for line in patch_text.splitlines():
        if line.startswith("--- /dev/null"):
            continue
        if line.startswith("+++ b/"):
            current_file = line[6:]
            new_file_content[current_file] = ""
            in_hunk = False
            continue
        if line.startswith("@@") and current_file:
            in_hunk = True
            continue
        if in_hunk and current_file and line.startswith("+"):
            content_line = line[1:]
            no_nl_pos = content_line.find("\\ No newline at end of file")
            if no_nl_pos >= 0:
                content_line = content_line[:no_nl_pos]
            new_file_content[current_file] += content_line + "\n"

    # Find the transport implementation file
    transport_files = [f for f in new_file_content
                       if f.startswith("src/transport/") and f.endswith("_transport.py")
                       and "socks5" not in f.lower()]  # skip socks5 unless target
    if not transport_files:
        transport_files = [f for f in new_file_content
                           if f.startswith("src/transport/") and f.endswith("_transport.py")]

    if transport_files:
        transport_content = new_file_content[transport_files[0]]
        # Detect skeleton pattern: connect/send/recv that raise TransportError with
        # "skeleton" or "not fully implemented" messages
        skeleton_patterns = [
            r'raise\s+TransportError\s*\(\s*["\'].*?(?:skeleton|not\s+(?:yet\s+)?implemented|not\s+fully\s+implemented).*?["\']\s*\)',
            r'raise\s+NotImplementedError',
        ]
        for pat in skeleton_patterns:
            if re.search(pat, transport_content, re.IGNORECASE):
                is_skeleton = True
                break

        # Check if connect() raises an error
        connect_raises = re.findall(
            r'def\s+connect\s*\([^)]*\)[^:]*:\s*(?:.+?\n)*?\s+(raise\s+\w+)',
            transport_content,
        )
        if connect_raises:
            connect_raises_error = True

        # Check if send() raises an error
        send_raises = re.findall(
            r'def\s+send\s*\([^)]*\)[^:]*:\s*(?:.+?\n)*?\s+(raise\s+\w+)',
            transport_content,
        )
        if send_raises:
            send_raises_error = True

        # Check if recv() raises an error
        recv_raises = re.findall(
            r'def\s+recv\s*\([^)]*\)[^:]*:\s*(?:.+?\n)*?\s+(raise\s+\w+)',
            transport_content,
        )
        if recv_raises:
            recv_raises_error = True

    # Find test files and check for roundtrip tests
    test_files = [f for f in new_file_content if f.startswith("tests/test_")]
    for tf in test_files:
        content = new_file_content[tf]
        has_roundtrip = bool(re.search(
            r'(?:roundtrip|round_trip|RoundTrip|send_recv|SendRecv|'
            r'client_server|ClientServer|smoke|Smoke)',
            content,
        ))
        if has_roundtrip:
            has_roundtrip_test = True
            break

    # ---- Gate checks ----
    if runtime_required:
        if is_skeleton:
            errors.append(
                "RUNTIME CONTRACT VIOLATION: Transport is skeleton-only "
                "(connect/send/recv raise TransportError with 'not implemented' "
                "message). Runtime transport must have working connect/send/recv."
            )
        if not has_roundtrip_test:
            errors.append(
                "RUNTIME CONTRACT VIOLATION: No roundtrip or client/server test "
                "found. Runtime transport must include tests that exercise "
                "actual data transmission."
            )

    if requires_default_switch:
        if is_skeleton:
            errors.append(
                "DEFAULT SWITCH GATE: Cannot set skeleton transport as default. "
                "Transport must be runtime-usable before default switch."
            )
        if not has_roundtrip_test:
            errors.append(
                "DEFAULT SWITCH GATE: No roundtrip test found. "
                "Cannot switch default transport without runtime smoke coverage."
            )

    if allow_skeleton and not runtime_required and is_skeleton:
        warnings.append(
            "Transport is skeleton-only. Default switch is forbidden."
        )

    passed = len(errors) == 0

    return RuntimeTransportCheckResult(
        passed=passed,
        runtime_required=runtime_required,
        is_skeleton=is_skeleton,
        has_roundtrip_test=has_roundtrip_test,
        connect_raises_error=connect_raises_error,
        send_raises_error=send_raises_error,
        recv_raises_error=recv_raises_error,
        errors=errors,
        warnings=warnings,
    )
