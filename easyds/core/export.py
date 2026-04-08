"""Dataset export — wraps /api/projects/{id}/datasets/export.

The server's ``/datasets/export`` route only really accepts
``{status, batchMode, balanceConfig, selectedIds}`` and always returns a JSON
array of records. **Everything else** the CLI offers — JSONL / CSV file types,
field renaming, train/val/test splits, ``--include-chunk`` /
``--include-image-path`` — is implemented **client-side** in this module after
the records come back. That stays within the HTTP-backend constraint because
it's pure output formatting (no LLM calls, no business logic).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from typing import Any, Iterable

from easyds.utils.backend import EasyDatasetBackend
from easyds.core import datasets as datasets_mod


VALID_FORMATS = {"alpaca", "sharegpt", "multilingual-thinking"}


def format_records(
    records: list[dict[str, Any]],
    *,
    fmt: str,
    include_cot: bool = False,
    system_prompt: str = "",
    reasoning_language: str = "English",
) -> list[dict[str, Any]]:
    """Convert raw dataset rows into the requested fine-tuning format.

    The Easy-Dataset server's ``/datasets/export`` POST returns RAW rows
    (id, projectId, question, answer, cot, score, ...). The GUI's
    ``useDatasetExport.formatDataBatch`` then maps each row into the canonical
    Alpaca / ShareGPT / multilingual-thinking shape — the CLI must do the
    same client-side. This function is a faithful port of that logic.

    Rows with empty/missing ``question`` or ``answer`` are silently skipped
    so half-generated datasets can still be exported.

    Args:
        records: raw rows from /datasets/export
        fmt: one of ``alpaca`` / ``sharegpt`` / ``multilingual-thinking``
        include_cot: when True, embeds ``<think>{cot}</think>\\n`` before the
            answer in alpaca/sharegpt outputs (matches the GUI's includeCOT
            behaviour)
        system_prompt: alpaca ``system`` field / sharegpt system message
        reasoning_language: only used by ``multilingual-thinking``
    """
    if fmt not in VALID_FORMATS:
        raise ValueError(
            f"format must be one of {sorted(VALID_FORMATS)}, got {fmt!r}"
        )

    out: list[dict[str, Any]] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        # Idempotency: if the row is already in the requested shape (e.g.
        # the GUI sometimes pre-formats; tests sometimes pass alpaca dicts
        # directly) skip the conversion and pass it through.
        if fmt == "alpaca" and "instruction" in row and "output" in row:
            out.append(row)
            continue
        if fmt == "sharegpt" and "messages" in row:
            out.append(row)
            continue
        if fmt == "multilingual-thinking" and "user" in row and "final" in row:
            out.append(row)
            continue
        question = (row.get("question") or "").strip()
        answer = (row.get("answer") or "").strip()
        if not question or not answer:
            continue
        cot = row.get("cot") or ""
        answer_with_cot = (
            f"<think>{cot}</think>\n{answer}" if (include_cot and cot) else answer
        )

        if fmt == "alpaca":
            out.append({
                "instruction": question,
                "input": "",
                "output": answer_with_cot,
                "system": system_prompt or "",
            })
        elif fmt == "sharegpt":
            messages: list[dict[str, str]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": question})
            messages.append({"role": "assistant", "content": answer_with_cot})
            out.append({"messages": messages})
        elif fmt == "multilingual-thinking":
            out.append({
                "reasoning_language": reasoning_language,
                "developer": system_prompt or "",
                "user": question,
                "analysis": (cot if (include_cot and cot) else None),
                "final": answer,
                "messages": [
                    {"content": system_prompt or "", "role": "system", "thinking": None},
                    {"content": question, "role": "user", "thinking": None},
                    {
                        "content": answer,
                        "role": "assistant",
                        "thinking": (cot if (include_cot and cot) else None),
                    },
                ],
            })
    return out

# Output file types the CLI can serialize client-side.
VALID_FILE_TYPES = ("json", "jsonl", "csv")

# Multi-turn dialogue datasets can ONLY be exported as ShareGPT JSON.
# (See spec/01 §16 and spec/02 §multi-turn — Easy-Dataset's own UI also
# locks the format selector for multi-turn corpora.)
MULTI_TURN_FORMATS = {"sharegpt"}


def validate_multi_turn_format(fmt: str) -> None:
    """Raise ValueError if ``fmt`` isn't allowed for multi-turn datasets."""
    if fmt not in MULTI_TURN_FORMATS:
        raise ValueError(
            f"multi-turn dialogue datasets only support {sorted(MULTI_TURN_FORMATS)} "
            f"format, got {fmt!r}. See spec/04 §L9."
        )


def export_conversations(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    output_path: str,
    fmt: str = "sharegpt",
    overwrite: bool = False,
) -> dict[str, Any]:
    """POST /api/projects/{id}/dataset-conversations/export — write multi-turn JSON.

    Forces ShareGPT format (per spec/04 §L9). Raises ``ValueError`` for any
    other format.
    """
    validate_multi_turn_format(fmt)

    if os.path.exists(output_path) and not overwrite:
        raise FileExistsError(
            f"{output_path} already exists. Pass --overwrite to replace it."
        )

    result = backend.post(
        f"/api/projects/{project_id}/dataset-conversations/export",
        json_body={"format": fmt},
    )
    if isinstance(result, dict) and "data" in result:
        records = result["data"]
    elif isinstance(result, list):
        records = result
    else:
        records = result

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, indent=2)

    return {
        "output": os.path.abspath(output_path),
        "format": fmt,
        "size": os.path.getsize(output_path),
        "count": len(records) if hasattr(records, "__len__") else None,
        "kind": "multi-turn",
    }


def parse_field_map(specs: Iterable[str]) -> dict[str, str]:
    """Parse repeated ``--field-map src=dst`` flags into a rename dict.

    Example: ``["question=instruction", "answer=output"]`` →
    ``{"question": "instruction", "answer": "output"}``.

    Raises ``ValueError`` on missing ``=``, empty source, or empty target.
    """
    result: dict[str, str] = {}
    for spec in specs:
        if "=" not in spec:
            raise ValueError(
                f"--field-map expects 'src=dst' (got {spec!r})"
            )
        src, dst = spec.split("=", 1)
        src, dst = src.strip(), dst.strip()
        if not src or not dst:
            raise ValueError(f"--field-map src and dst must both be non-empty (got {spec!r})")
        result[src] = dst
    return result


def apply_field_map(records: list[dict[str, Any]], field_map: dict[str, str]) -> list[dict[str, Any]]:
    """Rename keys in every record according to ``field_map``.

    Keys not present in the map are preserved unchanged. Records that aren't
    dicts are passed through.
    """
    if not field_map:
        return records
    out: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            out.append(rec)
            continue
        new = {field_map.get(k, k): v for k, v in rec.items()}
        out.append(new)
    return out


def parse_split_ratio(spec: str) -> tuple[float, float, float]:
    """Parse ``"0.7,0.15,0.15"`` (or ``"70/15/15"``) into three floats summing to 1.

    Raises ``ValueError`` if the parts don't add up (with 1e-6 tolerance) or
    if any value is negative.
    """
    parts = spec.replace("/", ",").split(",")
    if len(parts) != 3:
        raise ValueError(
            f"--split expects 3 comma-separated ratios, got {spec!r}"
        )
    try:
        ratios = tuple(float(p.strip()) for p in parts)
    except ValueError as e:
        raise ValueError(f"--split values must be numbers: {spec!r}") from e
    if any(r < 0 for r in ratios):
        raise ValueError(f"--split values must be ≥ 0: {spec!r}")
    total = sum(ratios)
    # Accept either fractions (0.7,0.15,0.15) or percentages (70,15,15).
    if abs(total - 1.0) < 1e-6:
        return ratios  # type: ignore[return-value]
    if abs(total - 100.0) < 1e-4:
        return tuple(r / 100.0 for r in ratios)  # type: ignore[return-value]
    raise ValueError(
        f"--split ratios must sum to 1.0 or 100, got sum={total} from {spec!r}"
    )


def deterministic_split(
    records: list[dict[str, Any]],
    *,
    train: float,
    valid: float,
    test: float,
    key: str = "id",
) -> dict[str, list[dict[str, Any]]]:
    """Split ``records`` into train/valid/test using a stable hash of ``key``.

    Determinism means re-running the export produces identical splits even if
    the row order changes — important when you want to add new rows to an
    existing benchmark without leaking train into test. Records missing
    ``key`` fall back to a hash of their JSON dump.
    """
    buckets: dict[str, list[dict[str, Any]]] = {"train": [], "valid": [], "test": []}
    if not records:
        return buckets
    boundary_tv = train  # train upper bound
    boundary_te = train + valid  # valid upper bound
    for rec in records:
        seed_src = ""
        if isinstance(rec, dict) and key in rec:
            seed_src = str(rec[key])
        else:
            seed_src = json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str)
        h = hashlib.sha1(seed_src.encode("utf-8")).hexdigest()
        # First 8 hex chars give us a uniform [0,1) float.
        bucket = int(h[:8], 16) / 0xFFFFFFFF
        if bucket < boundary_tv:
            buckets["train"].append(rec)
        elif bucket < boundary_te:
            buckets["valid"].append(rec)
        else:
            buckets["test"].append(rec)
    return buckets


def serialize_records(
    records: list[dict[str, Any]], *, file_type: str
) -> bytes:
    """Serialize a record list to bytes in the requested file format.

    Supports json / jsonl / csv. xlsx is intentionally **not** supported to
    avoid dragging in openpyxl as a hard dependency — use csv and convert in
    your spreadsheet of choice.
    """
    if file_type not in VALID_FILE_TYPES:
        raise ValueError(
            f"file_type must be one of {VALID_FILE_TYPES}, got {file_type!r}"
        )
    if file_type == "json":
        return json.dumps(records, ensure_ascii=False, indent=2).encode("utf-8")
    if file_type == "jsonl":
        lines = [
            json.dumps(rec, ensure_ascii=False, default=str)
            for rec in records
        ]
        return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    # csv
    if not records:
        return b""
    # Collect the union of all keys, preserving first-seen order.
    headers: list[str] = []
    seen: set[str] = set()
    for rec in records:
        if not isinstance(rec, dict):
            continue
        for k in rec.keys():
            if k not in seen:
                headers.append(k)
                seen.add(k)
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for rec in records:
        if not isinstance(rec, dict):
            continue
        row = {k: _csv_cell(v) for k, v in rec.items()}
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def _csv_cell(value: Any) -> str:
    """Coerce a value to a CSV cell — JSON-encode lists/dicts, str() the rest."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _strip_empty_dirs(path: str) -> None:
    """No-op placeholder kept for clarity in caller flow."""


def _attach_metadata(
    records: list[dict[str, Any]],
    *,
    include_chunk: bool,
    include_image_path: bool,
) -> list[dict[str, Any]]:
    """Optionally attach ``chunkContent`` / ``imagePath`` to each record.

    The Easy-Dataset SFT ``Datasets`` row already carries ``chunkContent`` and
    an ``other`` JSON-string field that often holds ``imagePath``. This helper
    just unwraps them when the user asks. Records without the metadata pass
    through untouched.
    """
    if not (include_chunk or include_image_path):
        return records
    out: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            out.append(rec)
            continue
        new = dict(rec)
        if include_chunk:
            # chunkContent / chunkName may already be present in the export
            # response; nothing to do beyond preserving them.
            new.setdefault("chunkContent", rec.get("chunkContent", ""))
            new.setdefault("chunkName", rec.get("chunkName", ""))
        if include_image_path:
            other = rec.get("other")
            if isinstance(other, str):
                try:
                    other = json.loads(other)
                except (json.JSONDecodeError, ValueError):
                    other = None
            if isinstance(other, dict) and "imagePath" in other:
                new["imagePath"] = other["imagePath"]
            elif "imagePath" in rec:
                new["imagePath"] = rec["imagePath"]
        out.append(new)
    return out


def _split_output_path(output_path: str, suffix: str) -> str:
    """Insert ``-suffix`` before the file extension. ``a/b.json`` → ``a/b-train.json``."""
    base, ext = os.path.splitext(output_path)
    return f"{base}-{suffix}{ext}"


def run(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    output_path: str,
    fmt: str = "alpaca",
    confirmed_only: bool = True,
    overwrite: bool = False,
    balance_config: dict[str, Any] | None = None,
    score_gte: float | None = None,
    score_lte: float | None = None,
    file_type: str = "json",
    field_map: dict[str, str] | None = None,
    include_chunk: bool = False,
    include_image_path: bool = False,
    include_cot: bool = False,
    system_prompt: str = "",
    reasoning_language: str = "English",
    split: tuple[float, float, float] | None = None,
) -> dict[str, Any]:
    """POST /api/projects/{id}/datasets/export — fetch + write to disk.

    Returns ``{"output": output_path, "format": fmt, "size": bytes, "count": n}``.
    With ``split=(train, valid, test)`` writes three files (``-train`` /
    ``-valid`` / ``-test`` suffixes) and the returned dict gains a
    ``"splits": {"train": {...}, "valid": {...}, "test": {...}}`` block.

    When ``score_gte`` / ``score_lte`` is given, the function first calls
    ``datasets.list_datasets`` with the same filter to collect matching ids,
    then forwards them as ``selectedIds`` to the export endpoint. This works
    because Easy-Dataset's export route already supports id-scoped exports
    (see CLAUDE.md and the export endpoint source).
    """
    if fmt not in VALID_FORMATS:
        raise ValueError(f"format must be one of {sorted(VALID_FORMATS)}, got {fmt!r}")
    if file_type not in VALID_FILE_TYPES:
        raise ValueError(
            f"file_type must be one of {VALID_FILE_TYPES}, got {file_type!r}"
        )

    # When splitting, we'll write 3 files; check each one's existence.
    if split is not None:
        if len(split) != 3:
            raise ValueError("split must be a (train, valid, test) tuple")
        for suffix in ("train", "valid", "test"):
            target = _split_output_path(output_path, suffix)
            if os.path.exists(target) and not overwrite:
                raise FileExistsError(
                    f"{target} already exists. Pass --overwrite to replace it."
                )
    else:
        if os.path.exists(output_path) and not overwrite:
            raise FileExistsError(
                f"{output_path} already exists. Pass --overwrite to replace it."
            )

    body: dict[str, Any] = {
        "format": fmt,
        "confirmedOnly": confirmed_only,
    }
    if balance_config:
        body["balanceConfig"] = balance_config

    if score_gte is not None or score_lte is not None:
        filtered = datasets_mod.list_datasets(
            backend,
            project_id,
            score_gte=score_gte,
            score_lte=score_lte,
            confirmed=True if confirmed_only else None,
            page=1,
            page_size=10000,
        )
        ids = [d["id"] for d in filtered if isinstance(d, dict) and "id" in d]
        if not ids:
            raise ValueError(
                f"No datasets matched score filter "
                f"(gte={score_gte}, lte={score_lte}). Nothing to export."
            )
        body["selectedIds"] = ids

    result = backend.post(f"/api/projects/{project_id}/datasets/export", json_body=body)

    # The server may return either a list (records) or {data: [...]}.
    if isinstance(result, dict) and "data" in result:
        records = result["data"]
    elif isinstance(result, list):
        records = result
    else:
        records = result

    if not isinstance(records, list):
        records = [records] if records is not None else []

    # Client-side post-processing — order matters:
    # 1. Enrich raw rows with chunk/image metadata (if requested)
    # 2. Convert to canonical format (alpaca / sharegpt / multilingual)
    # 3. Apply field map (rename instruction → prompt etc.)
    # 4. Split + serialize
    records = _attach_metadata(
        records,
        include_chunk=include_chunk,
        include_image_path=include_image_path,
    )
    # Stash chunk/image metadata BEFORE format conversion (which would drop
    # any keys not in the canonical schema)
    metadata_by_index: list[dict[str, Any]] = []
    if include_chunk or include_image_path:
        for r in records:
            extra: dict[str, Any] = {}
            if include_chunk and "chunkContent" in r:
                extra["chunkContent"] = r.get("chunkContent")
                extra["chunkName"] = r.get("chunkName")
            if include_image_path and "imagePath" in r:
                extra["imagePath"] = r.get("imagePath")
            metadata_by_index.append(extra)

    records = format_records(
        records, fmt=fmt,
        include_cot=include_cot,
        system_prompt=system_prompt,
        reasoning_language=reasoning_language,
    )

    # Re-attach the chunk/image metadata onto the formatted records
    if metadata_by_index:
        # The format step may drop empty rows; re-zip by position only when
        # counts still match.
        if len(metadata_by_index) == len(records):
            for r, extra in zip(records, metadata_by_index):
                r.update(extra)

    if field_map:
        records = apply_field_map(records, field_map)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    if split is None:
        payload = serialize_records(records, file_type=file_type)
        with open(output_path, "wb") as fh:
            fh.write(payload)
        return {
            "output": os.path.abspath(output_path),
            "format": fmt,
            "file_type": file_type,
            "size": os.path.getsize(output_path),
            "count": len(records),
            "field_map": field_map or {},
        }

    # Split mode: write 3 files.
    train, valid, test = split
    buckets = deterministic_split(records, train=train, valid=valid, test=test)
    splits_summary: dict[str, Any] = {}
    for name in ("train", "valid", "test"):
        target = _split_output_path(output_path, name)
        payload = serialize_records(buckets[name], file_type=file_type)
        with open(target, "wb") as fh:
            fh.write(payload)
        splits_summary[name] = {
            "output": os.path.abspath(target),
            "count": len(buckets[name]),
            "size": os.path.getsize(target),
        }
    return {
        "output": os.path.abspath(output_path),
        "format": fmt,
        "file_type": file_type,
        "count": len(records),
        "split_ratio": {"train": train, "valid": valid, "test": test},
        "splits": splits_summary,
        "field_map": field_map or {},
    }
