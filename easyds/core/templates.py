"""Question templates — wraps /api/projects/{id}/questions/templates*.

Easy-Dataset's question templates let you skip per-chunk LLM question generation
by defining a fixed question + answer format that gets applied across many
chunks (or images). The schema (from
``app/api/projects/[projectId]/questions/templates/route.js``) is:

* ``question``     (str)         — the prompt that will be applied to every source
* ``sourceType``   ``image|text``— what kind of source rows the template covers
* ``answerType``   ``text|label|custom_format``
* ``description``  (str, opt)
* ``labels``       (list[str])   — required when ``answerType == 'label'``
* ``customFormat`` (str, opt)    — required when ``answerType == 'custom_format'``
* ``order``        (int, opt)
* ``autoGenerate`` (bool, opt)   — if True, server immediately materializes
                                   one Question row per matching source

This module is purely a remote control over those endpoints. The CLI uses these
to reproduce 案例 1 (image VQA with three template types) and 案例 2
(label-set sentiment classification).
"""

from __future__ import annotations

import json
from typing import Any

from easyds.utils.backend import EasyDatasetBackend


VALID_SOURCE_TYPES = ("text", "image")
VALID_ANSWER_TYPES = ("text", "label", "custom_format")


# CLI-friendly synonym for "custom_format"
ANSWER_TYPE_ALIASES = {"json-schema": "custom_format", "json": "custom_format"}


def normalize_answer_type(value: str) -> str:
    """Map CLI-friendly aliases (json-schema, json) onto the server's enum."""
    return ANSWER_TYPE_ALIASES.get(value, value)


def list_templates(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    source_type: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """GET /api/projects/{id}/questions/templates."""
    params: dict[str, Any] = {}
    if source_type:
        params["sourceType"] = source_type
    if search:
        params["search"] = search
    result = backend.get(
        f"/api/projects/{project_id}/questions/templates", params=params
    )
    if isinstance(result, dict) and "templates" in result:
        return result["templates"]
    return result or []


def create_template(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    question: str,
    source_type: str,
    answer_type: str,
    description: str = "",
    labels: list[str] | None = None,
    custom_format: str | None = None,
    order: int = 0,
    auto_generate: bool = False,
) -> dict[str, Any]:
    """POST /api/projects/{id}/questions/templates.

    Validates ``source_type`` ∈ image|text and ``answer_type`` ∈
    text|label|custom_format. When ``answer_type == 'label'``, ``labels`` must
    be a non-empty list. When ``answer_type == 'custom_format'``,
    ``custom_format`` must be a non-empty string (typically a JSON Schema
    serialized to text).
    """
    answer_type = normalize_answer_type(answer_type)
    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError(
            f"source_type must be one of {VALID_SOURCE_TYPES}, got {source_type!r}"
        )
    if answer_type not in VALID_ANSWER_TYPES:
        raise ValueError(
            f"answer_type must be one of {VALID_ANSWER_TYPES}, got {answer_type!r}"
        )
    if answer_type == "label" and not labels:
        raise ValueError("answer_type='label' requires a non-empty labels list")
    if answer_type == "custom_format" and not custom_format:
        raise ValueError(
            "answer_type='custom_format' requires --custom-format or --schema-file"
        )

    body: dict[str, Any] = {
        "question": question,
        "sourceType": source_type,
        "answerType": answer_type,
        "description": description,
        "labels": labels or [],
        "customFormat": custom_format,
        "order": order,
        "autoGenerate": auto_generate,
    }
    return backend.post(
        f"/api/projects/{project_id}/questions/templates", json_body=body
    )


def get_template(
    backend: EasyDatasetBackend, project_id: str, template_id: str
) -> dict[str, Any]:
    return backend.get(
        f"/api/projects/{project_id}/questions/templates/{template_id}"
    )


def update_template(
    backend: EasyDatasetBackend,
    project_id: str,
    template_id: str,
    **fields: Any,
) -> dict[str, Any]:
    if "answer_type" in fields:
        fields["answerType"] = normalize_answer_type(fields.pop("answer_type"))
    if "source_type" in fields:
        fields["sourceType"] = fields.pop("source_type")
    if "auto_generate" in fields:
        fields["autoGenerate"] = fields.pop("auto_generate")
    if "custom_format" in fields:
        fields["customFormat"] = fields.pop("custom_format")
    return backend.put(
        f"/api/projects/{project_id}/questions/templates/{template_id}",
        json_body=fields,
    )


def delete_template(
    backend: EasyDatasetBackend, project_id: str, template_id: str
) -> dict[str, Any] | None:
    return backend.delete(
        f"/api/projects/{project_id}/questions/templates/{template_id}"
    )


def load_schema_from_file(path: str) -> str:
    """Read a JSON Schema file and return it as a compact JSON string.

    The server stores ``customFormat`` as a string, not a parsed object, so
    callers can either pass raw text or use this helper to round-trip a JSON
    Schema document.
    """
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    # Validate it's parseable JSON; surface a clean error otherwise.
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"--schema-file {path!r} is not valid JSON: {e}") from e
    return json.dumps(parsed, ensure_ascii=False)


def parse_label_set(spec: str) -> list[str]:
    """Split '正面,负面,中性' into ['正面', '负面', '中性'], stripping whitespace."""
    return [item.strip() for item in spec.split(",") if item.strip()]
