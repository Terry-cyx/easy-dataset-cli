"""Chunking + domain tree — wraps /api/projects/{id}/split + /custom-split.

Two server endpoints power chunking:

* ``POST /api/projects/{id}/split`` — LLM-driven default. Reads chunk-size
  parameters from the project's task-config (configured via
  ``project.update_config``).
* ``POST /api/projects/{id}/custom-split`` — position-based manual chunking.
  The CLI uses this for separator-driven splitting (案例 2 / 案例 4): the
  client computes the byte positions of every separator occurrence in the
  local content and posts them as ``splitPoints``. This is purely string
  indexing — no chunking algorithm is reimplemented.
"""

from __future__ import annotations

from typing import Any

from easyds.utils.backend import EasyDatasetBackend
from easyds.core import project as project_mod


VALID_STRATEGIES = ("document", "fixed", "text", "code")


def split(
    backend: EasyDatasetBackend,
    project_id: str,
    files: list[dict[str, str]] | None = None,
    model_config_id: str | None = None,
    *,
    model: dict[str, Any] | None = None,
    text_split_min: int | None = None,
    text_split_max: int | None = None,
    domain_tree_action: str = "rebuild",
    language: str | None = None,
) -> dict[str, Any]:
    """POST /api/projects/{id}/split — split uploaded files into chunks.

    The wire format ``fileNames`` is an array of OBJECTS, not strings —
    the server destructures ``{fileName, fileId}`` from each entry inside
    ``splitProjectFile``. Passing bare strings yields a 500 with
    'path argument must be of type string. Received undefined'.

    If ``text_split_min`` / ``text_split_max`` are provided, they are first
    written to the project's task-config.json via ``project.set_task_config``
    (which does GET-then-PUT on ``/tasks`` to preserve unrelated fields).
    The server then reads them when running ``splitProjectFile``.

    The server also runs domain-tree generation as a side effect via
    handleDomainTree() in lib/util/domain-tree.js, so the model_config_id is
    required for that LLM call.
    """
    if files is None:
        raise TypeError("files is required")
    if not isinstance(files, list) or not all(isinstance(f, dict) for f in files):
        raise TypeError(
            "files must be a list of dicts each containing 'fileName' and 'fileId'; "
            "the /split route destructures these from every entry"
        )
    for f in files:
        if "fileName" not in f or "fileId" not in f:
            raise ValueError(
                f"each file dict must contain 'fileName' and 'fileId'; got {f!r}"
            )

    cfg_overrides: dict[str, Any] = {}
    if text_split_min is not None:
        cfg_overrides["textSplitMinLength"] = text_split_min
    if text_split_max is not None:
        cfg_overrides["textSplitMaxLength"] = text_split_max
    if cfg_overrides:
        project_mod.set_task_config(backend, project_id, **cfg_overrides)

    body: dict[str, Any] = {
        "fileNames": files,
        "domainTreeAction": domain_tree_action,
    }
    # Server expects the FULL model config dict, not just the id (the
    # frontend reads selectedModelInfoAtom from localStorage). If the caller
    # provides ``model`` use it; otherwise fetch it from the server by
    # ``model_config_id`` for backward compatibility.
    from easyds.core import model as model_mod  # local import
    if model is not None:
        body["model"] = model
    elif model_config_id:
        body["model"] = model_mod.get_config_object(backend, project_id, model_config_id)
    if language:
        body["language"] = language
    return backend.post(f"/api/projects/{project_id}/split", json_body=body)


def resolve_file_objects(
    backend: EasyDatasetBackend, project_id: str, file_names: list[str]
) -> list[dict[str, str]]:
    """Look up the ``{fileName, fileId}`` dicts for the given file names.

    Used by the CLI ``chunks split --file NAME`` command to translate
    user-friendly filenames into the object form the server expects. Raises
    ``ValueError`` listing every name that wasn't found in the project.
    """
    from easyds.core import files as files_mod  # avoid circular
    listed = files_mod.list_files(backend, project_id)
    by_name: dict[str, dict[str, Any]] = {}
    for entry in listed:
        if isinstance(entry, dict) and entry.get("fileName"):
            by_name[entry["fileName"]] = entry
    resolved: list[dict[str, str]] = []
    missing: list[str] = []
    for name in file_names:
        match = by_name.get(name)
        if not match:
            missing.append(name)
            continue
        resolved.append({"fileName": name, "fileId": match.get("id") or match.get("fileId", "")})
    if missing:
        raise ValueError(
            f"file(s) not found in project {project_id}: {missing}. "
            f"Run 'easyds files list' to see what's uploaded."
        )
    return resolved


def compute_split_points(content: str, separator: str) -> list[dict[str, Any]]:
    """Find every occurrence of ``separator`` in ``content`` and return the
    list of split-point dicts that ``/custom-split`` expects.

    A split point is the BYTE INDEX RIGHT AFTER each separator. The chunk
    boundary cuts at that index, so the separator stays attached to the
    previous chunk and the next chunk starts cleanly. This matches Easy-
    Dataset's own ``generateCustomChunks`` slicing convention (see
    ``app/api/projects/[projectId]/custom-split/route.js``).

    Empty separators raise ``ValueError``. Returns an empty list if the
    separator never appears (the caller should treat that as "no chunking
    happened" and surface an error).
    """
    if not separator:
        raise ValueError("separator must be a non-empty string")

    points: list[dict[str, Any]] = []
    start = 0
    sep_len = len(separator)
    while True:
        idx = content.find(separator, start)
        if idx < 0:
            break
        position = idx + sep_len
        if position < len(content):
            points.append({"position": position})
        start = position
    return points


def custom_split(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    file_id: str,
    file_name: str,
    content: str,
    split_points: list[dict[str, Any]],
) -> dict[str, Any]:
    """POST /api/projects/{id}/custom-split with raw split positions."""
    body = {
        "fileId": file_id,
        "fileName": file_name,
        "content": content,
        "splitPoints": split_points,
    }
    return backend.post(f"/api/projects/{project_id}/custom-split", json_body=body)


def custom_split_by_separator(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    file_id: str,
    file_name: str,
    content: str,
    separator: str,
) -> dict[str, Any]:
    """High-level helper: separator → split_points → POST /custom-split.

    Used by ``easyds chunks split --separator``. Note that the CLI must read the
    file content from a local path (typically the same file that was uploaded
    earlier) because Easy-Dataset has no public ``GET file content`` endpoint.
    The split-point math is pure string indexing, not chunking logic.
    """
    points = compute_split_points(content, separator)
    if not points:
        raise ValueError(
            f"separator {separator!r} not found in content "
            f"({len(content)} chars). Nothing to split."
        )
    return custom_split(
        backend,
        project_id,
        file_id=file_id,
        file_name=file_name,
        content=content,
        split_points=points,
    )


def list_chunks(backend: EasyDatasetBackend, project_id: str) -> list[dict[str, Any]]:
    """GET /api/projects/{id}/split — list all chunks for the project."""
    result = backend.get(f"/api/projects/{project_id}/split")
    if isinstance(result, dict) and "chunks" in result:
        return result["chunks"]
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    return result or []


# ── Round 4: per-chunk CRUD + clean + batch edit ──────────────────────


VALID_BATCH_POSITIONS = ("start", "end")


def get_chunk(
    backend: EasyDatasetBackend, project_id: str, chunk_id: str
) -> dict[str, Any]:
    """GET /api/projects/{id}/chunks/{chunkId}."""
    return backend.get(
        f"/api/projects/{project_id}/chunks/{chunk_id}"
    )


def update_chunk(
    backend: EasyDatasetBackend,
    project_id: str,
    chunk_id: str,
    *,
    content: str,
) -> dict[str, Any]:
    """PATCH /api/projects/{id}/chunks/{chunkId} — overwrite content."""
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    return backend.patch(
        f"/api/projects/{project_id}/chunks/{chunk_id}",
        json_body={"content": content},
    )


def delete_chunk(
    backend: EasyDatasetBackend, project_id: str, chunk_id: str
) -> dict[str, Any] | None:
    """DELETE /api/projects/{id}/chunks/{chunkId}."""
    return backend.delete(
        f"/api/projects/{project_id}/chunks/{chunk_id}"
    )


def clean_chunk(
    backend: EasyDatasetBackend,
    project_id: str,
    chunk_id: str,
    *,
    model: dict[str, Any],
    language: str = "中文",
) -> dict[str, Any]:
    """POST /api/projects/{id}/chunks/{chunkId}/clean — apply cleaning prompt.

    The cleaning prompt comes from the project's ``cleanPrompt`` setting (set
    via ``easyds prompts set --type dataClean ...``); the endpoint **does not**
    accept an inline prompt. The server runs the LLM and writes back to the
    same chunk in place. Returns
    ``{chunkId, originalLength, cleanedLength, success, message}``.

    To override the prompt for one run, save the override with
    ``prompts set`` first; the server reads it on every clean call.
    """
    body = {"model": model, "language": language}
    return backend.post(
        f"/api/projects/{project_id}/chunks/{chunk_id}/clean", json_body=body
    )


def batch_edit_chunks(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    chunk_ids: list[str],
    position: str,
    content: str,
) -> dict[str, Any]:
    r"""POST /api/projects/{id}/chunks/batch-edit — prepend/append text to many.

    ``position='start'`` prepends ``content + "\n\n"``; ``position='end'``
    appends. Returns ``{success, updatedCount, message}``.
    """
    if position not in VALID_BATCH_POSITIONS:
        raise ValueError(
            f"position must be one of {VALID_BATCH_POSITIONS}, got {position!r}"
        )
    if not chunk_ids:
        raise ValueError("chunk_ids must be a non-empty list")
    return backend.post(
        f"/api/projects/{project_id}/chunks/batch-edit",
        json_body={"chunkIds": chunk_ids, "position": position, "content": content},
    )


def batch_content(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    chunk_names: list[str],
) -> dict[str, Any]:
    """POST /api/projects/{id}/chunks/batch-content — name → content lookup."""
    if not chunk_names:
        raise ValueError("chunk_names must be a non-empty list")
    return backend.post(
        f"/api/projects/{project_id}/chunks/batch-content",
        json_body={"chunkNames": chunk_names},
    )
