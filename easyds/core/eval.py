"""Evaluation datasets — wraps /api/projects/{id}/eval-datasets*.

The eval-dataset model is the **benchmark** side of Easy-Dataset (separate from
the SFT ``Datasets`` table). Each row is one evaluation question with:

* ``question``       — the prompt
* ``questionType``   — 'single_choice' / 'multiple_choice' / 'true_false' /
                       'short_answer' / 'open_ended'
* ``options``        — JSON-encoded **string** (server stores stringified) for
                       choice questions; ``None`` for short_answer / open_ended
* ``correctAnswer``  — for choice questions: JSON-encoded string of the correct
                       option index/letter (or array for multiple_choice). For
                       short_answer / open_ended: free-form ground-truth text.
* ``tags``           — comma-separated string
* ``note``           — free text
* ``chunkId``        — optional FK back into the source chunk

The CLI accepts native Python lists for ``options`` / ``correctAnswer`` and
serializes them on the way out, so users never have to think about the wire
format.
"""

from __future__ import annotations

import json
from typing import Any

from easyds.utils.backend import EasyDatasetBackend


VALID_QUESTION_TYPES = (
    "single_choice",
    "multiple_choice",
    "true_false",
    "short_answer",
    "open_ended",
)

# Question types whose ``options`` / ``correctAnswer`` are JSON-encoded.
CHOICE_TYPES = {"single_choice", "multiple_choice", "true_false"}


def _encode_choice_field(value: Any) -> str | None:
    """Stringify a list/dict for the server's JSON-encoded columns."""
    if value is None:
        return None
    if isinstance(value, str):
        return value  # caller already encoded
    return json.dumps(value, ensure_ascii=False)


def _decode_choice_field(value: Any) -> Any:
    """Best-effort decode a JSON-encoded string field, returning value on failure."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return value


def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
    """Decode the JSON-string columns of one EvalDataset row in-place."""
    if not isinstance(row, dict):
        return row
    for key in ("options", "correctAnswer"):
        if key in row:
            row[key] = _decode_choice_field(row[key])
    return row


# ── CRUD ──────────────────────────────────────────────────────────────


def list_eval_datasets(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    page: int = 1,
    page_size: int = 50,
    question_type: str | None = None,
    question_types: list[str] | None = None,
    keyword: str | None = None,
    chunk_id: str | None = None,
    tags: list[str] | None = None,
    include_stats: bool = False,
) -> dict[str, Any]:
    """GET /api/projects/{id}/eval-datasets — paginated benchmark list.

    Returns the raw server response (``{items, total, page, pageSize, ...}``)
    after decoding ``options`` / ``correctAnswer`` on every item.
    """
    params: dict[str, Any] = {"page": page, "pageSize": page_size}
    if question_type:
        params["questionType"] = question_type
    if question_types:
        # The server route accepts repeated questionTypes[] params
        params["questionTypes"] = question_types
    if keyword:
        params["keyword"] = keyword
    if chunk_id:
        params["chunkId"] = chunk_id
    if tags:
        params["tags"] = tags
    if include_stats:
        params["includeStats"] = "true"

    result = backend.get(
        f"/api/projects/{project_id}/eval-datasets", params=params
    )
    if isinstance(result, dict) and "items" in result:
        for item in result["items"]:
            _decode_row(item)
    return result


def get_eval_dataset(
    backend: EasyDatasetBackend, project_id: str, eval_id: str
) -> dict[str, Any]:
    """GET /api/projects/{id}/eval-datasets/{evalId}."""
    result = backend.get(
        f"/api/projects/{project_id}/eval-datasets/{eval_id}"
    )
    return _decode_row(result) if isinstance(result, dict) else result


def create_eval_dataset(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    question: str,
    correct_answer: Any,
    question_type: str = "short_answer",
    options: list[str] | None = None,
    tags: str | list[str] | None = None,
    note: str = "",
    chunk_id: str | None = None,
) -> dict[str, Any]:
    """POST /api/projects/{id}/eval-datasets — create a single benchmark row.

    For choice-style questions (``single_choice`` / ``multiple_choice`` /
    ``true_false``), pass ``options`` as a Python list and ``correct_answer``
    as either an int / str (single) or a list (multiple). The function
    JSON-encodes them to match the server's storage format.
    """
    if question_type not in VALID_QUESTION_TYPES:
        raise ValueError(
            f"question_type must be one of {VALID_QUESTION_TYPES}, "
            f"got {question_type!r}"
        )
    if question_type in CHOICE_TYPES and not options:
        raise ValueError(
            f"question_type={question_type!r} requires --options"
        )

    body: dict[str, Any] = {
        "question": question,
        "questionType": question_type,
        "correctAnswer": _encode_choice_field(correct_answer)
        if question_type in CHOICE_TYPES
        else correct_answer,
        "note": note,
    }
    if options is not None:
        body["options"] = _encode_choice_field(options)
    if tags is not None:
        body["tags"] = ",".join(tags) if isinstance(tags, list) else tags
    if chunk_id:
        body["chunkId"] = chunk_id

    return backend.post(
        f"/api/projects/{project_id}/eval-datasets", json_body=body
    )


def update_eval_dataset(
    backend: EasyDatasetBackend,
    project_id: str,
    eval_id: str,
    *,
    question: str | None = None,
    options: list[str] | None = None,
    correct_answer: Any = None,
    tags: str | list[str] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """PUT /api/projects/{id}/eval-datasets/{evalId}."""
    body: dict[str, Any] = {}
    if question is not None:
        body["question"] = question
    if options is not None:
        body["options"] = _encode_choice_field(options)
    if correct_answer is not None:
        body["correctAnswer"] = _encode_choice_field(correct_answer)
    if tags is not None:
        body["tags"] = ",".join(tags) if isinstance(tags, list) else tags
    if note is not None:
        body["note"] = note
    return backend.put(
        f"/api/projects/{project_id}/eval-datasets/{eval_id}", json_body=body
    )


def delete_eval_dataset(
    backend: EasyDatasetBackend, project_id: str, eval_id: str
) -> dict[str, Any] | None:
    """DELETE /api/projects/{id}/eval-datasets/{evalId}."""
    return backend.delete(
        f"/api/projects/{project_id}/eval-datasets/{eval_id}"
    )


def delete_many(
    backend: EasyDatasetBackend, project_id: str, ids: list[str]
) -> dict[str, Any] | None:
    """DELETE /api/projects/{id}/eval-datasets — bulk delete by ids list."""
    return backend.delete(
        f"/api/projects/{project_id}/eval-datasets", json_body={"ids": ids}
    )


# ── Sampling / counting / tags ────────────────────────────────────────


def sample(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    question_type: str | None = None,
    question_types: list[str] | None = None,
    keyword: str | None = None,
    chunk_id: str | None = None,
    tags: list[str] | None = None,
    limit: int = 50,
    strategy: str = "random",
) -> dict[str, Any]:
    """POST /api/projects/{id}/eval-datasets/sample — pick a subset.

    Used by ``eval-task run`` and ``blind run`` to grab a fresh sample of
    benchmark rows without dragging in the whole table.
    """
    body: dict[str, Any] = {"limit": limit, "strategy": strategy}
    if question_type:
        body["questionType"] = question_type
    if question_types:
        body["questionTypes"] = question_types
    if keyword:
        body["keyword"] = keyword
    if chunk_id:
        body["chunkId"] = chunk_id
    if tags:
        body["tags"] = tags
    return backend.post(
        f"/api/projects/{project_id}/eval-datasets/sample", json_body=body
    )


def count(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    question_type: str | None = None,
    question_types: list[str] | None = None,
    keyword: str | None = None,
    chunk_id: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """GET /api/projects/{id}/eval-datasets/count — type breakdown without rows."""
    params: dict[str, Any] = {}
    if question_type:
        params["questionType"] = question_type
    if question_types:
        params["questionTypes"] = question_types
    if keyword:
        params["keyword"] = keyword
    if chunk_id:
        params["chunkId"] = chunk_id
    if tags:
        params["tags"] = tags
    return backend.get(
        f"/api/projects/{project_id}/eval-datasets/count", params=params
    )


def list_tags(
    backend: EasyDatasetBackend, project_id: str
) -> list[str]:
    """GET /api/projects/{id}/eval-datasets/tags."""
    result = backend.get(f"/api/projects/{project_id}/eval-datasets/tags")
    if isinstance(result, dict) and "tags" in result:
        return result["tags"]
    return result or []


# ── Import / export ───────────────────────────────────────────────────


VALID_EXPORT_FORMATS = ("json", "jsonl", "csv")


def export(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    output_path: str,
    fmt: str = "json",
    question_types: list[str] | None = None,
    tags: list[str] | None = None,
    keyword: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """POST /api/projects/{id}/eval-datasets/export — server returns a file.

    Unlike the SFT ``datasets/export`` route, this one **really** supports
    json / jsonl / csv server-side and writes the file as a stream for
    >1000 rows. The CLI just forwards the bytes.
    """
    import os

    if fmt not in VALID_EXPORT_FORMATS:
        raise ValueError(
            f"format must be one of {VALID_EXPORT_FORMATS}, got {fmt!r}"
        )
    if os.path.exists(output_path) and not overwrite:
        raise FileExistsError(
            f"{output_path} already exists. Pass --overwrite to replace it."
        )

    body: dict[str, Any] = {"format": fmt}
    if question_types:
        body["questionTypes"] = question_types
    if tags:
        body["tags"] = tags
    if keyword:
        body["keyword"] = keyword

    raw = backend.post_raw(
        f"/api/projects/{project_id}/eval-datasets/export", json_body=body
    )
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    with open(output_path, "wb") as fh:
        fh.write(raw)
    return {
        "output": os.path.abspath(output_path),
        "format": fmt,
        "size": os.path.getsize(output_path),
        "kind": "eval-dataset",
    }


def import_file(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    file_path: str,
    question_type: str,
    tags: str | list[str] | None = None,
) -> dict[str, Any]:
    """POST /api/projects/{id}/eval-datasets/import — multipart upload.

    The server parses json/jsonl/csv based on the file extension. The CLI
    just streams the bytes through.
    """
    import os

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    if question_type not in VALID_QUESTION_TYPES:
        raise ValueError(
            f"question_type must be one of {VALID_QUESTION_TYPES}, "
            f"got {question_type!r}"
        )

    file_name = os.path.basename(file_path)
    extra: dict[str, str] = {"questionType": question_type}
    if tags:
        extra["tags"] = ",".join(tags) if isinstance(tags, list) else tags

    with open(file_path, "rb") as fh:
        files = {"file": (file_name, fh.read(), "application/octet-stream")}
        return backend.post_multipart(
            f"/api/projects/{project_id}/eval-datasets/import",
            files=files,
            data=extra,
        )


# ── Cross-table helpers (SFT → eval) ──────────────────────────────────


def copy_from_dataset(
    backend: EasyDatasetBackend,
    project_id: str,
    dataset_id: str,
) -> dict[str, Any]:
    """POST /api/projects/{id}/datasets/{datasetId}/copy-to-eval.

    Promotes one SFT dataset row into an eval-dataset row. Useful for seeding
    a benchmark out of high-quality answered questions.
    """
    return backend.post(
        f"/api/projects/{project_id}/datasets/{dataset_id}/copy-to-eval",
        json_body={},
    )


def generate_variant(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    dataset_id: str,
    model: dict[str, Any],
    question_type: str = "single_choice",
    count: int = 3,
    language: str = "zh-CN",
) -> dict[str, Any]:
    """POST /api/projects/{id}/datasets/generate-eval-variant.

    Asks the LLM to derive ``count`` evaluation-style variants from one SFT
    dataset row (e.g. turn a free-form Q/A into a single-choice question).
    """
    if question_type not in VALID_QUESTION_TYPES:
        raise ValueError(
            f"question_type must be one of {VALID_QUESTION_TYPES}, "
            f"got {question_type!r}"
        )
    body = {
        "datasetId": dataset_id,
        "model": model,
        "questionType": question_type,
        "count": count,
        "language": language,
    }
    return backend.post(
        f"/api/projects/{project_id}/datasets/generate-eval-variant",
        json_body=body,
    )
