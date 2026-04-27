"""Code Task Manager for LLM-Assisted Development.

Manages code generation tasks and tracks implementation progress.
Coordinates between user requirements and LLM code generation.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from ..common.logger import setup_logger


logger = setup_logger(__name__)


class TaskStatus(Enum):
    """Task status enumeration."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class CodeTask:
    """Code generation task.

    Attributes:
        task_id: Unique task identifier.
        description: Task description.
        module_name: Target module name.
        status: Current task status.
        generated_code: Generated code (if completed).
        error_message: Error message (if failed).
        created_at: Creation timestamp.
        completed_at: Completion timestamp.
    """
    task_id: str
    description: str
    module_name: str
    status: TaskStatus = TaskStatus.PENDING
    generated_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


class CodeTaskManager:
    """Manages code generation tasks for LLM-assisted development.

    Provides:
    - Task creation and tracking
    - Task status updates
    - Generated code storage
    - Task history
    """

    def __init__(self, storage_dir: str = ".llm_tasks"):
        """Initialize task manager.

        Args:
            storage_dir: Directory for task storage.
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(exist_ok=True)

        self._tasks: dict[str, CodeTask] = {}
        logger.info(f"Task manager initialized (storage={self.storage_dir})")

    def create_task(
        self,
        description: str,
        module_name: str,
    ) -> CodeTask:
        """Create a new code generation task.

        Args:
            description: Description of code to generate.
            module_name: Target module name.

        Returns:
            Created task object.
        """
        # Generate task ID from description hash
        task_id = hashlib.sha256(
            f"{description}{module_name}{time.time()}".encode()
        ).hexdigest()[:16]

        task = CodeTask(
            task_id=task_id,
            description=description,
            module_name=module_name,
        )

        self._tasks[task_id] = task
        self._save_task(task)

        logger.info(f"Created task {task_id}: {module_name}")
        return task

    def get_task(self, task_id: str) -> Optional[CodeTask]:
        """Retrieve a task by ID.

        Args:
            task_id: Task identifier.

        Returns:
            Task object, or None if not found.
        """
        if task_id in self._tasks:
            return self._tasks[task_id]

        # Try loading from storage
        task_file = self.storage_dir / f"{task_id}.json"
        if task_file.exists():
            with open(task_file) as f:
                data = json.load(f)
                task = CodeTask(**data)
                task.status = TaskStatus(task.status)
                self._tasks[task_id] = task
                return task

        return None

    def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        generated_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Update task status and results.

        Args:
            task_id: Task identifier.
            status: New status.
            generated_code: Generated code (for completed tasks).
            error_message: Error message (for failed tasks).
        """
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        task.status = status
        if generated_code is not None:
            task.generated_code = generated_code
        if error_message is not None:
            task.error_message = error_message
        if status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            task.completed_at = time.time()

        self._save_task(task)
        logger.info(f"Updated task {task_id}: {status.value}")

    def list_tasks(self, status: Optional[TaskStatus] = None) -> list[CodeTask]:
        """List all tasks, optionally filtered by status.

        Args:
            status: Optional status filter.

        Returns:
            List of tasks.
        """
        tasks = list(self._tasks.values())
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.created_at)

    def _save_task(self, task: CodeTask) -> None:
        """Save task to storage.

        Args:
            task: Task to save.
        """
        task_file = self.storage_dir / f"{task.task_id}.json"
        with open(task_file, "w") as f:
            data = {
                "task_id": task.task_id,
                "description": task.description,
                "module_name": task.module_name,
                "status": task.status.value,
                "generated_code": task.generated_code,
                "error_message": task.error_message,
                "created_at": task.created_at,
                "completed_at": task.completed_at,
            }
            json.dump(data, f, indent=2)

    def __repr__(self) -> str:
        return f"CodeTaskManager(tasks={len(self._tasks)})"
