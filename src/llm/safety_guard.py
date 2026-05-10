"""Safety Guard for LLM Agent modifications.

Blocks dangerous paths and commands before any file write or command execution.
"""

import os
import re


class SafetyError(Exception):
    """Raised when a safety boundary is violated."""


class SafetyGuard:
    """Enforces security boundaries for LLM-initiated actions."""

    # Patterns for blocked file paths (checked against basename or full path)
    BLOCKED_BASENAMES = frozenset({
        ".env",
    })

    BLOCKED_EXTENSIONS = frozenset({
        ".key",
    })

    BLOCKED_KEYWORDS = frozenset({
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
        "cert.pem",
        "key.pem",
        "credentials",
        "secret",
        "token",
    })

    BLOCKED_PATH_PREFIXES = (
        ".claude/",
        ".git/",
    )

    # Patterns for blocked shell commands
    BLOCKED_COMMAND_PATTERNS = [
        (re.compile(r"\bsudo\b"), "sudo is blocked"),
        (re.compile(r"\brm\s+-(?:r[fw]?|fr|rf)\b"), "rm -rf (recursive force delete) is blocked"),
        (re.compile(r"\bcurl\b.*\|.*\b(?:ba)?sh\b"), "curl | bash is blocked"),
        (re.compile(r"\bwget\b.*\|.*\b(?:ba)?sh\b"), "wget | bash is blocked"),
        (re.compile(r"\bgit\s+push\b"), "git push is blocked (no automatic push)"),
    ]

    @classmethod
    def validate_write_path(cls, path: str) -> None:
        """Check whether a file path is safe for writes.

        Args:
            path: The path to validate, relative or absolute.

        Raises:
            SafetyError: If the path is blocked.
        """
        normalized = os.path.normpath(path)
        basename = os.path.basename(normalized)

        if basename in cls.BLOCKED_BASENAMES:
            raise SafetyError(f"Blocked path: {path} (.env files are protected)")

        _, ext = os.path.splitext(basename)
        if ext.lower() in cls.BLOCKED_EXTENSIONS:
            raise SafetyError(f"Blocked path: {path} (private key files are protected)")

        basename_lower = basename.lower()
        for keyword in cls.BLOCKED_KEYWORDS:
            if keyword in basename_lower:
                raise SafetyError(f"Blocked path: {path} (matches blocked keyword '{keyword}')")

        components = normalized.split(os.sep)
        for prefix in cls.BLOCKED_PATH_PREFIXES:
            prefix_clean = prefix.rstrip("/")
            if normalized.startswith(prefix) or prefix_clean in components:
                raise SafetyError(f"Blocked path: {path} (matches blocked prefix '{prefix}')")

    @classmethod
    def validate_command(cls, command: str) -> None:
        """Check whether a shell command is safe to execute.

        Args:
            command: The shell command to validate.

        Raises:
            SafetyError: If the command contains a blocked pattern.
        """
        for pattern, message in cls.BLOCKED_COMMAND_PATTERNS:
            if pattern.search(command):
                raise SafetyError(f"Blocked command: {message}. Command: {command[:80]}...")
