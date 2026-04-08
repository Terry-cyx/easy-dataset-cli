"""Custom prompts — wraps /api/projects/{id}/custom-prompts.

Easy-Dataset stores per-project prompt overrides in a four-key tuple:
``(projectId, promptType, promptKey, language)``. The prompt files under
``easy-dataset/lib/llm/prompts/`` define the actual default prompts; this
module is purely a remote control over them.

The CLI exposes the most commonly overridden hooks via the
``KNOWN_PROMPT_TYPES`` constant. Other hook keys are still accepted — the
constant is just for autocomplete / validation suggestions.
"""

from __future__ import annotations

import re
from typing import Any

from easyds.utils.backend import EasyDatasetBackend


# Known prompt types observed in easy-dataset/lib/llm/prompts/.
# This is a hint set, NOT a hard whitelist — Easy-Dataset accepts any string.
KNOWN_PROMPT_TYPES: tuple[str, ...] = (
    "question",
    "answer",
    "newAnswer",
    "enhancedAnswer",
    "imageQuestion",
    "imageAnswer",
    "label",
    "addLabel",
    "labelRevise",
    "dataClean",
    "datasetEvaluation",
    "distillQuestions",
    "distillTags",
    "evalQuestion",
    "ga-generation",
    "llmJudge",
    "modelEvaluation",
    "multiTurnConversation",
    "optimizeCot",
)

KNOWN_LANGUAGES: tuple[str, ...] = ("zh-CN", "en", "tr")

# Template variables we know about. We don't enforce a closed set; we just
# enforce that AT LEAST ONE template variable is present unless explicitly
# overridden, since prompts without any variable are almost always a mistake.
TEMPLATE_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class TemplateValidationError(ValueError):
    """Raised when a prompt body is missing required template variables."""


def validate_template_vars(
    content: str,
    *,
    required: list[str] | None = None,
    require_at_least_one: bool = True,
) -> list[str]:
    """Verify that ``content`` contains the required ``{{var}}`` placeholders.

    Returns the sorted list of variables actually found. Raises
    ``TemplateValidationError`` if any required variable is missing, or if
    ``require_at_least_one`` is True and the content has zero placeholders.
    """
    found = sorted(set(TEMPLATE_VAR_RE.findall(content)))

    if required:
        missing = [v for v in required if v not in found]
        if missing:
            raise TemplateValidationError(
                "prompt is missing required template variable(s): "
                f"{', '.join('{{' + v + '}}' for v in missing)}.\n"
                "See FAQ: https://docs.easy-dataset.com/geng-duo/chang-jian-wen-ti — "
                "custom prompts must keep the original {{var}} placeholders."
            )

    if require_at_least_one and not found:
        raise TemplateValidationError(
            "prompt body contains no {{var}} placeholders. This is almost "
            "always a mistake — Easy-Dataset substitutes runtime data into "
            "these placeholders. If you really want a static prompt, pass "
            "--no-validate. FAQ: "
            "https://docs.easy-dataset.com/geng-duo/chang-jian-wen-ti"
        )

    return found


# ── HTTP wrappers ─────────────────────────────────────────────────────


def list_prompts(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    prompt_type: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """GET /api/projects/{id}/custom-prompts.

    Returns a dict with shape ``{customPrompts: [...], templates: [...]}``.
    """
    params: dict[str, Any] = {}
    if prompt_type:
        params["promptType"] = prompt_type
    if language:
        params["language"] = language
    result = backend.get(f"/api/projects/{project_id}/custom-prompts", params=params)
    if isinstance(result, dict):
        return result
    return {"customPrompts": result or [], "templates": []}


def get_prompt(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    prompt_type: str,
    prompt_key: str,
    language: str,
) -> dict[str, Any] | None:
    """Find a single prompt by tuple. Returns ``None`` if not customized."""
    listing = list_prompts(backend, project_id, prompt_type=prompt_type, language=language)
    for p in listing.get("customPrompts", []):
        if (
            p.get("promptType") == prompt_type
            and p.get("promptKey") == prompt_key
            and p.get("language") == language
        ):
            return p
    return None


def save_prompt(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    prompt_type: str,
    prompt_key: str,
    language: str,
    content: str,
    validate: bool = True,
    required_vars: list[str] | None = None,
) -> dict[str, Any]:
    """POST /api/projects/{id}/custom-prompts — single save.

    Validates template variables by default; pass ``validate=False`` to skip.
    """
    if validate:
        validate_template_vars(content, required=required_vars)
    body = {
        "promptType": prompt_type,
        "promptKey": prompt_key,
        "language": language,
        "content": content,
    }
    return backend.post(
        f"/api/projects/{project_id}/custom-prompts", json_body=body
    )


def batch_save_prompts(
    backend: EasyDatasetBackend,
    project_id: str,
    prompts: list[dict[str, Any]],
) -> dict[str, Any]:
    """POST /api/projects/{id}/custom-prompts with the batch ``{prompts: [...]}`` shape."""
    return backend.post(
        f"/api/projects/{project_id}/custom-prompts",
        json_body={"prompts": prompts},
    )


def delete_prompt(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    prompt_type: str,
    prompt_key: str,
    language: str,
) -> dict[str, Any] | None:
    """DELETE /api/projects/{id}/custom-prompts?promptType=&promptKey=&language=."""
    return backend.delete(
        f"/api/projects/{project_id}/custom-prompts",
        params={
            "promptType": prompt_type,
            "promptKey": prompt_key,
            "language": language,
        },
    )
