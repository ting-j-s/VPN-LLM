"""Validation Runner for LLM Agent tasks.

Runs shell commands and captures structured results.
Does NOT exit the Python process on failure.
"""

import subprocess
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    """Structured result of a command execution."""

    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.returncode == 0


class ValidationRunner:
    """Runs validation commands and returns structured results."""

    @staticmethod
    def run_command(cmd: str, timeout: int = 120) -> ValidationResult:
        """Run a shell command and capture its output.

        Args:
            cmd: The shell command string to execute.
            timeout: Maximum execution time in seconds.

        Returns:
            ValidationResult with returncode, stdout, and stderr.
        """
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return ValidationResult(
                command=cmd,
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
        except subprocess.TimeoutExpired as e:
            return ValidationResult(
                command=cmd,
                returncode=-1,
                stdout=e.stdout.decode("utf-8", errors="replace") if e.stdout else "",
                stderr=e.stderr.decode("utf-8", errors="replace") if e.stderr else f"Command timed out after {timeout}s",
            )

    def run_compileall(self) -> ValidationResult:
        """Run Python compileall to check syntax of all source and test files."""
        return self.run_command("python3 -m compileall src tests")

    def run_targeted_tests(self, test_paths: list[str]) -> ValidationResult:
        """Run pytest on specific test files."""
        paths = " ".join(test_paths)
        return self.run_command(f"python3 -m pytest {paths} -v")

    def run_full_tests(self) -> ValidationResult:
        """Run the full test suite."""
        return self.run_command("python3 -m pytest tests/ -v", timeout=300)

    def run_git_status(self) -> ValidationResult:
        """Run git status --short to see what files have changed."""
        return self.run_command("git status --short")
