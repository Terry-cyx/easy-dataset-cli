"""Blind-test tasks — wraps /api/projects/{id}/blind-test-tasks*.

Pairwise model comparison: pick model A and model B, run them both against a
sample of ``EvalDataset`` rows, then have a human (or another LLM judge) vote
on which answer is better.

Spec/04 originally claimed "voting must use the GUI". This is **wrong**: the
``POST /vote`` endpoint is a regular HTTP route with no auth, so the CLI can
drive the entire blind-test loop end-to-end. ``vote_left`` / ``vote_right`` /
``vote_tie`` are thin convenience wrappers; ``vote_with_judge`` runs an LLM
judge call (via the model-config layer) and forwards its decision.

The route handler swaps left/right at random per question, so the CLI must
preserve the ``isSwapped`` flag when submitting the vote — the server uses it
to undo the swap and credit the right model.
"""

from __future__ import annotations

from typing import Any

from easyds.utils.backend import EasyDatasetBackend


VALID_VOTES = ("left", "right", "tie", "neither")


def list_tasks(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """GET /api/projects/{id}/blind-test-tasks."""
    return backend.get(
        f"/api/projects/{project_id}/blind-test-tasks",
        params={"page": page, "pageSize": page_size},
    )


def create_task(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    model_a: dict[str, str],
    model_b: dict[str, str],
    eval_dataset_ids: list[str],
    language: str = "zh-CN",
) -> dict[str, Any]:
    """POST /api/projects/{id}/blind-test-tasks.

    ``model_a`` / ``model_b`` are ``{"modelId": ..., "providerId": ...}``
    dicts; the server runs both against ``eval_dataset_ids`` and stores the
    answers, then waits for votes.
    """
    if not eval_dataset_ids:
        raise ValueError("eval_dataset_ids must be a non-empty list")
    body = {
        "modelA": model_a,
        "modelB": model_b,
        "evalDatasetIds": eval_dataset_ids,
        "language": language,
    }
    return backend.post(
        f"/api/projects/{project_id}/blind-test-tasks", json_body=body
    )


def get_task(
    backend: EasyDatasetBackend, project_id: str, task_id: str
) -> dict[str, Any]:
    """GET /api/projects/{id}/blind-test-tasks/{taskId} — full detail + results."""
    return backend.get(
        f"/api/projects/{project_id}/blind-test-tasks/{task_id}"
    )


def get_current(
    backend: EasyDatasetBackend, project_id: str, task_id: str
) -> dict[str, Any]:
    """GET /api/projects/{id}/blind-test-tasks/{taskId}/current.

    Returns either ``{completed: True, ...}`` or the current question with
    ``leftAnswer`` / ``rightAnswer`` and the swap flag.
    """
    return backend.get(
        f"/api/projects/{project_id}/blind-test-tasks/{task_id}/current"
    )


def get_next_question(
    backend: EasyDatasetBackend, project_id: str, task_id: str
) -> dict[str, Any]:
    """GET /api/projects/{id}/blind-test-tasks/{taskId}/question."""
    return backend.get(
        f"/api/projects/{project_id}/blind-test-tasks/{task_id}/question"
    )


def vote(
    backend: EasyDatasetBackend,
    project_id: str,
    task_id: str,
    *,
    vote_value: str,
    question_id: str,
    is_swapped: bool,
    left_answer: str,
    right_answer: str,
) -> dict[str, Any]:
    """POST /api/projects/{id}/blind-test-tasks/{taskId}/vote.

    The CLI MUST forward ``isSwapped`` and the two answer strings exactly as
    returned by ``get_current`` / ``get_next_question``. The server uses
    ``isSwapped`` to undo the random left/right swap and credit the correct
    model in the score table.
    """
    if vote_value not in VALID_VOTES:
        raise ValueError(
            f"vote must be one of {VALID_VOTES}, got {vote_value!r}"
        )
    body = {
        "vote": vote_value,
        "questionId": question_id,
        "isSwapped": is_swapped,
        "leftAnswer": left_answer,
        "rightAnswer": right_answer,
    }
    return backend.post(
        f"/api/projects/{project_id}/blind-test-tasks/{task_id}/vote",
        json_body=body,
    )


def interrupt_task(
    backend: EasyDatasetBackend, project_id: str, task_id: str
) -> dict[str, Any]:
    """PUT /api/projects/{id}/blind-test-tasks/{taskId} {action: "interrupt"}."""
    return backend.put(
        f"/api/projects/{project_id}/blind-test-tasks/{task_id}",
        json_body={"action": "interrupt"},
    )


def delete_task(
    backend: EasyDatasetBackend, project_id: str, task_id: str
) -> dict[str, Any] | None:
    """DELETE /api/projects/{id}/blind-test-tasks/{taskId}."""
    return backend.delete(
        f"/api/projects/{project_id}/blind-test-tasks/{task_id}"
    )


# ── Driver loops ──────────────────────────────────────────────────────


def run_manual_loop(
    backend: EasyDatasetBackend,
    project_id: str,
    task_id: str,
    *,
    vote_callback,
) -> dict[str, Any]:
    """Drive a blind-test task to completion using a vote callback.

    ``vote_callback(question_payload) -> str`` should return one of
    ``VALID_VOTES``. Returns a summary dict with vote counts.

    Used by the CLI's ``blind auto-vote`` command (where the callback wraps
    a judge LLM) and by the unit tests (where it's a deterministic stub).
    """
    summary = {
        "task_id": task_id,
        "votes_cast": 0,
        "by_vote": {v: 0 for v in VALID_VOTES},
        "results": [],
    }
    while True:
        current = get_current(backend, project_id, task_id)
        if not isinstance(current, dict):
            break
        if current.get("completed") is True:
            break
        # The "current" route may return either the question payload directly
        # or wrap it under "data".
        payload = current.get("data") if "data" in current and current.get("questionId") is None else current
        if not payload or not payload.get("questionId"):
            break

        decision = vote_callback(payload)
        if decision not in VALID_VOTES:
            raise ValueError(
                f"vote callback returned {decision!r}, expected one of {VALID_VOTES}"
            )

        result = vote(
            backend, project_id, task_id,
            vote_value=decision,
            question_id=payload["questionId"],
            is_swapped=bool(payload.get("isSwapped", False)),
            left_answer=payload.get("leftAnswer", ""),
            right_answer=payload.get("rightAnswer", ""),
        )
        summary["votes_cast"] += 1
        summary["by_vote"][decision] += 1
        summary["results"].append({"questionId": payload["questionId"], "vote": decision})

        # Stop loop when the server reports completion in the vote response.
        data = result.get("data") if isinstance(result, dict) else None
        if isinstance(data, dict) and data.get("isCompleted"):
            summary["final_scores"] = data.get("scores")
            break

    return summary
