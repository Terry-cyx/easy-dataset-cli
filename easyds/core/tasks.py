"""Background task system — wraps /api/projects/{id}/tasks{/list,/[taskId]}.

Easy-Dataset has two superficially similar things called "tasks":

* **The ``Task`` table** — long-running operations (question generation,
  answer generation, GA expansion, distillation, evaluation, ...). One row
  per kicked-off job, with a status counter and progress fields. Accessed via
  ``GET /tasks/list``, ``GET/PATCH/DELETE /tasks/[taskId]``.
* **task-config.json** — a project-scoped JSON file with chunking knobs and
  concurrency limits. Accessed via ``GET/PUT /tasks`` (no ``/list``).

This module covers the first one. ``core/project.py`` already exposes the
config file via the ``/config`` route; we don't bind to ``GET /tasks`` to
avoid the overload.

The server processes tasks in-process via ``setImmediate`` (no real worker
queue). That means a CLI client polling ``GET /tasks/[taskId]`` is the
canonical way to wait for completion — there's no streaming or websocket.
``wait_for`` implements that polling loop with a sane default backoff.
"""

from __future__ import annotations

import time
from typing import Any

from easyds.utils.backend import EasyDatasetBackend


# Status enum from lib/services/tasks/index.js
STATUS_PROCESSING = 0
STATUS_COMPLETED = 1
STATUS_FAILED = 2
STATUS_INTERRUPTED = 3

STATUS_NAMES = {
    STATUS_PROCESSING: "processing",
    STATUS_COMPLETED: "completed",
    STATUS_FAILED: "failed",
    STATUS_INTERRUPTED: "interrupted",
}

TERMINAL_STATUSES = {STATUS_COMPLETED, STATUS_FAILED, STATUS_INTERRUPTED}

# Task type enum (verified against lib/services/tasks/index.js).
TASK_TYPES = (
    "question-generation",
    "file-processing",
    "answer-generation",
    "data-cleaning",
    "dataset-evaluation",
    "multi-turn-generation",
    "data-distillation",
    "image-question-generation",
    "image-dataset-generation",
    "eval-generation",
    "model-evaluation",
)


def create_task(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    task_type: str,
    model_info: dict[str, Any] | None = None,
    note: dict[str, Any] | str | None = None,
    language: str | None = None,
    detail: str = "",
    total_count: int = 0,
) -> dict[str, Any]:
    """POST /api/projects/{id}/tasks — kick off a background task.

    The server creates the row immediately and starts ``processTask`` in the
    background. Caller polls via ``wait_for(...)`` (or ``task wait`` from the
    CLI) on the returned ``data.id``.

    ``task_type`` must be one of :data:`TASK_TYPES`. ``note`` can be a dict
    (will be JSON-stringified server-side) carrying task-specific parameters,
    e.g. ``{"chunkIds": [...]}`` for ``data-cleaning``, or
    ``{"fileList": [...], "strategy": "default"}`` for ``file-processing``.
    """
    if task_type not in TASK_TYPES:
        raise ValueError(
            f"Unknown task_type {task_type!r}; must be one of {TASK_TYPES}"
        )
    body: dict[str, Any] = {
        "taskType": task_type,
        "modelInfo": model_info or {},
        "language": language or "zh-CN",
        "detail": detail,
        "totalCount": total_count,
    }
    if note is not None:
        body["note"] = note
    return backend.post(f"/api/projects/{project_id}/tasks", json_body=body)


def list_tasks(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    task_type: str | None = None,
    status: int | None = None,
    page: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """GET /api/projects/{id}/tasks/list — paginated task list.

    The server takes ``page`` (0-indexed!) and ``limit``, plus optional
    ``taskType`` and ``status`` filters. Returns
    ``{code, data: [...], total, page, limit}``.
    """
    params: dict[str, Any] = {"page": page, "limit": limit}
    if task_type:
        params["taskType"] = task_type
    if status is not None:
        params["status"] = status
    return backend.get(
        f"/api/projects/{project_id}/tasks/list", params=params
    )


def get_task(
    backend: EasyDatasetBackend, project_id: str, task_id: str
) -> dict[str, Any]:
    """GET /api/projects/{id}/tasks/{taskId}."""
    return backend.get(
        f"/api/projects/{project_id}/tasks/{task_id}"
    )


def update_task(
    backend: EasyDatasetBackend,
    project_id: str,
    task_id: str,
    **fields: Any,
) -> dict[str, Any]:
    """PATCH /api/projects/{id}/tasks/{taskId}.

    Server accepts ``status``, ``completedCount``, ``totalCount``, ``detail``,
    ``note``, ``endTime``. Used by the CLI's ``task interrupt`` command which
    sets ``status=3`` (INTERRUPTED).
    """
    return backend.patch(
        f"/api/projects/{project_id}/tasks/{task_id}", json_body=fields
    )


def cancel_task(
    backend: EasyDatasetBackend, project_id: str, task_id: str
) -> dict[str, Any]:
    """Mark a task as INTERRUPTED via PATCH.

    Easy-Dataset has no separate cancel endpoint — interruption is just a
    status update. The processing loop checks the status before each step
    and bails out when it sees ``status=3``.
    """
    return update_task(
        backend, project_id, task_id,
        status=STATUS_INTERRUPTED,
    )


def delete_task(
    backend: EasyDatasetBackend, project_id: str, task_id: str
) -> dict[str, Any] | None:
    """DELETE /api/projects/{id}/tasks/{taskId}."""
    return backend.delete(
        f"/api/projects/{project_id}/tasks/{task_id}"
    )


def wait_for(
    backend: EasyDatasetBackend,
    project_id: str,
    task_id: str,
    *,
    poll_interval: float = 1.0,
    timeout: float = 600.0,
    sleep_func=time.sleep,
    now_func=time.monotonic,
) -> dict[str, Any]:
    """Poll a task to terminal status (completed / failed / interrupted).

    Returns the final task payload. Raises ``TimeoutError`` if ``timeout``
    seconds elapse before the task reaches a terminal status. The
    ``sleep_func`` and ``now_func`` injection points exist so unit tests can
    pump time deterministically.
    """
    deadline = now_func() + timeout
    while True:
        result = get_task(backend, project_id, task_id)
        task = (
            result.get("data") if isinstance(result, dict) and "data" in result else result
        )
        if isinstance(task, dict) and task.get("status") in TERMINAL_STATUSES:
            return task if isinstance(task, dict) else result
        if now_func() >= deadline:
            raise TimeoutError(
                f"task {task_id} did not finish within {timeout}s "
                f"(last status={(task or {}).get('status') if isinstance(task, dict) else 'unknown'})"
            )
        sleep_func(poll_interval)


def status_label(status: int | None) -> str:
    """Map a numeric status to its human-readable name."""
    if status is None:
        return "unknown"
    return STATUS_NAMES.get(status, f"status-{status}")
