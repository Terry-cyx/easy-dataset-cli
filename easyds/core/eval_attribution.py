"""Rule → pipeline-step attribution table.

When a schema/judge rule fails, we want to tell an agent *which step in
the easyds pipeline to re-run* and *what parameter to change*. This table
is intentionally hand-maintained (not auto-generated) so that every
failure maps to a concrete, actionable suggestion.

Each entry is keyed by the rule name emitted in the check report and
returns a dict with:

* ``step``            — the canonical pipeline step that owns the fix
                        (``upload``, ``split``, ``questions generate``,
                        ``datasets generate``, ``export``,
                        ``prompts set``, ``post-process``)
* ``command``         — the easyds command the agent should re-invoke
* ``suggested_change``— what flag / parameter to change
* ``root_cause_hint`` — brief explanation so the agent can judge edge
                        cases (is this the real cause or a symptom?)
* ``fix``             — optional post-processing ``--fix`` name that can
                        be applied locally without re-running the server

Keep this list short and precise. Prefer "I don't know, ask the user"
(no entry) over a misleading suggestion.
"""

from __future__ import annotations

from typing import Any

ATTRIBUTION: dict[str, dict[str, Any]] = {
    "instruction_empty_rate": {
        "step": "questions generate",
        "command": "easyds questions generate",
        "suggested_change": (
            "Some rows have no instruction — re-run questions generate and "
            "inspect the question template. If using a label template, "
            "verify --question text was provided."
        ),
        "root_cause_hint": (
            "Instruction is produced by the question step; if empty, the "
            "template literal was blank or the question row was deleted."
        ),
    },
    "input_empty_rate": {
        "step": "export",
        "command": "easyds export run",
        "suggested_change": (
            "Re-export with --include-chunk --field-map chunkContent=input. "
            "If the dataset rows themselves have empty chunkContent (happens "
            "for label-template tasks — server-side quirk), use "
            "'easyds datasets eval --fix chunk-join --chunks-file <chunks.json>' "
            "to client-side join the chunk text back in."
        ),
        "root_cause_hint": (
            "Classification / label-template tasks expect the source text in "
            "the `input` field. The server stores the review text in the "
            "chunk, but does not always persist it onto the dataset row."
        ),
        "fix": "chunk-join",
    },
    "output_empty_rate": {
        "step": "datasets generate",
        "command": "easyds datasets generate",
        "suggested_change": (
            "Some rows have an empty answer — re-run datasets generate for "
            "the failing question ids, or raise the model's maxTokens."
        ),
        "root_cause_hint": (
            "Empty answers usually mean the generation call errored silently "
            "or hit a token limit before the model produced content."
        ),
    },
    "output_double_encoded": {
        "step": "post-process",
        "command": "easyds datasets eval --fix unwrap-labels",
        "suggested_change": (
            "Run 'easyds datasets eval <file> --fix unwrap-labels' to unwrap "
            "double-encoded JSON label arrays like '[\"positive\"]' → 'positive'."
        ),
        "root_cause_hint": (
            "Label template answers are stored as JSON arrays server-side. "
            "Trainers usually want the plain label string."
        ),
        "fix": "unwrap-labels",
    },
    "placeholder_leak_rate": {
        "step": "datasets generate",
        "command": "easyds datasets generate",
        "suggested_change": (
            "Re-run datasets generate and pass every {{var}} placeholder as "
            "an explicit flag (e.g. --role-a 用户 --role-b 助手 for multi-turn). "
            "For one-off cleanup without re-running, use "
            "'easyds datasets eval --fix render-placeholders --var student=高中生'."
        ),
        "root_cause_hint": (
            "Placeholders like {{student}} come from the system prompt; if "
            "they survive into the final output, the prompt template was "
            "never rendered with the corresponding variable."
        ),
        "fix": "render-placeholders",
    },
    "duplicate_instruction_rate": {
        "step": "export",
        "command": "easyds export run",
        "suggested_change": (
            "Identical instructions across rows are only OK when `input` "
            "carries the varying content (e.g. classification). If inputs "
            "are also missing, this is the same root cause as "
            "input_empty_rate — fix that first."
        ),
        "root_cause_hint": (
            "Template questions produce N identical instructions. Without "
            "distinguishing input, the dataset has effectively one example."
        ),
    },
    "sample_size_too_small": {
        "step": "questions generate",
        "command": "easyds questions generate",
        "suggested_change": (
            "Generate more questions (increase --question-count for image "
            "sources, or split into more chunks for text sources), then "
            "re-run datasets generate and re-export."
        ),
        "root_cause_hint": (
            "Fewer than 8 records is rarely enough signal to train anything. "
            "This is a warn-only rule unless --strict is passed."
        ),
    },
    "multi_turn_malformed": {
        "step": "datasets generate",
        "command": "easyds datasets generate --rounds N",
        "suggested_change": (
            "Re-run multi-turn generation with a higher --rounds value and "
            "verify --role-a / --role-b are both set. ShareGPT conversations "
            "must alternate user/assistant strictly."
        ),
        "root_cause_hint": (
            "Multi-turn conversations with fewer than 3 messages or with "
            "non-alternating roles will break most SFT training pipelines."
        ),
    },
    "mean_output_length_outlier": {
        "step": "datasets generate",
        "command": "easyds datasets generate",
        "suggested_change": (
            "Raise or lower the model's maxTokens via 'easyds model set', "
            "or adjust the answer prompt via 'easyds prompts set'."
        ),
        "root_cause_hint": (
            "Very short (<10 char) or very long (>4000 char) answers usually "
            "mean the prompt is off or the model is truncating."
        ),
    },
    # ── LLM-judge rules (phase 2) ──
    "judge_groundedness_low": {
        "step": "datasets generate",
        "command": "easyds datasets generate",
        "suggested_change": (
            "Low groundedness means the model's answer isn't supported by "
            "the input. Try a stronger model, or tighten the answer prompt "
            "to require 'only use information present in the source text'."
        ),
        "root_cause_hint": (
            "Groundedness failures indicate hallucination, not a schema "
            "problem. Re-running datasets generate with a better model "
            "typically fixes it."
        ),
    },
    "judge_correctness_low": {
        "step": "datasets generate",
        "command": "easyds datasets generate",
        "suggested_change": (
            "Correctness failures usually mean the generation model is too "
            "weak. Re-run datasets generate with a larger / better model."
        ),
        "root_cause_hint": (
            "The judge model thinks the answer is factually wrong. Consider "
            "using the judge model *as* the generation model if credit allows."
        ),
    },
    "judge_clarity_low": {
        "step": "prompts set",
        "command": "easyds prompts set --type answer --key ANSWER_PROMPT",
        "suggested_change": (
            "Low clarity usually means the answer prompt doesn't enforce "
            "structure. Update the answer prompt to require clean, "
            "non-repetitive prose."
        ),
        "root_cause_hint": (
            "Clarity problems are prompt problems, not model problems — "
            "fix the prompt first before swapping models."
        ),
    },
}


def attribute(rule_name: str) -> dict[str, Any] | None:
    """Return the attribution entry for a rule, or None if unknown."""
    return ATTRIBUTION.get(rule_name)
