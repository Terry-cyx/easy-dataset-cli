"""Answer generation + dataset CRUD — wraps /api/projects/{id}/datasets."""

from __future__ import annotations

import csv
import json
import os
from typing import Any

from easyds.utils.backend import EasyDatasetBackend


def generate(
    backend: EasyDatasetBackend,
    project_id: str,
    question_id: str,
    model_config_id: str | None = None,
    *,
    model: dict[str, Any] | None = None,
    language: str = "en",
) -> dict[str, Any]:
    """POST /api/projects/{id}/datasets — generate {answer, cot} for one question.

    The server expects the FULL model config dict for ``model``. Pass either
    ``model={"providerId":..., "endpoint":..., ...}`` directly or
    ``model_config_id="mc1"`` and the function will fetch the dict by id.
    """
    from easyds.core import model as model_mod  # local import
    if model is None:
        if not model_config_id:
            raise ValueError("either model (dict) or model_config_id (str) is required")
        model = model_mod.get_config_object(backend, project_id, model_config_id)
    body = {"questionId": question_id, "model": model, "language": language}
    return backend.post(f"/api/projects/{project_id}/datasets", json_body=body)


def _format_score_range(
    score_gte: float | None, score_lte: float | None
) -> str | None:
    """Build the ``scoreRange`` query value the server expects, e.g. '4-5' or '0-3'."""
    if score_gte is None and score_lte is None:
        return None
    lo = 0 if score_gte is None else score_gte
    hi = 5 if score_lte is None else score_lte
    return f"{lo}-{hi}"


def list_datasets(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    confirmed: bool | None = None,
    score_gte: float | None = None,
    score_lte: float | None = None,
    status: str | None = None,
    custom_tag: str | None = None,
    note_keyword: str | None = None,
    chunk_name: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> list[dict[str, Any]]:
    """GET /api/projects/{id}/datasets with optional filters.

    The Easy-Dataset GET route accepts these query parameters (see
    ``app/api/projects/[projectId]/datasets/route.js``):

    * ``page``, ``size`` — pagination
    * ``status`` — confirmed/unconfirmed/all
    * ``scoreRange`` — string like ``"4-5"``
    * ``customTag``, ``noteKeyword``, ``chunkName`` — text filters
    """
    params: dict[str, Any] = {"page": page, "size": page_size}
    if confirmed is True:
        params["status"] = "confirmed"
    elif confirmed is False:
        params["status"] = "unconfirmed"
    elif status:
        params["status"] = status

    score_range = _format_score_range(score_gte, score_lte)
    if score_range:
        params["scoreRange"] = score_range
    if custom_tag:
        params["customTag"] = custom_tag
    if note_keyword:
        params["noteKeyword"] = note_keyword
    if chunk_name:
        params["chunkName"] = chunk_name

    result = backend.get(f"/api/projects/{project_id}/datasets", params=params)
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    if isinstance(result, dict) and "datasets" in result:
        return result["datasets"]
    return result or []


def update(
    backend: EasyDatasetBackend,
    project_id: str,
    dataset_id: str,
    **fields: Any,
) -> dict[str, Any]:
    """PUT /api/projects/{id}/datasets/{datasetId} — confirm, score, tag, etc."""
    return backend.put(
        f"/api/projects/{project_id}/datasets/{dataset_id}", json_body=fields
    )


# ── Quality evaluation ─────────────────────────────────────────────────


def evaluate(
    backend: EasyDatasetBackend,
    project_id: str,
    dataset_id: str,
    *,
    model: dict[str, Any],
    language: str = "zh-CN",
) -> dict[str, Any]:
    """POST /api/projects/{id}/datasets/{datasetId}/evaluate.

    ``model`` is the *full* model config object Easy-Dataset expects (with at
    least ``modelId``, ``providerId``, ``endpoint``, ``apiKey``). When called
    from the CLI we usually fetch the active model config and pass it through
    verbatim.
    """
    body = {"model": model, "language": language}
    return backend.post(
        f"/api/projects/{project_id}/datasets/{dataset_id}/evaluate", json_body=body
    )


def batch_evaluate(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    model: dict[str, Any],
    language: str = "zh-CN",
) -> dict[str, Any]:
    """POST /api/projects/{id}/datasets/batch-evaluate — kicks off an async task.

    Returns ``{success, message, data: {taskId}}``. The actual progress is
    tracked in the server's Task table; the CLI just reports the task id.
    """
    body = {"model": model, "language": language}
    return backend.post(
        f"/api/projects/{project_id}/datasets/batch-evaluate", json_body=body
    )


# ── Multi-turn dialogue datasets ───────────────────────────────────────


def generate_multi_turn(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    question_id: str,
    model: dict[str, Any],
    system_prompt: str = "",
    scenario: str = "",
    rounds: int = 3,
    role_a: str = "用户",
    role_b: str = "助手",
    language: str = "中文",
) -> dict[str, Any]:
    """POST /api/projects/{id}/dataset-conversations.

    Generates a multi-turn dialogue dataset for a single question. The body
    shape mirrors the server route exactly (see
    ``app/api/projects/[projectId]/dataset-conversations/route.js``).

    Reproduces spec/03 §案例 3 (爱因斯坦给初中生讲相对论 multi-turn corpus).
    """
    body = {
        "questionId": question_id,
        "systemPrompt": system_prompt,
        "scenario": scenario,
        "rounds": rounds,
        "roleA": role_a,
        "roleB": role_b,
        "model": model,
        "language": language,
    }
    return backend.post(
        f"/api/projects/{project_id}/dataset-conversations", json_body=body
    )


def list_conversations(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    page: int = 1,
    page_size: int = 50,
    role_a: str | None = None,
    role_b: str | None = None,
    keyword: str | None = None,
) -> list[dict[str, Any]]:
    """GET /api/projects/{id}/dataset-conversations."""
    params: dict[str, Any] = {"page": page, "pageSize": page_size}
    if role_a:
        params["roleA"] = role_a
    if role_b:
        params["roleB"] = role_b
    if keyword:
        params["keyword"] = keyword
    result = backend.get(
        f"/api/projects/{project_id}/dataset-conversations", params=params
    )
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    if isinstance(result, dict) and "conversations" in result:
        return result["conversations"]
    return result or []


# ── Round 4: import + optimize ────────────────────────────────────────


def load_records_from_file(
    path: str, *, mapping: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Parse a JSON/JSONL/CSV file into a record list, applying field renaming.

    The format is detected by file extension:

    * ``.json``  → expects a JSON array of objects
    * ``.jsonl`` → one JSON object per line
    * ``.csv``   → ``DictReader`` (header row required)

    ``mapping`` is a ``{source_key: target_key}`` dict applied to every record
    *after* parsing — used by the CLI's ``datasets import --mapping`` flag so
    a file with non-standard column names can be loaded without editing it.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")
    ext = os.path.splitext(path)[1].lower()

    if ext == ".json":
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise ValueError(f"{path} must be a JSON array of objects, got {type(data).__name__}")
        records = data
    elif ext in (".jsonl", ".ndjson"):
        records = []
        with open(path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise ValueError(f"{path}:{lineno} is not valid JSON: {e}") from e
    elif ext == ".csv":
        with open(path, "r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            records = [dict(row) for row in reader]
    else:
        raise ValueError(
            f"Unsupported file type {ext!r}; expected .json / .jsonl / .csv"
        )

    if mapping:
        records = [_apply_mapping(rec, mapping) for rec in records]

    # Filter out rows that lack the required {question, answer} after mapping;
    # the server will reject them anyway, and a noisy CLI error is friendlier.
    cleaned = [
        rec for rec in records
        if isinstance(rec, dict) and rec.get("question") and rec.get("answer")
    ]
    return cleaned


def _apply_mapping(record: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    """Rename ``record`` keys per ``mapping``; unmatched keys pass through."""
    if not isinstance(record, dict):
        return record
    return {mapping.get(k, k): v for k, v in record.items()}


def import_records(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """POST /api/projects/{id}/datasets/import — bulk insert pre-baked records.

    The server expects the request body to look like ``{datasets: [...]}``,
    not multipart. Each record needs ``question`` + ``answer`` at minimum;
    optional fields (``cot``, ``chunkName``, ``confirmed``, ``score``,
    ``tags``, ``note``, ``other``) get sensible defaults.

    Returns the server's count summary
    ``{success, total, failed, skipped, errors, sourceInfo}``.
    """
    if not isinstance(records, list):
        raise ValueError("records must be a list of dicts")
    if not records:
        raise ValueError("records is empty — nothing to import")
    return backend.post(
        f"/api/projects/{project_id}/datasets/import",
        json_body={"datasets": records},
    )


def optimize(
    backend: EasyDatasetBackend,
    project_id: str,
    dataset_id: str,
    *,
    advice: str,
    model: dict[str, Any],
    language: str = "zh-CN",
) -> dict[str, Any]:
    """POST /api/projects/{id}/datasets/optimize — re-generate one answer with advice.

    The server treats ``advice`` as the user's instruction for how to polish
    the answer (e.g. "make it more concise", "add a worked example"). It
    re-runs the LLM with ``getNewAnswerPrompt`` and writes back to the same
    dataset row.

    This is the API behind the GUI's "magic wand" button (魔法棒, G4 in
    spec/04). Single-row only — there is no batch endpoint.
    """
    if not advice or not advice.strip():
        raise ValueError("advice must be a non-empty string")
    body = {
        "datasetId": dataset_id,
        "advice": advice,
        "model": model,
        "language": language,
    }
    return backend.post(
        f"/api/projects/{project_id}/datasets/optimize", json_body=body
    )
