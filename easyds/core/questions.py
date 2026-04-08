"""Question generation — wraps /api/projects/{id}/generate-questions and /questions."""

from __future__ import annotations

from typing import Any

from easyds.utils.backend import EasyDatasetBackend


VALID_SOURCES = ("chunk", "image")


def generate(
    backend: EasyDatasetBackend,
    project_id: str,
    chunk_ids: list[str],
    model_config_id: str | None = None,
    *,
    model: dict[str, Any] | None = None,
    enable_ga_expansion: bool = False,
    language: str = "en",
    source: str = "chunk",
    image_ids: list[str] | None = None,
) -> dict[str, Any]:
    """POST /api/projects/{id}/generate-questions.

    Server-side: for ``source='chunk'`` (default) the question count per chunk =
    floor(chunkLen / questionGenerationLength). For ``source='image'`` the
    server uses the active vision model to generate VQA-style questions from
    each image — the CLI must pass a vision-type model.

    The server expects the FULL model config dict as ``model``, NOT just the
    id. Pass ``model={...}`` directly or ``model_config_id="mc1"`` to look it
    up.
    """
    from easyds.core import model as model_mod  # local import
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be one of {VALID_SOURCES}, got {source!r}")
    if model is None:
        if not model_config_id:
            raise ValueError("either model (dict) or model_config_id (str) is required")
        model = model_mod.get_config_object(backend, project_id, model_config_id)
    body: dict[str, Any] = {
        "model": model,
        "enableGaExpansion": enable_ga_expansion,
        "language": language,
        "sourceType": source,
    }
    if source == "image":
        body["imageIds"] = image_ids or []
    else:
        body["chunkIds"] = chunk_ids
    return backend.post(
        f"/api/projects/{project_id}/generate-questions", json_body=body
    )


VALID_STATUS_FILTERS = ("answered", "unanswered", "all")
VALID_SOURCE_FILTERS = ("all", "text", "image")
VALID_MATCH_MODES = ("match", "notMatch")


def list_questions(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    status: str | None = None,
    chunk_name: str | None = None,
    source_type: str | None = None,
    input_keyword: str | None = None,
    search_match_mode: str | None = None,
    page: int | None = None,
    size: int | None = None,
    selected_all: bool = False,
    all_records: bool = False,
) -> Any:
    """GET /api/projects/{id}/questions with rich filters.

    Server-supported query parameters (verified against route handler):

    * ``status``           — answered / unanswered / all
    * ``chunkName``        — filter by source chunk name
    * ``sourceType``       — all / text / image
    * ``input``            — search keyword in question text
    * ``searchMatchMode``  — match / notMatch (positive / negated search)
    * ``page`` / ``size``  — pagination
    * ``selectedAll=true`` — return only ids (for bulk operations)
    * ``all=true``         — return every question, no pagination

    With pagination params the server returns ``{items, total, page, size}``;
    without them it returns a flat list. The CLI's ``questions list`` command
    branches on the response shape.
    """
    if status is not None and status not in VALID_STATUS_FILTERS:
        raise ValueError(
            f"status must be one of {VALID_STATUS_FILTERS}, got {status!r}"
        )
    if source_type is not None and source_type not in VALID_SOURCE_FILTERS:
        raise ValueError(
            f"source_type must be one of {VALID_SOURCE_FILTERS}, got {source_type!r}"
        )
    if search_match_mode is not None and search_match_mode not in VALID_MATCH_MODES:
        raise ValueError(
            f"search_match_mode must be one of {VALID_MATCH_MODES}, got {search_match_mode!r}"
        )

    params: dict[str, Any] = {}
    if status:
        params["status"] = status
    if chunk_name:
        params["chunkName"] = chunk_name
    if source_type:
        params["sourceType"] = source_type
    if input_keyword:
        params["input"] = input_keyword
    if search_match_mode:
        params["searchMatchMode"] = search_match_mode
    if page is not None:
        params["page"] = page
    if size is not None:
        params["size"] = size
    if selected_all:
        params["selectedAll"] = "true"
    if all_records:
        params["all"] = "true"

    # Server bug: prisma.questions.findMany() requires a `take` argument, so
    # if neither pagination (page/size) nor selectedAll/all is set, the route
    # 500s with "Argument `take` is missing." Default to all=true so an
    # unconfigured `questions list` Just Works.
    if not any(k in params for k in ("page", "size", "all", "selectedAll")):
        params["all"] = "true"

    result = backend.get(
        f"/api/projects/{project_id}/questions", params=params or None
    )
    if isinstance(result, dict) and "items" in result:
        return result
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    if isinstance(result, dict) and "questions" in result:
        return result["questions"]
    return result or []


def create_question(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    question: str,
    chunk_id: str | None = None,
    label: str | None = None,
    image_id: str | None = None,
) -> dict[str, Any]:
    """POST /api/projects/{id}/questions — create one question manually.

    Either ``chunk_id`` (text source) or ``image_id`` (image source) should
    be supplied — the server auto-assigns the question to the right source
    table. ``label`` is the optional domain-tag classification.
    """
    if not question or not question.strip():
        raise ValueError("question must be a non-empty string")
    body: dict[str, Any] = {"projectId": project_id, "question": question}
    if chunk_id:
        body["chunkId"] = chunk_id
    if image_id:
        body["imageId"] = image_id
    if label:
        body["label"] = label
    return backend.post(
        f"/api/projects/{project_id}/questions", json_body=body
    )


def update_question(
    backend: EasyDatasetBackend,
    project_id: str,
    question_obj: dict[str, Any],
) -> dict[str, Any]:
    """PUT /api/projects/{id}/questions — update an existing question.

    The server expects the entire question object (with ``id``) in the body.
    Pass the row you got from ``list_questions`` after mutating fields.
    """
    if not isinstance(question_obj, dict) or "id" not in question_obj:
        raise ValueError("question_obj must be a dict containing 'id'")
    return backend.put(
        f"/api/projects/{project_id}/questions", json_body=question_obj
    )


def delete_question(
    backend: EasyDatasetBackend, project_id: str, question_id: str
) -> dict[str, Any] | None:
    """DELETE /api/projects/{id}/questions/{questionId}."""
    return backend.delete(
        f"/api/projects/{project_id}/questions/{question_id}"
    )
