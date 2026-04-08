"""Project CRUD — wraps /api/projects*."""

from __future__ import annotations

from typing import Any

from easyds.utils.backend import EasyDatasetBackend


def create(backend: EasyDatasetBackend, name: str, description: str = "") -> dict[str, Any]:
    """POST /api/projects — create a new project."""
    return backend.post("/api/projects", json_body={"name": name, "description": description})


def list_all(backend: EasyDatasetBackend) -> list[dict[str, Any]]:
    """GET /api/projects — list all projects."""
    result = backend.get("/api/projects")
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    return result or []


def get(backend: EasyDatasetBackend, project_id: str) -> dict[str, Any]:
    """GET /api/projects/{id}."""
    return backend.get(f"/api/projects/{project_id}")


def update(backend: EasyDatasetBackend, project_id: str, **fields: Any) -> dict[str, Any]:
    """PUT /api/projects/{id} — update name/description/defaultModelConfigId.

    The server only defines GET/PUT/DELETE on this route (no PATCH). PUT
    accepts a partial body — at minimum either ``name`` or
    ``defaultModelConfigId`` must be present (defaultModelConfigId may be
    None to clear it). Other prompt fields go through ``/custom-prompts``.
    """
    return backend.put(f"/api/projects/{project_id}", json_body=fields)


def set_default_model(
    backend: EasyDatasetBackend, project_id: str, model_config_id: str | None
) -> dict[str, Any]:
    """Set (or clear) ``defaultModelConfigId`` on the server.

    Required before any LLM endpoint that calls ``getActiveModel(projectId)``
    server-side — most importantly ``/batch-generateGA``, which has no way
    to receive a model from the request body. Pass ``None`` to clear.
    """
    return update(backend, project_id, defaultModelConfigId=model_config_id)


def delete(backend: EasyDatasetBackend, project_id: str) -> dict[str, Any] | None:
    """DELETE /api/projects/{id}."""
    return backend.delete(f"/api/projects/{project_id}")


def get_config(backend: EasyDatasetBackend, project_id: str) -> dict[str, Any]:
    """GET /api/projects/{id}/config — merged project + task config.

    Returns fields like ``textSplitMinLength``, ``textSplitMaxLength``,
    ``questionGenerationLength``, ``concurrencyLimit``, ``multiTurnRounds``,
    plus all project metadata.
    """
    return backend.get(f"/api/projects/{project_id}/config")


def get_task_config(backend: EasyDatasetBackend, project_id: str) -> dict[str, Any]:
    """GET /api/projects/{id}/tasks — read the project's task-config.json.

    Returns the chunking + question-generation knobs (textSplitMinLength,
    textSplitMaxLength, questionGenerationLength, concurrencyLimit, minerUToken,
    multiTurnRounds, evalQuestionTypeRatios, etc.).
    """
    return backend.get(f"/api/projects/{project_id}/tasks") or {}


def set_task_config(
    backend: EasyDatasetBackend, project_id: str, **fields: Any
) -> dict[str, Any]:
    """PUT /api/projects/{id}/tasks — merge fields into task-config.json.

    Easy-Dataset's task-config knobs (textSplitMinLength, textSplitMaxLength,
    questionGenerationLength, concurrencyLimit, etc.) live in a per-project
    JSON file, NOT in the Prisma ``Projects`` table. The PUT route at
    ``/tasks`` REPLACES the file wholesale, so we GET the current contents,
    merge the caller's overrides, then PUT the complete dict back. This
    preserves any unrelated fields (e.g. minerUToken).

    See easy-dataset/app/api/projects/[projectId]/tasks/route.js.
    """
    current = get_task_config(backend, project_id)
    if not isinstance(current, dict):
        current = {}
    merged = {**current, **fields}
    return backend.put(
        f"/api/projects/{project_id}/tasks", json_body=merged
    )
