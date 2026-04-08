"""Genre-Audience (GA / MGA) pairs — wraps /api/projects/{id}/...ga.../*.

Easy-Dataset's MGA expansion lets you generate **5 distinct (genre, audience)
pairs** per source file. Each subsequent question generation can be cross-
multiplied with these pairs to produce richer, less-repetitive data — that's
where the "MGA" name comes from.

Endpoints (verified against the route handlers):

* ``POST /api/projects/{id}/batch-generateGA``
    body ``{fileIds, modelConfigId, language, appendMode}``
* ``POST /api/projects/{id}/batch-add-manual-ga``
    body ``{fileIds, gaPair: {genreTitle, audienceTitle, ...}, appendMode}``
* ``GET / POST / PUT / PATCH /api/projects/{id}/files/{fileId}/ga-pairs``
    file-scoped CRUD + activation toggle

**Things spec/04 was wrong about** (and how this module deals with them):

* "strict vs loose mode": **the server has no such mode**. The prompt is
  fixed. We accept ``--mode`` for forward-compatibility but log a warning
  saying it has no server effect.
* "Token-estimation endpoint with 3.9× inflation warning": **does not exist**
  on the server for GA. There's only ``/datasets/{id}/token-count`` for
  per-row SFT estimation. We provide a *client-side* approximator
  (``estimate_inflation``) that uses the documented constant (5 pairs per
  file × ~3.9× expansion) without calling any server endpoint.
* "``count`` / ``--pairs-per-file``": **fixed at 5** in the prompt and the DB
  unique constraint ``(fileId, pairNumber)`` where ``pairNumber ∈ 1..5``.
  No CLI flag for this.
"""

from __future__ import annotations

from typing import Any

from easyds.utils.backend import EasyDatasetBackend


# Per the prompt + DB schema, every file has exactly five pairs.
PAIRS_PER_FILE = 5

# Documented in the official MGA docs as "up to ~3.9× token inflation".
DEFAULT_INFLATION_FACTOR = 3.9

# Recognized but server-side no-ops; we keep them for forward-compat
# and emit a warning at the CLI layer.
KNOWN_MODES = ("strict", "loose")


def batch_generate(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    file_ids: list[str],
    model_config_id: str,
    language: str = "中文",
    append_mode: bool = False,
) -> dict[str, Any]:
    """POST /api/projects/{id}/batch-generateGA — generate 5 GA pairs per file.

    ``append_mode=False`` overwrites existing pairs; ``True`` adds to them.
    The server enforces a max of 5 pairs per file via DB unique constraint.
    """
    if not file_ids:
        raise ValueError("file_ids must be a non-empty list")
    body = {
        "fileIds": file_ids,
        "modelConfigId": model_config_id,
        "language": language,
        "appendMode": append_mode,
    }
    return backend.post(
        f"/api/projects/{project_id}/batch-generateGA", json_body=body
    )


def add_manual(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    file_ids: list[str],
    genre_title: str,
    audience_title: str,
    genre_desc: str = "",
    audience_desc: str = "",
    append_mode: bool = True,
) -> dict[str, Any]:
    """POST /api/projects/{id}/batch-add-manual-ga — attach a hand-written GA pair.

    Defaults to ``append_mode=True`` because manual additions are almost
    always meant to *augment* the LLM-generated set, not replace it.
    """
    if not file_ids:
        raise ValueError("file_ids must be a non-empty list")
    if not genre_title or not audience_title:
        raise ValueError("genre_title and audience_title are required")
    body = {
        "fileIds": file_ids,
        "appendMode": append_mode,
        "gaPair": {
            "genreTitle": genre_title,
            "genreDesc": genre_desc,
            "audienceTitle": audience_title,
            "audienceDesc": audience_desc,
        },
    }
    return backend.post(
        f"/api/projects/{project_id}/batch-add-manual-ga", json_body=body
    )


def list_pairs(
    backend: EasyDatasetBackend, project_id: str, file_id: str
) -> list[dict[str, Any]]:
    """GET /api/projects/{id}/files/{fileId}/ga-pairs."""
    result = backend.get(
        f"/api/projects/{project_id}/files/{file_id}/ga-pairs"
    )
    if isinstance(result, dict) and "data" in result:
        return result["data"] or []
    return result or []


def generate_for_file(
    backend: EasyDatasetBackend,
    project_id: str,
    file_id: str,
    *,
    language: str = "中文",
    regenerate: bool = False,
    append_mode: bool = False,
) -> dict[str, Any]:
    """POST /api/projects/{id}/files/{fileId}/ga-pairs — single-file generate."""
    body: dict[str, Any] = {
        "language": language,
        "regenerate": regenerate,
        "appendMode": append_mode,
    }
    return backend.post(
        f"/api/projects/{project_id}/files/{file_id}/ga-pairs", json_body=body
    )


def update_pairs(
    backend: EasyDatasetBackend,
    project_id: str,
    file_id: str,
    *,
    updates: list[dict[str, Any]],
) -> dict[str, Any]:
    """PUT /api/projects/{id}/files/{fileId}/ga-pairs — bulk edit titles/descs."""
    return backend.put(
        f"/api/projects/{project_id}/files/{file_id}/ga-pairs",
        json_body={"updates": updates},
    )


def set_active(
    backend: EasyDatasetBackend,
    project_id: str,
    file_id: str,
    *,
    ga_pair_id: str,
    is_active: bool,
) -> dict[str, Any]:
    """PATCH /api/projects/{id}/files/{fileId}/ga-pairs — toggle one pair."""
    return backend.patch(
        f"/api/projects/{project_id}/files/{file_id}/ga-pairs",
        json_body={"gaPairId": ga_pair_id, "isActive": is_active},
    )


# ── Client-side helpers ──────────────────────────────────────────────


def estimate_inflation(
    *,
    file_count: int,
    base_question_count: int,
    inflation_factor: float = DEFAULT_INFLATION_FACTOR,
) -> dict[str, Any]:
    """Estimate the question-count blow-up before actually running batch GA.

    This is **purely client-side arithmetic** — there is no server endpoint
    for token estimation in the GA pipeline. We use the documented constants:

    * Each file produces ``PAIRS_PER_FILE`` (5) GA pairs.
    * Every subsequent question is generated once per active GA pair, so the
      total question count is multiplied by 5 in the worst case.
    * The official docs warn of up to ~3.9× *token* inflation (not row count).

    Returns a dict with both the row inflation (≤5×) and the token inflation
    estimate (≤``inflation_factor``×). The CLI surfaces this as a warning so
    AI agents don't accidentally bankrupt themselves on a long document.
    """
    if file_count < 0 or base_question_count < 0:
        raise ValueError("file_count and base_question_count must be ≥ 0")
    return {
        "files": file_count,
        "pairs_per_file": PAIRS_PER_FILE,
        "max_pairs_total": file_count * PAIRS_PER_FILE,
        "base_question_count": base_question_count,
        "estimated_max_questions": base_question_count * PAIRS_PER_FILE,
        "estimated_token_inflation": inflation_factor,
        "warning": (
            f"With MGA enabled, every question is regenerated once per active "
            f"GA pair (up to {PAIRS_PER_FILE}). Token usage may grow up to "
            f"{inflation_factor}× compared to a non-MGA run. This is a "
            f"client-side estimate; the server has no token-prediction endpoint."
        ),
    }
