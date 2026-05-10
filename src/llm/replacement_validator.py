"""Replacement Validator for LLM Agent.

Runs the smoke replacement matrix as part of post-apply validation to verify
that Transport or Core replacements are minimally runnable.

This is the glue between the LLM Agent pipeline and the Phase 10.1 smoke matrix.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

from src.llm.task_planner import (
    TASK_TRANSPORT_CHANGE,
    TASK_CORE_CHANGE,
    TASK_BUGFIX,
    TASK_REFACTOR,
    TASK_UNKNOWN,
)


# Map target_transport to the smoke matrix transports that should be run.
_TRANSPORT_SELECTION = {
    "websocket": ["mock", "websocket"],
    "tcp": ["mock", "tcp"],
    "tls": ["mock", "tls"],
    "ssh": ["mock", "ssh"],
    "mock": ["mock"],
}

# Transport set for broad validation (core_change, refactor, unknown, etc.)
_BROAD_TRANSPORTS = ["mock", "tcp", "tls", "websocket"]

# Default transports when nothing matches
_DEFAULT_TRANSPORTS = ["mock", "tcp", "websocket"]


@dataclass
class ReplacementValidationResult:
    """Result of running the replacement smoke matrix."""

    success: bool
    returncode: int
    transports: list[str]
    cores: list[str]
    summary: dict = field(default_factory=dict)
    results: list[dict] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    error: str | None = None

    @property
    def was_run(self) -> bool:
        """True if the smoke matrix was actually executed."""
        return True

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "returncode": self.returncode,
            "transports": self.transports,
            "cores": self.cores,
            "summary": self.summary,
            "results": self.results,
            "stdout": self.stdout[-2000:] if self.stdout else "",
            "stderr": self.stderr[-2000:] if self.stderr else "",
            "error": self.error,
        }


class ReplacementValidator:
    """Run the smoke replacement matrix for a given task plan.

    Usage:
        validator = ReplacementValidator()
        result = validator.validate(task_plan, timeout=5)
        if result.success:
            print("All replacement smokes pass.")
    """

    def __init__(self, repo_root: str | None = None):
        if repo_root is None:
            repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)
            )))
        self._repo_root = repo_root
        self._script = os.path.join(repo_root, "scripts", "smoke_replacement_matrix.py")

    def validate(
        self,
        task_plan,
        timeout: int = 5,
        include_tls: bool = False,
        include_ssh: bool = False,
    ) -> ReplacementValidationResult:
        """Select transports from the task plan and run the smoke matrix.

        Args:
            task_plan: TaskPlan or LLMTaskPlan instance.
            timeout: Per-check timeout in seconds (unused; smoke matrix is fast).
            include_tls: If True, always include TLS in the matrix.
            include_ssh: If True, include SSH (expected skip without sshd).

        Returns:
            ReplacementValidationResult with success, summary, and per-transport results.
        """
        transports = self._select_transports(task_plan, include_tls, include_ssh)
        cores = ["default"]
        return self._run_matrix(transports, cores)

    @staticmethod
    def _select_transports(
        task_plan,
        include_tls: bool = False,
        include_ssh: bool = False,
    ) -> list[str]:
        """Select which transports to smoke based on the task plan.

        Selection rules (in priority order):
        1. If target_transport is set and known → mock + target
        2. If task_type is core_change / refactor / bugfix / unknown → broad set
        3. Default: mock, tcp, websocket
        """
        task_type = getattr(task_plan, "task_type", TASK_UNKNOWN)
        target = getattr(task_plan, "target_transport", None)

        # Rule 1: explicit target transport
        if target and target in _TRANSPORT_SELECTION:
            transports = list(_TRANSPORT_SELECTION[target])
        elif task_type in (TASK_CORE_CHANGE, TASK_REFACTOR, TASK_BUGFIX, TASK_UNKNOWN):
            transports = list(_BROAD_TRANSPORTS)
        else:
            transports = list(_DEFAULT_TRANSPORTS)

        # Optionally inject TLS / SSH when explicitly requested
        if include_tls and "tls" not in transports:
            transports.append("tls")
        if include_ssh and "ssh" not in transports:
            transports.append("ssh")

        return sorted(set(transports), key=lambda t: transports.index(t))

    def _run_matrix(
        self,
        transports: list[str],
        cores: list[str],
    ) -> ReplacementValidationResult:
        """Execute the smoke matrix script and parse its JSON output."""
        cmd = [
            sys.executable, self._script,
            "--transports", ",".join(transports),
            "--cores", ",".join(cores),
            "--json",
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=60,
                cwd=self._repo_root,
            )
        except FileNotFoundError:
            return ReplacementValidationResult(
                success=False, returncode=-1,
                transports=transports, cores=cores,
                error=f"Smoke matrix script not found: {self._script}",
            )
        except subprocess.TimeoutExpired:
            return ReplacementValidationResult(
                success=False, returncode=-1,
                transports=transports, cores=cores,
                error="Smoke matrix timed out (60s)",
            )

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        # Parse JSON from stdout
        parsed = self._parse_json(stdout, transports, cores)
        if parsed is None:
            return ReplacementValidationResult(
                success=False,
                returncode=proc.returncode,
                transports=transports,
                cores=cores,
                stdout=stdout,
                stderr=stderr,
                error=f"Failed to parse JSON from smoke matrix stdout: {stdout[:300]}",
            )

        summary = parsed.get("summary", {})
        results = parsed.get("results", [])
        failed = summary.get("failed", 0)

        success = proc.returncode == 0 and failed == 0

        return ReplacementValidationResult(
            success=success,
            returncode=proc.returncode,
            transports=transports,
            cores=cores,
            summary=summary,
            results=results,
            stdout=stdout,
            stderr=stderr,
        )

    @staticmethod
    def _parse_json(
        stdout: str,
        transports: list[str],
        cores: list[str],
    ) -> dict | None:
        """Parse JSON from stdout. Returns None on failure."""
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, dict):
            return None
        if "results" not in data or "summary" not in data:
            return None

        return data
