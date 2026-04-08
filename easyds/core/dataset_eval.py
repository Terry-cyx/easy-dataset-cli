"""Dataset evaluation — the easyds feedback loop.

This is the unique value-add of easyds over the Easy-Dataset GUI: once
you've emitted a final Alpaca/ShareGPT file, you can ask the CLI
"is this actually usable?", get a machine-readable report, and (for
an agent) a list of suggested re-runs keyed to pipeline steps.

Architecture:

1. **Task-type detection** — classification, qa, vqa, or multi-turn.
   Explicit ``task_type`` wins; otherwise we sniff from record shape.
2. **Schema rules** (fast, deterministic, always run). Each rule is a
   pure function ``(records, task_type) -> CheckResult``. Verdicts:
   ``pass`` / ``warn`` / ``fail``.
3. **LLM judge** (optional, ``--llm-judge``). Delegates to
   ``eval_judge.judge_records`` with a sampled subset.
4. **Attribution** — every failing rule is cross-referenced with
   ``eval_attribution.ATTRIBUTION`` to produce actionable suggestions.
5. **Report** — a dict containing all of the above, plus a
   summary ``verdict`` and a shell ``exit_code``:
   ``0`` all-pass, ``1`` warn-only, ``2`` hard fail.

The runner never calls the server. ``run_llm_judge`` talks directly to
an OpenAI-compatible endpoint so agents can run eval against a bare
file on a machine with no running Easy-Dataset backend.
"""

from __future__ import annotations

import hashlib
import re
import statistics
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from easyds.core import eval_attribution, eval_fixes, eval_judge


VALID_TASK_TYPES = ("auto", "qa", "classification", "vqa", "multi-turn")
VALID_VERDICTS = ("pass", "warn", "fail")

# Default thresholds. Overridden by CheckResult.threshold in output.
DEFAULT_THRESHOLDS = {
    "instruction_empty_rate": 0.0,
    "input_empty_rate": 0.1,       # only enforced when task expects input
    "output_empty_rate": 0.0,
    "output_double_encoded": 0.0,
    "placeholder_leak_rate": 0.0,
    "duplicate_instruction_rate": 0.5,
    "sample_size_too_small": 8,    # min records
    "mean_output_length_min": 10,
    "mean_output_length_max": 4000,
    "judge_mean_min": 3.0,         # judge scores below this → fail
}


@dataclass
class CheckResult:
    """Outcome of a single rule evaluation."""
    name: str
    value: Any
    verdict: str                    # "pass" | "warn" | "fail"
    threshold: Any = None
    message: str = ""
    failing_indices: list[int] = field(default_factory=list)


@dataclass
class EvalReport:
    """Full report — what we hand back to the CLI / agent."""
    file: str
    file_sha256_prefix: str
    format: str                     # "alpaca" | "sharegpt"
    task_type: str
    sample_size: int
    checks: list[CheckResult]
    attribution: list[dict[str, Any]]
    judge: dict[str, Any] | None
    verdict: str                    # aggregate
    exit_code: int
    failing_samples: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "file_sha256_prefix": self.file_sha256_prefix,
            "format": self.format,
            "task_type": self.task_type,
            "sample_size": self.sample_size,
            "checks": [asdict(c) for c in self.checks],
            "attribution": self.attribution,
            "judge": self.judge,
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "failing_samples": self.failing_samples,
        }


# ── Format + task-type detection ──────────────────────────────────────

def detect_format(records: list[dict[str, Any]]) -> str:
    """Return 'sharegpt' if every record has a 'messages' list, else 'alpaca'."""
    if not records:
        return "alpaca"
    if all(isinstance(r.get("messages"), list) for r in records):
        return "sharegpt"
    return "alpaca"


def detect_task_type(
    records: list[dict[str, Any]],
    fmt: str,
    explicit: str | None = None,
) -> str:
    """Auto-detect or honor an explicit override.

    Heuristics:
    - ShareGPT → multi-turn
    - Any ``input`` field starts with 'image://' → vqa
    - All identical instructions → classification (label template)
    - Otherwise → qa
    """
    if explicit and explicit != "auto":
        return explicit
    if fmt == "sharegpt":
        return "multi-turn"
    if not records:
        return "qa"
    first_inputs = [str(r.get("input", "")) for r in records[:5]]
    if any(s.startswith("image://") for s in first_inputs):
        return "vqa"
    instructions = {str(r.get("instruction", "")) for r in records}
    if len(instructions) == 1 and len(records) >= 2:
        return "classification"
    return "qa"


def task_expects_input(task_type: str) -> bool:
    """Does this task type require a populated ``input`` field?"""
    return task_type in ("classification", "vqa")


# ── Individual schema rules ───────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(r"\{\{\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\}\}")


def _records_iter_text_fields(
    records: list[dict[str, Any]], fmt: str
) -> list[tuple[int, str, str]]:
    """Yield (record_index, field_name, text) for every string field we care about.

    For Alpaca: instruction, input, output.
    For ShareGPT: every messages[].content.
    """
    out: list[tuple[int, str, str]] = []
    for i, r in enumerate(records):
        if fmt == "sharegpt":
            for j, m in enumerate(r.get("messages", [])):
                c = m.get("content")
                if isinstance(c, str):
                    out.append((i, f"messages[{j}].content", c))
        else:
            for k in ("instruction", "input", "output"):
                v = r.get(k)
                if isinstance(v, str):
                    out.append((i, k, v))
    return out


def check_instruction_empty(
    records: list[dict[str, Any]], fmt: str, task_type: str
) -> CheckResult | None:
    if fmt == "sharegpt":
        return None
    thresh = DEFAULT_THRESHOLDS["instruction_empty_rate"]
    failing = [
        i for i, r in enumerate(records)
        if not str(r.get("instruction", "")).strip()
    ]
    rate = len(failing) / len(records) if records else 0
    verdict = "fail" if rate > thresh else "pass"
    return CheckResult(
        name="instruction_empty_rate",
        value=round(rate, 3),
        verdict=verdict,
        threshold=thresh,
        message=(
            f"{len(failing)}/{len(records)} records have empty instruction"
            if failing else "all records have an instruction"
        ),
        failing_indices=failing[:10],
    )


def check_input_empty(
    records: list[dict[str, Any]], fmt: str, task_type: str
) -> CheckResult | None:
    if fmt == "sharegpt":
        return None
    if not task_expects_input(task_type):
        # Open-ended QA: empty input is fine.
        return None
    thresh = DEFAULT_THRESHOLDS["input_empty_rate"]
    failing = [
        i for i, r in enumerate(records)
        if not str(r.get("input", "")).strip()
    ]
    rate = len(failing) / len(records) if records else 0
    verdict = "fail" if rate > thresh else "pass"
    return CheckResult(
        name="input_empty_rate",
        value=round(rate, 3),
        verdict=verdict,
        threshold=thresh,
        message=(
            f"{len(failing)}/{len(records)} records have empty input — "
            f"task_type={task_type} requires input"
            if failing
            else f"all {task_type} records have non-empty input"
        ),
        failing_indices=failing[:10],
    )


def check_output_empty(
    records: list[dict[str, Any]], fmt: str, task_type: str
) -> CheckResult | None:
    thresh = DEFAULT_THRESHOLDS["output_empty_rate"]
    if fmt == "sharegpt":
        failing = []
        for i, r in enumerate(records):
            msgs = r.get("messages", [])
            last_asst = next(
                (m for m in reversed(msgs) if m.get("role") == "assistant"),
                None,
            )
            if not last_asst or not str(last_asst.get("content", "")).strip():
                failing.append(i)
    else:
        failing = [
            i for i, r in enumerate(records)
            if not str(r.get("output", "")).strip()
        ]
    rate = len(failing) / len(records) if records else 0
    verdict = "fail" if rate > thresh else "pass"
    return CheckResult(
        name="output_empty_rate",
        value=round(rate, 3),
        verdict=verdict,
        threshold=thresh,
        message=(
            f"{len(failing)}/{len(records)} records have empty output"
            if failing else "all records have non-empty output"
        ),
        failing_indices=failing[:10],
    )


def check_output_double_encoded(
    records: list[dict[str, Any]], fmt: str, task_type: str
) -> CheckResult | None:
    """Detects ``'["label"]'`` style outputs (label template server quirk)."""
    if fmt == "sharegpt":
        return None
    import json as _json
    failing = []
    for i, r in enumerate(records):
        v = r.get("output")
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("[") and s.endswith("]"):
                try:
                    decoded = _json.loads(s)
                    if isinstance(decoded, list):
                        failing.append(i)
                except _json.JSONDecodeError:
                    pass
    rate = len(failing) / len(records) if records else 0
    verdict = "fail" if rate > 0 else "pass"
    return CheckResult(
        name="output_double_encoded",
        value=round(rate, 3),
        verdict=verdict,
        threshold=0.0,
        message=(
            f"{len(failing)}/{len(records)} records have JSON-string-encoded "
            f"label arrays — unwrap with --fix unwrap-labels"
            if failing else "no double-encoded outputs"
        ),
        failing_indices=failing[:10],
    )


def check_placeholder_leak(
    records: list[dict[str, Any]], fmt: str, task_type: str
) -> CheckResult | None:
    """Fails if any string field still contains ``{{var}}`` placeholders."""
    failing_indices: set[int] = set()
    samples: list[str] = []
    for i, field_name, text in _records_iter_text_fields(records, fmt):
        m = _PLACEHOLDER_RE.search(text)
        if m:
            failing_indices.add(i)
            if len(samples) < 3:
                samples.append(f"record[{i}].{field_name}: {m.group(0)}")
    rate = len(failing_indices) / len(records) if records else 0
    verdict = "fail" if failing_indices else "pass"
    return CheckResult(
        name="placeholder_leak_rate",
        value=round(rate, 3),
        verdict=verdict,
        threshold=0.0,
        message=(
            f"unrendered placeholders in {len(failing_indices)} record(s): "
            + "; ".join(samples)
            if failing_indices else "no placeholder leaks"
        ),
        failing_indices=sorted(failing_indices)[:10],
    )


def check_duplicate_instruction(
    records: list[dict[str, Any]], fmt: str, task_type: str
) -> CheckResult | None:
    """For classification: duplicate instructions are EXPECTED.

    This rule only fires when the task is NOT classification and there
    are many duplicates, which usually signals a mis-configured template.
    """
    if fmt == "sharegpt" or task_type == "classification":
        return None
    if not records:
        return None
    from collections import Counter
    counts = Counter(str(r.get("instruction", "")) for r in records)
    duplicates = sum(c - 1 for c in counts.values() if c > 1)
    rate = duplicates / len(records)
    thresh = DEFAULT_THRESHOLDS["duplicate_instruction_rate"]
    verdict = "warn" if rate > thresh else "pass"
    return CheckResult(
        name="duplicate_instruction_rate",
        value=round(rate, 3),
        verdict=verdict,
        threshold=thresh,
        message=(
            f"{duplicates} duplicate instructions in {len(records)} records "
            f"(task_type={task_type}) — maybe a misconfigured template?"
            if rate > thresh else "instructions are distinct"
        ),
    )


def check_sample_size(
    records: list[dict[str, Any]], fmt: str, task_type: str
) -> CheckResult:
    thresh = DEFAULT_THRESHOLDS["sample_size_too_small"]
    n = len(records)
    verdict = "warn" if n < thresh else "pass"
    return CheckResult(
        name="sample_size_too_small",
        value=n,
        verdict=verdict,
        threshold=thresh,
        message=(
            f"only {n} records — usually not enough to train anything. "
            f"Generate more questions and re-run."
            if verdict == "warn" else f"{n} records"
        ),
    )


def check_output_length(
    records: list[dict[str, Any]], fmt: str, task_type: str
) -> CheckResult | None:
    lengths: list[int] = []
    if fmt == "sharegpt":
        for r in records:
            last = next(
                (m for m in reversed(r.get("messages", []))
                 if m.get("role") == "assistant"),
                None,
            )
            if last:
                lengths.append(len(str(last.get("content", ""))))
    else:
        for r in records:
            lengths.append(len(str(r.get("output", ""))))
    if not lengths:
        return None
    mean = statistics.mean(lengths)
    lo = DEFAULT_THRESHOLDS["mean_output_length_min"]
    hi = DEFAULT_THRESHOLDS["mean_output_length_max"]
    verdict = "warn" if mean < lo or mean > hi else "pass"
    return CheckResult(
        name="mean_output_length_outlier",
        value=round(mean, 1),
        verdict=verdict,
        threshold=[lo, hi],
        message=(
            f"mean output length {mean:.0f} chars is outside [{lo}, {hi}]"
            if verdict == "warn" else f"mean output length {mean:.0f} chars is healthy"
        ),
    )


def check_multi_turn(
    records: list[dict[str, Any]], fmt: str, task_type: str
) -> CheckResult | None:
    if fmt != "sharegpt":
        return None
    failing: list[int] = []
    for i, r in enumerate(records):
        msgs = r.get("messages", [])
        if not isinstance(msgs, list) or len(msgs) < 3:
            failing.append(i)
            continue
        # Strip optional leading system message
        body = [m for m in msgs if m.get("role") != "system"]
        if len(body) < 2:
            failing.append(i)
            continue
        # Must alternate user/assistant, starting with user
        for j, m in enumerate(body):
            expected = "user" if j % 2 == 0 else "assistant"
            if m.get("role") != expected:
                failing.append(i)
                break
    rate = len(failing) / len(records) if records else 0
    verdict = "fail" if failing else "pass"
    return CheckResult(
        name="multi_turn_malformed",
        value=round(rate, 3),
        verdict=verdict,
        threshold=0.0,
        message=(
            f"{len(failing)}/{len(records)} conversations are malformed "
            f"(fewer than 3 messages or non-alternating roles)"
            if failing else "all conversations well-formed"
        ),
        failing_indices=failing[:10],
    )


SCHEMA_RULES = [
    check_instruction_empty,
    check_input_empty,
    check_output_empty,
    check_output_double_encoded,
    check_placeholder_leak,
    check_duplicate_instruction,
    check_sample_size,
    check_output_length,
    check_multi_turn,
]


# ── Runner ─────────────────────────────────────────────────────────────

def run_schema_checks(
    records: list[dict[str, Any]],
    *,
    fmt: str,
    task_type: str,
    strict: bool = False,
) -> list[CheckResult]:
    """Apply every schema rule and return the list of CheckResults."""
    results: list[CheckResult] = []
    for rule in SCHEMA_RULES:
        r = rule(records, fmt, task_type)
        if r is None:
            continue
        if strict and r.verdict == "warn":
            r.verdict = "fail"
        results.append(r)
    return results


def _sha256_prefix(path: str | Path, n: int = 12) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:n]


def _collect_failing_samples(
    records: list[dict[str, Any]],
    checks: list[CheckResult],
    *,
    max_samples: int = 3,
    max_field_len: int = 200,
) -> list[dict[str, Any]]:
    """Pick up to 3 records that triggered hard failures."""
    bad_indices: list[int] = []
    for c in checks:
        if c.verdict != "fail":
            continue
        for i in c.failing_indices:
            if i not in bad_indices:
                bad_indices.append(i)
            if len(bad_indices) >= max_samples:
                break
        if len(bad_indices) >= max_samples:
            break

    def truncate(v: Any) -> Any:
        if isinstance(v, str) and len(v) > max_field_len:
            return v[:max_field_len] + "…"
        if isinstance(v, list):
            return [truncate(x) for x in v[:3]]
        if isinstance(v, dict):
            return {k: truncate(val) for k, val in v.items()}
        return v

    out = []
    for idx in bad_indices[:max_samples]:
        rec = records[idx]
        out.append({"index": idx, "excerpt": truncate(rec)})
    return out


def evaluate(
    file: str | Path,
    *,
    task_type: str = "auto",
    strict: bool = False,
    llm_judge: bool = False,
    judge_model_config: dict[str, Any] | None = None,
    judge_sample_size: int = 20,
) -> EvalReport:
    """The main entry point. Load → check → (judge) → build report.

    Never calls the Easy-Dataset server. The LLM judge, when enabled,
    hits the model config's endpoint directly.
    """
    records, _file_type = eval_fixes.load_records(file)
    fmt = detect_format(records)
    resolved_task_type = detect_task_type(records, fmt, task_type)

    checks = run_schema_checks(
        records, fmt=fmt, task_type=resolved_task_type, strict=strict
    )

    judge_summary: dict[str, Any] | None = None
    if llm_judge:
        if judge_model_config is None:
            judge_summary = {
                "errors": [
                    "--llm-judge requires --model-config (or a session "
                    "default) so the CLI knows which endpoint to call"
                ],
            }
        else:
            judge_summary = eval_judge.judge_records(
                records,
                model_config=judge_model_config,
                sample_size=judge_sample_size,
            )
            # Promote low mean scores to failing checks
            means = judge_summary.get("mean", {})
            thresh = DEFAULT_THRESHOLDS["judge_mean_min"]
            for axis, rule_name in (
                ("groundedness", "judge_groundedness_low"),
                ("correctness", "judge_correctness_low"),
                ("clarity", "judge_clarity_low"),
            ):
                v = means.get(axis)
                if v is None:
                    continue
                verdict = "fail" if v < thresh else "pass"
                checks.append(
                    CheckResult(
                        name=rule_name,
                        value=v,
                        verdict=verdict,
                        threshold=thresh,
                        message=(
                            f"judge mean {axis}={v:.2f} below {thresh}"
                            if verdict == "fail"
                            else f"judge mean {axis}={v:.2f} ≥ {thresh}"
                        ),
                    )
                )

    # Attribution for every failing or warning rule
    attribution: list[dict[str, Any]] = []
    for c in checks:
        if c.verdict == "pass":
            continue
        entry = eval_attribution.attribute(c.name)
        if entry is None:
            continue
        attribution.append({
            "rule": c.name,
            "verdict": c.verdict,
            **entry,
        })

    # Aggregate verdict
    has_fail = any(c.verdict == "fail" for c in checks)
    has_warn = any(c.verdict == "warn" for c in checks)
    if has_fail:
        verdict, exit_code = "fail", 2
    elif has_warn:
        verdict, exit_code = "warn", 1
    else:
        verdict, exit_code = "pass", 0

    return EvalReport(
        file=str(file),
        file_sha256_prefix=_sha256_prefix(file),
        format=fmt,
        task_type=resolved_task_type,
        sample_size=len(records),
        checks=checks,
        attribution=attribution,
        judge=judge_summary,
        verdict=verdict,
        exit_code=exit_code,
        failing_samples=_collect_failing_samples(records, checks),
    )
