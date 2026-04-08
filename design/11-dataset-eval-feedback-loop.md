# Design — `easyds datasets eval` + Feedback Loop

> Status: **DRAFT — awaiting user review on §3 Key Decisions before implementation.**
> Owner: refine-round-3
> Related: `spec/04-coverage-gap.md`, `tests_examples/case2-sentiment/` (live failure that motivated this)

## 1. Why — what's the problem we're solving?

Today the Easy-Dataset pipeline is **write-only**: you run
`upload → split → questions generate → datasets generate → export` and
get an Alpaca/ShareGPT file at the end. **Nothing in the pipeline looks at
the final file**. If the file is broken — missing fields, ungrounded
answers, unrendered placeholders, collapsed label encodings — you find out
later, during training, when it's already too late.

The sentiment-classification case (case 2) is the smoking gun: the live
run produced 8 perfectly-formatted Alpaca records where **every `input`
field was empty**, making the dataset completely untrainable. The bug was
three layers deep (server never wrote `chunkContent` → `--include-chunk`
had nothing to embed → export silently emitted empty strings), and the
CLI never noticed. A schema check would have caught this in 5 ms.

The insight is bigger than one bug though: **every step in the pipeline
has decisions** (prompt text, chunk separator, question count, model
temperature, label set, field-map). If the final file has a problem, we
want to know **which step owns the fix**, and we want an LLM agent to be
able to loop back and retry just that step. That's the unique thing
Easy-Dataset-CLI can offer that the GUI can't: **closed-loop, agent-driven
dataset iteration.**

## 2. User stories

1. **Schema gate (deterministic, no LLM needed).** A CI step runs
   `easyds datasets eval sentiment-alpaca.json` and fails the build
   because `input_empty_rate = 1.0 > 0.1`. Zero tokens burned.

2. **LLM judge on a sample.** `easyds datasets eval --llm-judge
   --sample 20` picks 20 rows, asks a judge model for
   `groundedness / correctness / clarity`, and prints a scorecard plus
   the 3 worst samples with reasons.

3. **Attribution + suggestion.** When a check fails, the report includes
   **which step(s) to re-run** and **what parameter to change**. Example:
   ```json
   {
     "check": "input_empty_rate",
     "value": 1.0,
     "verdict": "fail",
     "attribution": {
       "step": "export",
       "command": "easyds export run",
       "suggested_change": "add --include-chunk --field-map chunkContent=input",
       "root_cause_hint": "label-template questions don't persist chunkContent on dataset rows; client-side join required"
     }
   }
   ```

4. **Agent-driven closed loop.** An agent runs:
   ```bash
   easyds datasets eval out.json --json > report.json
   # agent reads report.json, sees step=export, applies suggested change
   easyds export run <args with suggested change>
   easyds datasets eval out.json --json  # re-verify
   ```
   The agent never has to understand Easy-Dataset internals — the
   report is enough.

## 3. Key decisions (please weigh in before implementation)

### Decision 3.1 — Scope of the first pass

| Option | What ships | Cost | My recommendation |
|---|---|---|---|
| **A. Schema-only** | Deterministic rules only, no LLM | 0 tokens | ✅ **Start here.** It already would have caught case 2. |
| B. Schema + LLM judge | Add `--llm-judge` flag as second pass | N tokens per sample | Phase 2, once schema is solid |
| C. Schema + judge + auto-apply | CLI auto-retries failed steps | Dangerous | ❌ Let agents drive; don't auto-apply from inside CLI |

**My pick: A first, then B in the same commit if design is clean.** C is
a footgun — if the fix is wrong, we silently burn credits in a loop.

### Decision 3.2 — What rules go into the schema checker?

Each rule is a pure function `(records, format) → (metric_value, verdict)`.
My starter set:

| Rule | Metric | Default threshold | Motivating case |
|---|---|---|---|
| `instruction_empty_rate` | fraction of rows with empty `instruction` | `0.0` (fail if any) | generic |
| `input_empty_rate` | fraction with empty `input` — **only checked if the task type expects one** (see 3.3) | `0.1` | **case 2** |
| `output_empty_rate` | fraction with empty `output` | `0.0` | generic |
| `output_double_encoded` | `output` is a JSON-stringified array like `"[\"正面\"]"` | `0.0` | **case 2** (label template quirk) |
| `placeholder_leak_rate` | fraction containing unresolved `{{var}}` | `0.0` | **case 3** (`{{student}}`) |
| `duplicate_instruction_rate` | fraction that share an identical `instruction` with no `input` to disambiguate | `0.5` | **case 2** before the fix |
| `mean_output_length` | chars — warn if < 10 or > 4000 | warn-only | generic |
| `sample_size` | len(records) | warn if < 8 | generic |
| `cot_present_rate` | (only if user asked for `--include-cot`) fraction with `<think>…</think>` | warn if < 0.5 | **case 4** (some rows had no CoT) |
| `multi_turn_well_formed` | ShareGPT only — every conv alternates user/assistant, has ≥3 msgs | `1.0` | case 3 |

**Open question:** should warn-only rules have a `--strict` flag to
promote them to fails?

### Decision 3.3 — How does the checker know the "expected shape"?

The `input_empty_rate` rule can't blindly fail every Alpaca file — for a
VQA or Q&A task, empty `input` is fine; for a classification task with
a template question, empty `input` is a disaster.

**Three options:**

- **A. Explicit `--task-type {qa,classification,vqa,multi-turn}` flag.**
  Simple, no magic, user tells us.
- **B. Auto-detect from content.** If `instruction` is identical for every
  row → it's a template task → `input` must be non-empty. Fragile.
- **C. Read the task shape from the project's task-config.json.** Would
  require knowing the project id, defeats the purpose of running eval on
  a bare JSON file pulled from some other box.

**My pick: A + B as a fallback.** User passes `--task-type` when they
know; otherwise we auto-detect and print the detected type so they can
override.

### Decision 3.4 — LLM-judge prompt structure

For phase-2 `--llm-judge`:

- **One judge call per sampled record** — simple, parallelizable, robust
  to context limits, but expensive.
- **One judge call per batch of N** — cheaper, but the judge's attention
  is spread thin and score inflation becomes a real risk.

**My pick: per-record, with `concurrencyLimit` honored from
task-config.json.** Reuse existing `utils.tasks.wait_for` pattern.

**Judge rubric (3 axes, 1–5 each):**

1. **Groundedness** — is the output supported by the input? (skipped for
   open-ended QA with no input)
2. **Correctness** — is the output factually right given the question?
3. **Clarity** — is the output well-formed, readable, non-hallucinatory?

Plus a free-text `issues[]` list. Judge returns strict JSON; we parse +
fall back to "parse_error" verdict on malformed responses (no re-prompt
loops — keep it dumb and cheap).

**Open question:** separate `vqa_judge` with image-attachment support, or
skip VQA judging in phase 2? I'd **skip VQA in phase 2** — it needs the
vision model to see the image, which doubles the credential surface.

### Decision 3.5 — Attribution table: rule → step → suggested fix

This is the **unique value-add for agents**. The table is hand-maintained,
shipped as `easyds/core/eval_attribution.py`. Each rule maps to:

```python
ATTRIBUTION = {
    "input_empty_rate": {
        "step": "export",
        "command": "easyds export run",
        "suggested_change":
            "add --include-chunk; if still empty, the server didn't "
            "persist chunkContent on the dataset row (known issue for "
            "label-template tasks) — client-side join required via "
            "'easyds datasets eval --fix chunk-join'",
        "root_cause_hint": ...,
    },
    "placeholder_leak_rate": {
        "step": "datasets generate",
        "suggested_change":
            "pass all {{vars}} as flags, e.g. --role-a 用户 --role-b 助手",
        ...
    },
    "output_double_encoded": {
        "step": "export",
        "suggested_change":
            "add --field-map to unwrap, or use 'easyds datasets eval "
            "--fix unwrap-labels'",
        ...
    },
    # ... one row per rule
}
```

**Open question:** should `--fix <name>` actually perform the fix
(post-processing only, never re-run the server), or just print the
command? **My pick: post-processing fixes only (read JSON, rewrite JSON)
— never auto-invoke the server.** That keeps `datasets eval --fix` side-
effect-local and safe.

### Decision 3.6 — Report format

Two consumers, two formats:

- **Human** (default): colored table + top 3 failing samples with
  context, prints to stderr.
- **Agent** (`--json`): single JSON object to stdout, schema:
  ```json
  {
    "file": "out.json",
    "format": "alpaca",
    "task_type": "classification",
    "sample_size": 8,
    "checks": [{"name":"input_empty_rate","value":1.0,"verdict":"fail","threshold":0.1}],
    "failing_samples": [{"index":0,"excerpt":"...","issues":["input_empty"]}],
    "attribution": [{"rule":"input_empty_rate","step":"export","command":"...","suggested_change":"..."}],
    "verdict": "fail",
    "exit_code": 2
  }
  ```

Exit codes: `0` all-pass, `1` warn-only failures, `2` hard failures.

### Decision 3.7 — State / iteration history

Should we persist a per-project eval history so the agent can see "this
is the 3rd retry, previous tries hit rules X,Y"?

**Options:**

- **A. No history.** Each eval is stateless. Agents keep their own log.
- **B. `~/.easy-dataset-cli/session.json` gets an `eval_history[]`.**
  Cheap, local, already have the session module.
- **C. A table on the server.** Overengineering.

**My pick: B, lightweight.** Keeps last 20 eval results per project
keyed by file path + sha256 prefix, lets the agent run
`easyds datasets eval --history` to see iteration trend.

## 4. File layout

```
easyds/core/
  dataset_eval.py          # rules, runner, report
  eval_attribution.py      # rule → step → fix mapping (hand-maintained)
  eval_judge.py            # phase-2 LLM judge (flagged off by default)
  eval_fixes.py            # post-processing --fix handlers

easyds/cli.py
  datasets_grp.command("eval")       # main entrypoint
  datasets_grp.command("eval-history")  # phase-2, optional

tests/
  test_dataset_eval.py     # unit tests with golden fixtures
  fixtures/eval/
    good-alpaca.json
    case2-broken.json          # empty inputs — must fail input_empty_rate
    case3-placeholder-leak.json
    case3-good.json
    double-encoded-labels.json
    multi-turn-malformed.json

easyds/skills/reference/
  11-dataset-eval.md       # how agents invoke + interpret eval
```

## 5. CLI surface (proposed)

```bash
# Deterministic checks only (always fast, always free)
easyds datasets eval <file>
easyds datasets eval <file> --task-type classification
easyds datasets eval <file> --strict           # promote warns to fails
easyds datasets eval <file> --json             # agent mode

# LLM judge (phase 2; opt-in, burns tokens)
easyds datasets eval <file> --llm-judge --sample 20
easyds datasets eval <file> --llm-judge --sample 20 --model-config mc-abc

# Post-processing fixes (safe, no server calls)
easyds datasets eval <file> --fix chunk-join --chunks-file 03_chunks.json
easyds datasets eval <file> --fix unwrap-labels
easyds datasets eval <file> --fix render-placeholders --var student=高中生

# History (phase 2)
easyds datasets eval-history --file <file>
```

## 6. How agents will actually use this (end-to-end narrative)

An agent given the prompt "fix case 2" would:

1. `easyds datasets eval tests_examples/case2-sentiment/output/sentiment-alpaca.json --json`
2. Read `verdict: "fail"`, see `input_empty_rate: 1.0`, see
   `attribution[0].suggested_change`.
3. Try suggested change first:
   `easyds datasets eval <file> --fix chunk-join --chunks-file 03_chunks.json`
   (post-processing, no server round-trip, cheap).
4. Re-run eval: `easyds datasets eval <file> --json`.
5. If `verdict: "pass"`, commit. If still failing, escalate attribution
   to step=`datasets generate` and retry that.

**Key property:** every step is a single CLI call with machine-readable
output. No GUI, no human-in-the-loop, no hidden state. This is the
closed-loop property that `easyds` uniquely enables.

## 7. Test plan (Phase 4/5 of HARNESS.md)

- **Unit tests** (`test_dataset_eval.py`): one golden fixture per rule,
  asserting both pass and fail cases. The case 2 broken file goes in
  `fixtures/eval/case2-broken.json` verbatim so we never lose the
  regression.
- **Integration** (`test_full_e2e.py::TestDatasetEvalLoop`): run the full
  case 2 pipeline against the stub server, feed the output through
  `datasets eval`, assert verdict=fail, apply `--fix chunk-join`, re-run
  eval, assert verdict=pass.
- **No live LLM tests** for phase 1 (no LLM calls). Phase 2 judge will
  need a mocked `backend.post` for model calls.

## 8. Non-goals for this round

- Auto-retrying server-side steps from inside the CLI (too dangerous;
  agents can drive it explicitly)
- VQA image judging
- A "dataset linter" for Chinese vs English encoding issues
- Training-curve based eval (requires actually training a model)

---

## Please review these decisions before I implement

The ones I'd most like your call on:

1. **§3.1** — ship schema-only first, or schema+judge together in one
   commit?
2. **§3.3** — is `--task-type` explicit + auto-detect fallback OK, or do
   you want strict explicit-only?
3. **§3.5** — should `--fix` ever actually re-run the server, or only
   ever post-process the JSON?
4. **§3.7** — persist eval history in session.json (option B), or
   stateless (option A)?
5. **Attribution table** — do you want to review the full rule→step→fix
   mapping before I encode it, or is it fine to go with the sketch above
   + iterate based on real runs?

Once you give me thumbs on these, I'll implement + test + commit in one
refine pass.
