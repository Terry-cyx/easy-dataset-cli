"""LLM-judge for 'easyds datasets eval --llm-judge'.

This module talks directly to an OpenAI-compatible chat-completions
endpoint using the model config's own ``endpoint`` and ``apiKey``. We
do not route through the Easy-Dataset server because there is no
generic "chat" route exposed — and because we want the judge to work
even when pointed at a raw JSON file on a box that has no server.

The judge rubric is intentionally small (3 axes, 1–5 each) so the
prompt fits in any context and scoring is stable across providers.

The judge is disabled by default. Callers opt in via
``easyds datasets eval <file> --llm-judge``.
"""

from __future__ import annotations

import json
import random
from typing import Any

import requests


JUDGE_SYSTEM_PROMPT = """You are an impartial dataset quality judge.

You will be shown ONE record from a machine-generated training dataset.
Your job is to score it on three axes (1–5 each) and return strict JSON.

Axes:
- groundedness: is the OUTPUT supported by the INPUT? If there is no
  input (open-ended QA), score 5.
- correctness: is the OUTPUT factually right given the INSTRUCTION?
- clarity:     is the OUTPUT well-formed, non-repetitive, non-rambling?

Score rubric:
  5 = excellent, no issues
  4 = good, minor nitpicks
  3 = acceptable but noticeable problems
  2 = serious problems
  1 = unusable

Return ONLY a JSON object in this exact shape, no prose, no markdown:

{"groundedness": <int 1-5>, "correctness": <int 1-5>, "clarity": <int 1-5>, "issues": ["..."]}
"""


def _judge_user_prompt(record: dict[str, Any]) -> str:
    """Render one record into the judge's user message.

    Works for both Alpaca (instruction/input/output) and ShareGPT
    (messages[]) shapes. For ShareGPT, we treat the last user→assistant
    pair as the target.
    """
    if "messages" in record and isinstance(record["messages"], list):
        msgs = record["messages"]
        instruction = ""
        input_text = ""
        output = ""
        # system message becomes context
        for m in msgs:
            if m.get("role") == "system":
                instruction = f"(system) {m.get('content', '')}"
                break
        # last user/assistant pair
        last_user = next(
            (m for m in reversed(msgs) if m.get("role") == "user"), None
        )
        last_asst = next(
            (m for m in reversed(msgs) if m.get("role") == "assistant"), None
        )
        if last_user:
            input_text = last_user.get("content", "")
        if last_asst:
            output = last_asst.get("content", "")
    else:
        instruction = str(record.get("instruction", ""))
        input_text = str(record.get("input", ""))
        output = str(record.get("output", ""))

    return (
        f"INSTRUCTION:\n{instruction}\n\n"
        f"INPUT:\n{input_text}\n\n"
        f"OUTPUT:\n{output}\n\n"
        f"Score this record."
    )


def _call_chat_completions(
    *,
    endpoint: str,
    api_key: str,
    model_id: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.0,
    timeout: float = 60.0,
) -> str:
    """Low-level OpenAI-compatible chat completion. Returns the content string."""
    url = endpoint.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
    }
    resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"judge chat-completions call failed: {resp.status_code} {resp.text[:300]}"
        )
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _parse_judgment(raw: str) -> dict[str, Any]:
    """Pull a JSON object out of the model's raw response. Tolerant."""
    raw = raw.strip()
    # Drop possible ```json fences
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    # Find the first {...} block
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        return {"_parse_error": True, "raw": raw[:200]}
    try:
        obj = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {"_parse_error": True, "raw": raw[:200]}
    # Normalize scores
    for k in ("groundedness", "correctness", "clarity"):
        if k in obj:
            try:
                obj[k] = max(1, min(5, int(obj[k])))
            except (TypeError, ValueError):
                obj[k] = None
    return obj


def judge_records(
    records: list[dict[str, Any]],
    *,
    model_config: dict[str, Any],
    sample_size: int = 20,
    seed: int = 0,
    system_prompt: str = JUDGE_SYSTEM_PROMPT,
) -> dict[str, Any]:
    """Sample up to ``sample_size`` records and judge each with the LLM.

    Parameters
    ----------
    records
        Full list of dataset records.
    model_config
        A model-config dict as returned by ``core.model.list_configs``.
        Must contain ``endpoint``, ``apiKey``, ``modelId``.
    sample_size
        Max number of records to judge. If ``len(records) <= sample_size``
        every record is judged.
    seed
        Sampling seed for reproducibility.

    Returns a summary dict:
    ``{
        "sample_size": N,
        "mean": {"groundedness": 4.2, "correctness": 4.0, "clarity": 4.5},
        "per_record": [{"index": 3, "groundedness": 5, ..., "issues": [...]}],
        "worst": [...3 worst records by mean score...],
        "errors": [...]
    }``
    """
    if not records:
        return {
            "sample_size": 0,
            "mean": {},
            "per_record": [],
            "worst": [],
            "errors": ["no records to judge"],
        }

    rng = random.Random(seed)
    indices = list(range(len(records)))
    if len(indices) > sample_size:
        indices = rng.sample(indices, sample_size)
        indices.sort()

    endpoint = model_config.get("endpoint", "")
    api_key = model_config.get("apiKey", "")
    model_id = model_config.get("modelId") or model_config.get("modelName", "")

    if not endpoint or not api_key or not model_id:
        return {
            "sample_size": 0,
            "mean": {},
            "per_record": [],
            "worst": [],
            "errors": [
                "model_config is missing endpoint/apiKey/modelId — cannot run judge"
            ],
        }

    per_record: list[dict[str, Any]] = []
    errors: list[str] = []
    for idx in indices:
        try:
            user_prompt = _judge_user_prompt(records[idx])
            raw = _call_chat_completions(
                endpoint=endpoint,
                api_key=api_key,
                model_id=model_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            obj = _parse_judgment(raw)
            obj["index"] = idx
            per_record.append(obj)
        except Exception as e:  # noqa: BLE001 — report, don't crash
            errors.append(f"record {idx}: {e}")

    # Aggregate
    def mean_of(key: str) -> float | None:
        vals = [
            r[key]
            for r in per_record
            if isinstance(r.get(key), int)
        ]
        if not vals:
            return None
        return round(sum(vals) / len(vals), 2)

    means = {
        "groundedness": mean_of("groundedness"),
        "correctness": mean_of("correctness"),
        "clarity": mean_of("clarity"),
    }

    # Worst = lowest mean across the 3 axes
    def row_mean(r: dict[str, Any]) -> float:
        vals = [r.get(k) for k in ("groundedness", "correctness", "clarity")]
        vals = [v for v in vals if isinstance(v, int)]
        return sum(vals) / len(vals) if vals else 5.0

    worst = sorted(per_record, key=row_mean)[:3]

    return {
        "sample_size": len(per_record),
        "mean": means,
        "per_record": per_record,
        "worst": worst,
        "errors": errors,
    }
