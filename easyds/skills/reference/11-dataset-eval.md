# 11 — Dataset Eval & Feedback Loop

**This is the one thing `easyds` does that the Easy-Dataset GUI cannot.**
Once you have a final Alpaca / ShareGPT file, you can ask the CLI *"is
this actually usable?"*, get a machine-readable report, and an agent can
use it to decide which pipeline step to re-run and what flag to change.

Quick mental model:

```
┌──────────┐   ┌─────────┐   ┌──────────────┐   ┌──────────┐
│ generate │──▶│  export │──▶│ datasets eval│──▶│ verdict  │
└──────────┘   └─────────┘   └──────────────┘   └──────────┘
      ▲                                               │
      │                 attribution + --fix           │
      └───────────────────────────────────────────────┘
```

Every failing check is cross-referenced with an **attribution table** so
the report tells you *where to fix*, not just *what's wrong*. Some
failures can be repaired locally without re-running anything via
`--fix <name>`.

## TL;DR

```bash
# Deterministic schema checks (always fast, no API calls)
easyds --json datasets eval sentiment-alpaca.json

# Same thing, but also run a small LLM judge sample
easyds --json datasets eval sentiment-alpaca.json \
    --llm-judge --sample 20 --model-config mc-judge

# Post-processing repairs (safe, local, no server contact)
easyds datasets eval sentiment-alpaca.json \
    --fix chunk-join --chunks-file chunks.json
easyds datasets eval sentiment-alpaca.json --fix unwrap-labels
easyds datasets eval sharegpt.json --fix render-placeholders \
    --var student=高中生

# Audit trail (per project, kept in ~/.easyds/session.json)
easyds --json datasets eval-history
```

## Output schema (`--json`)

```json
{
  "file": "sentiment-alpaca.json",
  "file_sha256_prefix": "1a2b3c4d5e6f",
  "format": "alpaca",
  "task_type": "classification",      // auto-detected or --task-type
  "sample_size": 8,
  "checks": [
    {
      "name": "input_empty_rate",
      "value": 1.0,
      "verdict": "fail",
      "threshold": 0.1,
      "message": "8/8 records have empty input — task_type=classification requires input",
      "failing_indices": [0,1,2,3,4,5,6,7]
    }
  ],
  "attribution": [
    {
      "rule": "input_empty_rate",
      "verdict": "fail",
      "step": "export",
      "command": "easyds export run",
      "suggested_change": "Re-export with --include-chunk --field-map chunkContent=input. If the dataset rows themselves have empty chunkContent … use 'easyds datasets eval --fix chunk-join --chunks-file <chunks.json>'",
      "root_cause_hint": "Classification / label-template tasks expect …",
      "fix": "chunk-join"
    }
  ],
  "judge": null,
  "verdict": "fail",
  "exit_code": 2,
  "failing_samples": [ {"index": 0, "excerpt": { ... }} ]
}
```

- **`verdict`**: `pass` / `warn` / `fail`
- **`exit_code`**: 0 / 1 / 2 — use this in CI to gate pipelines
- **`attribution[*].fix`**: if present, you can run `--fix <name>` to
  repair locally without re-running the server

## The rules

| Rule | Verdict on fail | Fires when |
|---|---|---|
| `instruction_empty_rate` | fail | any row has empty instruction |
| `input_empty_rate` | fail | task_type ∈ {classification, vqa} but `input` is empty |
| `output_empty_rate` | fail | any row has empty answer |
| `output_double_encoded` | fail | output is a JSON-stringified array like `'["label"]'` |
| `placeholder_leak_rate` | fail | unrendered `{{var}}` survives into output |
| `multi_turn_malformed` | fail | ShareGPT conversations with <3 msgs or non-alternating roles |
| `sample_size_too_small` | warn (or fail with --strict) | <8 records |
| `mean_output_length_outlier` | warn | mean answer length outside [10, 4000] chars |
| `duplicate_instruction_rate` | warn | >50% duplicate instructions for non-classification tasks |
| `judge_groundedness_low` | fail | LLM judge mean groundedness < 3.0 |
| `judge_correctness_low` | fail | LLM judge mean correctness < 3.0 |
| `judge_clarity_low` | fail | LLM judge mean clarity < 3.0 |

## Task-type auto-detection

`easyds` tries to guess the task shape from the records themselves:

- `messages[]` present → **multi-turn** (ShareGPT)
- `input` starts with `image://` → **vqa**
- every row has an identical instruction → **classification**
- otherwise → **qa**

Override with `--task-type {qa,classification,vqa,multi-turn}` if the
auto-detection is wrong (e.g. for a summarisation task that happens to
reuse the same prompt).

The critical consequence: **`input_empty_rate` only fires for task types
that expect a populated input.** QA datasets with empty `input` pass.
Classification datasets with empty `input` fail hard.

## The `--fix` handlers (local, safe)

All fixes operate **in-place on the JSON file**. They never call the
Easy-Dataset server, so they're safe to run on CI and free to retry.

### `chunk-join`
Joins the source chunk text back into the `input` field. Designed for
the case-2 failure mode where label-template classification tasks emit
empty `input` because the server didn't persist `chunkContent` onto the
dataset row.

```bash
easyds --json chunks list > chunks.json
easyds datasets eval out.json --fix chunk-join --chunks-file chunks.json
```

### `unwrap-labels`
Collapses `"[\"positive\"]"` → `"positive"`. Idempotent on plain
strings. Always safe.

### `render-placeholders`
Substitutes `{{var}}` placeholders across every string field in every
record (including ShareGPT `messages[].content`). Reports any
placeholders that had no matching `--var`.

```bash
easyds datasets eval sharegpt.json --fix render-placeholders \
    --var student=高中生 --var subject=物理
```

## The LLM judge (`--llm-judge`)

Samples up to `--sample N` records (default 20, deterministic by seed)
and asks a judge model to score each on three 1–5 axes:

1. **groundedness** — is the output supported by the input?
2. **correctness** — is it factually right?
3. **clarity** — is it well-formed and non-rambling?

The judge talks directly to an OpenAI-compatible `chat/completions`
endpoint using the model config's own `endpoint` + `apiKey`. It does
NOT go through the Easy-Dataset server, so you can run it against a
bare JSON file on a box with no backend.

If any axis mean is below 3.0, `evaluate()` promotes it to a
corresponding `judge_*_low` failing check and the attribution table
points at `datasets generate` (model/prompt fix).

**Cost control:** judge is **off by default**. Turn it on explicitly
with `--llm-judge`. The per-record call is one chat completion, so
sample size × axes is the token ceiling.

## Attribution → re-run suggestions

When a rule fails, the report's `attribution[]` entry tells you which
pipeline step owns the fix. Current mappings (hand-maintained in
`core/eval_attribution.py`):

| Rule | Step | Fix |
|---|---|---|
| `instruction_empty_rate` | `questions generate` | re-run, check template |
| `input_empty_rate` | `export` | re-export with `--include-chunk`; or `--fix chunk-join` |
| `output_empty_rate` | `datasets generate` | re-run, raise maxTokens |
| `output_double_encoded` | `post-process` | `--fix unwrap-labels` |
| `placeholder_leak_rate` | `datasets generate` | pass `{{var}}` flags; or `--fix render-placeholders` |
| `multi_turn_malformed` | `datasets generate --rounds N` | re-run with higher rounds |
| `judge_groundedness_low` | `datasets generate` | stronger model / tighter answer prompt |
| `judge_correctness_low` | `datasets generate` | stronger generation model |
| `judge_clarity_low` | `prompts set` | update answer prompt |

Unknown rules simply don't appear in `attribution[]` — safer than a
misleading suggestion.

## Session history (`eval-history`)

Every call to `datasets eval` appends one entry (file, sha-prefix,
verdict, failing rule names, task type, sample size, timestamp) to
`~/.easyds/session.json` under the current project. The list is
trimmed to the most recent 20 entries per project.

Use cases:
- An agent retrying the same file can spot loops ("I hit
  `input_empty_rate` three times — the fix isn't sticking").
- Review progress across a refine session: `easyds --json datasets
  eval-history | jq '.[].verdict'`.

Opt out per-call with `--no-history` (e.g. in CI where the session file
is ephemeral).

## Closed-loop example — the case-2 sentiment bug

This is the canonical failure mode the feature was designed to catch.
The live case-2 run produced 8 perfectly-formatted Alpaca records where
every `input` was empty — untrainable, no easy way to notice by reading
a few samples.

```bash
# 1. Initial eval — reports the broken shape
$ easyds --json datasets eval sentiment-alpaca.json | jq .verdict
"fail"

$ easyds --json datasets eval sentiment-alpaca.json \
    | jq '.attribution[].fix'
"chunk-join"
"unwrap-labels"

# 2. Agent acts on the suggestions
$ easyds --json chunks list > chunks.json
$ easyds datasets eval sentiment-alpaca.json \
    --fix chunk-join --chunks-file chunks.json
$ easyds datasets eval sentiment-alpaca.json --fix unwrap-labels

# 3. Re-eval → pass
$ easyds --json datasets eval sentiment-alpaca.json | jq .verdict
"pass"
```

An agent running this loop never has to understand Easy-Dataset's
internals: the **report + attribution** is the entire interface.

## Exit codes

| Code | Meaning | Typical use |
|---|---|---|
| `0` | all checks pass | CI proceeds |
| `1` | warnings only | CI proceeds, optional review |
| `2` | hard failures | CI blocks |

JSON mode (`--json`) **never** raises via exit code — JSON callers are
agents and should read `report.exit_code` themselves. Non-JSON mode
raises a `click.exceptions.Exit` so shell pipelines gate correctly.
