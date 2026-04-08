"""Post-processing --fix handlers for 'easyds datasets eval'.

Every fix here operates **locally on the JSON file** and never calls the
Easy-Dataset server. This keeps --fix CI-safe: running it cannot burn
API credits or mutate server state. If a fix needs server-side re-runs,
the agent must explicitly invoke the corresponding easyds command.

All fixes return ``(new_records, summary)`` where ``summary`` is a dict
describing what changed. Callers are responsible for writing the result
back to disk.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def load_records(path: str | Path) -> tuple[list[dict[str, Any]], str]:
    """Load an eval target file. Supports .json (array) and .jsonl.

    Returns ``(records, file_type)``. ``file_type`` is ``"json"`` or
    ``"jsonl"``.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".jsonl":
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
        return records, "jsonl"
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(
            f"{path} is not a JSON array (got {type(data).__name__}). "
            "datasets eval expects a list of records."
        )
    return data, "json"


def write_records(
    path: str | Path, records: list[dict[str, Any]], file_type: str
) -> None:
    p = Path(path)
    if file_type == "jsonl":
        p.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
            encoding="utf-8",
        )
    else:
        p.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ── Fix: chunk-join ────────────────────────────────────────────────────
def fix_chunk_join(
    records: list[dict[str, Any]],
    chunks_file: str | Path,
    *,
    input_field: str = "input",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Join each record's source chunk content back into the ``input`` field.

    Designed for the case-2 failure mode: label-template classification
    tasks where the reviewed text lives in ``chunks[].content`` but the
    dataset row has empty ``chunkContent`` and the export therefore
    emits empty ``input``. Matches records to chunks by ``chunkName``.

    The chunks file should be the output of ``easyds chunks list``
    (a JSON array of chunk dicts with at least ``name`` and ``content``).
    """
    chunks_path = Path(chunks_file)
    try:
        chunks_raw = json.loads(chunks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"cannot read chunks file {chunks_file}: {e}")

    if not isinstance(chunks_raw, list):
        raise ValueError(
            f"{chunks_file} is not a JSON array of chunk dicts "
            "(expected output of 'easyds chunks list')."
        )

    # Support both {name, content} (server response) and pre-flattened shapes.
    chunk_by_name: dict[str, str] = {}
    for c in chunks_raw:
        if not isinstance(c, dict):
            continue
        name = c.get("name") or c.get("chunkName")
        content = c.get("content") or c.get("chunkContent") or ""
        if name:
            chunk_by_name[name] = content.strip()

    updated = 0
    missing: list[str] = []
    new_records: list[dict[str, Any]] = []
    for r in records:
        r = dict(r)
        # Target field is empty and we can find a matching chunk
        name = r.get("chunkName") or r.get("_chunkName")
        if r.get(input_field, "") == "" and name and name in chunk_by_name:
            r[input_field] = chunk_by_name[name]
            updated += 1
        elif r.get(input_field, "") == "" and name:
            missing.append(name)
        new_records.append(r)

    return new_records, {
        "fix": "chunk-join",
        "updated": updated,
        "total": len(records),
        "unmatched_chunks": sorted(set(missing)),
    }


# ── Fix: unwrap-labels ─────────────────────────────────────────────────
def fix_unwrap_labels(
    records: list[dict[str, Any]],
    *,
    output_field: str = "output",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Unwrap double-encoded JSON-string label arrays.

    Turns ``'[\\n  "positive"\\n]'`` (what the server emits for label
    templates) into plain ``'positive'``. Idempotent: records that are
    already plain strings pass through unchanged.
    """
    updated = 0
    new_records: list[dict[str, Any]] = []
    for r in records:
        r = dict(r)
        val = r.get(output_field)
        if isinstance(val, str):
            stripped = val.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                try:
                    decoded = json.loads(stripped)
                    if isinstance(decoded, list) and decoded:
                        flat = decoded[0] if len(decoded) == 1 else ", ".join(
                            str(x) for x in decoded
                        )
                        if flat != val:
                            r[output_field] = flat
                            updated += 1
                except json.JSONDecodeError:
                    pass
        new_records.append(r)

    return new_records, {
        "fix": "unwrap-labels",
        "updated": updated,
        "total": len(records),
    }


# ── Fix: render-placeholders ───────────────────────────────────────────
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def fix_render_placeholders(
    records: list[dict[str, Any]],
    variables: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Replace ``{{var}}`` placeholders with values from ``variables``.

    Walks every string field (including nested ``messages`` lists for
    ShareGPT) and substitutes. Placeholders with no matching variable
    are left intact and reported in the summary.
    """
    subs = 0
    missing: set[str] = set()

    def render(s: str) -> str:
        nonlocal subs

        def repl(m: re.Match[str]) -> str:
            nonlocal subs
            name = m.group(1)
            if name in variables:
                subs += 1
                return variables[name]
            missing.add(name)
            return m.group(0)

        return _PLACEHOLDER_RE.sub(repl, s)

    def walk(obj: Any) -> Any:
        if isinstance(obj, str):
            return render(obj)
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        return obj

    new_records = [walk(r) for r in records]
    return new_records, {
        "fix": "render-placeholders",
        "substitutions": subs,
        "unresolved_placeholders": sorted(missing),
        "total": len(records),
    }


FIXES = {
    "chunk-join": fix_chunk_join,
    "unwrap-labels": fix_unwrap_labels,
    "render-placeholders": fix_render_placeholders,
}
