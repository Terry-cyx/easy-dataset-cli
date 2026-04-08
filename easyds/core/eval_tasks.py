"""Automated evaluation tasks — wraps /api/projects/{id}/eval-tasks*.

An eval-task runs one or more **test models** against a sampled set of
``EvalDataset`` rows, with an optional **judge model** scoring the subjective
answers. The server runs the task asynchronously; the CLI just kicks it off,
optionally polls progress, and renders the result.

Body shape (from ``app/api/projects/[projectId]/eval-tasks/route.js``):

* ``models``               : ``[{modelId, providerId}, ...]`` — required
* ``evalDatasetIds``       : ``[str, ...]`` — required (use ``eval sample`` first)
* ``judgeModelId`` /
  ``judgeProviderId``      : optional, required if any subjective questions
* ``language``             : default 'zh-CN'
* ``filterOptions``        : optional dict
* ``customScoreAnchors``   : optional **JSON-encoded string** with custom rubric
"""

from __future__ import annotations

import json
from typing import Any

from easyds.utils.backend import EasyDatasetBackend


def list_tasks(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """GET /api/projects/{id}/eval-tasks."""
    return backend.get(
        f"/api/projects/{project_id}/eval-tasks",
        params={"page": page, "pageSize": page_size},
    )


def create_task(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    models: list[dict[str, str]],
    eval_dataset_ids: list[str],
    judge_model_id: str | None = None,
    judge_provider_id: str | None = None,
    language: str = "zh-CN",
    filter_options: dict[str, Any] | None = None,
    custom_score_anchors: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    """POST /api/projects/{id}/eval-tasks — kick off an async evaluation run.

    ``models`` is a list of ``{"modelId": ..., "providerId": ...}`` dicts.
    The server creates one Task row per test model and returns the lot.
    """
    if not models:
        raise ValueError("models must be a non-empty list")
    if not eval_dataset_ids:
        raise ValueError("eval_dataset_ids must be a non-empty list")

    body: dict[str, Any] = {
        "models": models,
        "evalDatasetIds": eval_dataset_ids,
        "language": language,
    }
    if judge_model_id:
        body["judgeModelId"] = judge_model_id
    if judge_provider_id:
        body["judgeProviderId"] = judge_provider_id
    if filter_options:
        body["filterOptions"] = filter_options
    if custom_score_anchors is not None:
        if isinstance(custom_score_anchors, (dict, list)):
            body["customScoreAnchors"] = json.dumps(
                custom_score_anchors, ensure_ascii=False
            )
        else:
            body["customScoreAnchors"] = custom_score_anchors
    return backend.post(
        f"/api/projects/{project_id}/eval-tasks", json_body=body
    )


def get_task(
    backend: EasyDatasetBackend,
    project_id: str,
    task_id: str,
    *,
    page: int = 1,
    page_size: int = 50,
    type_filter: str | None = None,
    is_correct: bool | None = None,
) -> dict[str, Any]:
    """GET /api/projects/{id}/eval-tasks/{taskId}.

    Returns the task header + paginated ``EvalResults`` rows. ``type_filter``
    narrows by question type; ``is_correct=True/False`` filters by score.
    """
    params: dict[str, Any] = {"page": page, "pageSize": page_size}
    if type_filter:
        params["type"] = type_filter
    if is_correct is not None:
        params["isCorrect"] = "true" if is_correct else "false"
    return backend.get(
        f"/api/projects/{project_id}/eval-tasks/{task_id}", params=params
    )


def interrupt_task(
    backend: EasyDatasetBackend, project_id: str, task_id: str
) -> dict[str, Any]:
    """PUT /api/projects/{id}/eval-tasks/{taskId} {action: "interrupt"}."""
    return backend.put(
        f"/api/projects/{project_id}/eval-tasks/{task_id}",
        json_body={"action": "interrupt"},
    )


def delete_task(
    backend: EasyDatasetBackend, project_id: str, task_id: str
) -> dict[str, Any] | None:
    """DELETE /api/projects/{id}/eval-tasks/{taskId}."""
    return backend.delete(
        f"/api/projects/{project_id}/eval-tasks/{task_id}"
    )
