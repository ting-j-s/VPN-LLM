"""Code Task Manager for LLM-Assisted Development.

Converts user requirements into structured code tasks.
Generates coding prompts for LLM-assisted development.
Does NOT automatically modify code - only generates task prompts.
"""

import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional

from ..common.logger import get_logger


logger = get_logger(__name__)


@dataclass
class CodeTask:
    """Structured code generation task.

    Attributes:
        title: Short title for the task.
        goal: Clear description of what needs to be achieved.
        files_to_modify: List of file paths to modify or create.
        constraints: List of constraints or requirements.
        acceptance_criteria: List of criteria to verify completion.
        test_commands: List of commands to run for verification.
    """
    title: str
    goal: str
    files_to_modify: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    test_commands: list[str] = field(default_factory=list)


class CodeTaskManager:
    """Manages code generation tasks.

    Converts natural language requirements into structured tasks
    with coding prompts for LLM-assisted development.
    Human confirmation required before execution (Vibe Coding).

    Usage:
        manager = CodeTaskManager()
        task = manager.create_task("Add TLS transport support")
        print(manager.render_prompt(task))
    """

    def __init__(self):
        """Initialize task manager."""
        logger.info("CodeTaskManager initialized")

    def create_task(self, requirement: str) -> CodeTask:
        """Convert a natural language requirement into a structured task.

        Args:
            requirement: Natural language description of what to implement.

        Returns:
            Structured CodeTask ready for prompt generation.
        """
        requirement = requirement.strip()

        # Generate task title from requirement
        title = self._generate_title(requirement)

        # Generate goal statement
        goal = self._generate_goal(requirement)

        # Identify files to modify based on requirement analysis
        files = self._identify_files(requirement)

        # Generate constraints
        constraints = self._generate_constraints(requirement)

        # Generate acceptance criteria
        criteria = self._generate_acceptance_criteria(requirement)

        # Generate test commands
        test_commands = self._generate_test_commands(files)

        task = CodeTask(
            title=title,
            goal=goal,
            files_to_modify=files,
            constraints=constraints,
            acceptance_criteria=criteria,
            test_commands=test_commands,
        )

        logger.info(f"Created task: {title}")
        return task

    def render_prompt(self, task: CodeTask) -> str:
        """Render a task as a markdown-formatted prompt for LLM.

        Args:
            task: The CodeTask to render.

        Returns:
            Markdown-formatted task prompt.
        """
        lines = [
            "# Code Task",
            "",
            f"## Title: {task.title}",
            "",
            f"## Goal",
            task.goal,
            "",
        ]

        if task.files_to_modify:
            lines.append("## Files to Modify")
            lines.append("")
            for f in task.files_to_modify:
                lines.append(f"- `{f}`")
            lines.append("")

        if task.constraints:
            lines.append("## Constraints")
            lines.append("")
            for c in task.constraints:
                lines.append(f"- {c}")
            lines.append("")

        if task.acceptance_criteria:
            lines.append("## Acceptance Criteria")
            lines.append("")
            for i, criteria in enumerate(task.acceptance_criteria, 1):
                lines.append(f"{i}. {criteria}")
            lines.append("")

        if task.test_commands:
            lines.append("## Test Commands")
            lines.append("")
            lines.append("Run these commands to verify the implementation:")
            lines.append("")
            for cmd in task.test_commands:
                lines.append(f"```bash")
                lines.append(cmd)
                lines.append(f"```")
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("**Important**: Do NOT automatically execute code changes.")
        lines.append("Generate the code following the constraints above.")
        lines.append("After generation, present the code for human review.")

        return "\n".join(lines)

    def _generate_title(self, requirement: str) -> str:
        """Generate a short title from requirement."""
        # Use first 60 chars of requirement, capitalized
        title = requirement[:60]
        if len(requirement) > 60:
            title = title.rstrip() + "..."
        return title.strip().capitalize()

    def _generate_goal(self, requirement: str) -> str:
        """Generate a clear goal statement."""
        return f"Implement: {requirement}"

    def _identify_files(self, requirement: str) -> list[str]:
        """Identify files to modify based on requirement keywords."""
        requirement_lower = requirement.lower()
        files = []

        # Detect transport type
        if "tls" in requirement_lower:
            files.append("src/transport/tls_transport.py")
            files.append("src/transport/factory.py")
        if "websocket" in requirement_lower:
            files.append("src/transport/websocket_transport.py")
            files.append("src/transport/factory.py")
        if "tcp" in requirement_lower:
            files.append("src/transport/tcp_transport.py")
            files.append("src/transport/factory.py")
        if "ssh" in requirement_lower:
            files.append("src/transport/ssh_transport.py")
            files.append("src/transport/factory.py")

        # Detect config
        if "config" in requirement_lower or "yaml" in requirement_lower:
            files.append("src/common/config.py")
            if "client" in requirement_lower:
                files.append("config/client.yaml")
            if "server" in requirement_lower:
                files.append("config/server.yaml")

        # Detect test
        if "test" in requirement_lower:
            # Look for test file patterns
            if "transport" in requirement_lower:
                files.append("tests/test_transport.py")

        return files

    def _generate_constraints(self, requirement: str) -> list[str]:
        """Generate constraints from requirement."""
        constraints = []

        requirement_lower = requirement.lower()

        # Add protocol constraints based on transport type
        if any(t in requirement_lower for t in ["tls", "websocket", "tcp", "ssh"]):
            constraints.append("Use 4-byte length prefix for frame framing")
            constraints.append("Handle partial reads (half-packet problem)")

        # Add mode constraints
        if "client" in requirement_lower or "server" in requirement_lower:
            constraints.append("Support both client and server modes")

        # Security constraints
        if "tls" in requirement_lower:
            constraints.append("Support certificate verification (optional for client)")
            constraints.append("Support certfile, keyfile, cafile configuration")

        # Error handling
        constraints.append("Raise TransportError with clear message on failure")
        constraints.append("Handle connection timeouts gracefully")

        return constraints

    def _generate_acceptance_criteria(self, requirement: str) -> list[str]:
        """Generate acceptance criteria."""
        criteria = []

        requirement_lower = requirement.lower()

        # Basic connectivity criteria
        criteria.append("Transport can connect in client mode")
        criteria.append("Transport can listen and accept in server mode")
        criteria.append("Data can be sent and received via transport")

        # Protocol criteria
        if "4-byte" in requirement_lower or "length prefix" in requirement_lower:
            criteria.append("All frames use 4-byte big-endian length prefix")

        # Test criteria
        criteria.append("All existing tests continue to pass")
        criteria.append("New tests cover basic functionality")

        # Error criteria
        criteria.append("Proper error messages on connection failure")
        criteria.append("Clean disconnect handling")

        return criteria

    def _generate_test_commands(self, files: list[str]) -> list[str]:
        """Generate test commands based on files to modify."""
        commands = []

        # Always include the basic test command
        commands.append("cd /data/xjr/VPN-LLM && python3 -m pytest tests/ -v --tb=short")

        # Add specific test file if transport
        transport_files = [f for f in files if "transport" in f and f.endswith(".py")]
        if transport_files:
            test_file = transport_files[0].replace("src/", "tests/test_").replace(".py", ".py")
            # Don't duplicate if same as first
            if not any("test_transport" in c for c in commands):
                commands.insert(0, f"cd /data/xjr/VPN-LLM && python3 -m pytest tests/test_transport*.py -v --tb=short")

        return commands[:2]  # Limit to 2 commands

    def __repr__(self) -> str:
        return "CodeTaskManager()"