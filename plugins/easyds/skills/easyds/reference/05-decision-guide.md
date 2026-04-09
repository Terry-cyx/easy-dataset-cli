# 05 — Decision Guide

When `easyds` gives you multiple options for the same job, here's how to choose.

## Chunk size: `--text-split-min` / `--text-split-max`

| Document shape | Recommendation | Why |
|---|---|---|
| Long-form prose / textbook | `min 2500, max 4000` | Each chunk holds a full subsection; LLM has enough context to write a coherent question |
| Case collection (each case 30–60 lines self-contained) | `min 1500, max 3000` | Aim for ≈ 1 case per chunk so questions stay self-contained |
| FAQ / Q&A source | `min 800, max 1500` | Each Q-A is a chunk; very small |
| Code / API docs | `min 1500, max 2500` | Function-sized chunks |

**Rule of thumb**: target chunk size ≈ "the smallest unit a single question could legitimately ask about, plus 1 paragraph of supporting context."

## Chunking strategy: `--strategy`

| Strategy | When | Trade-off |
|---|---|---|
| `text` | **Default.** Source has no clean structural delimiters | Fast, deterministic, no LLM call |
| `document` | Source has implicit semantic sections you want preserved | Calls the LLM — slow (~30s/chunk) |
| `separator` (with `--separator '## '` and `--content-file`) | Source has clean delimiters: chapter headings (`## `), case rules (`---`), etc. | Fast, **most predictable** for structured docs |
| `code` | Source code | Splits at function/class boundaries |

When in doubt, **try `text` first** — it's the fastest and most predictable.

## GA (Genre-Audience) pairs

| Goal | GA pair count | Cost multiplier |
|---|---|---|
| Tightest budget | **0** (don't run `ga generate`) — questions still work but no diversification | 1× |
| Reasonable diversification | **1–2** active pairs | 1–2× |
| Maximum diversity (the official default) | **5** active pairs | **5×** |

**Always `ga generate` first to get 5, then `ga set-active --inactive` the ones you don't need.** It's faster than re-running generation.

> ⚠️ Each GA pair multiplies the question generation time by 1× — 5 active pairs = 5× slower. The server iterates serially through chunks AND through GA pairs.

## Question generation: `--ga` flag

| Flag | Behavior | Recommendation |
|---|---|---|
| `--ga` | Uses active GA pairs; works correctly | **Always use this** |
| (no flag) | Non-GA mode | **Broken on the server** (`primaryGaPair is not defined` ReferenceError). Do not use |

## Answer mode: single-turn vs `--rounds N`

| Mode | Endpoint | Use case |
|---|---|---|
| Single-turn (default) | `/api/projects/{id}/datasets` | Standard SFT data |
| `datasets generate --rounds 5 --role-a 学生 --role-b 老师 --system-prompt-file ...` | `/dataset-conversations` | Multi-turn dialogue training |

Multi-turn datasets **must** be exported via `export conversations` and **only** support ShareGPT format.

## Evaluation filter: `--score-gte` / `--score-lte`

| Use | Filter | Typical value |
|---|---|---|
| Final SFT training data | `--score-gte` | `4` (out of 5) |
| Build a "hard examples" set for analysis | `--score-lte` | `2` |
| Get everything | `--all` (no filter) | — |

## Export format: `--format`

| Format | Output shape | Best for |
|---|---|---|
| `alpaca` | `{instruction, input, output, system}` | LoRA SFT, single-turn |
| `sharegpt` | `{conversations: [{from, value}, ...]}` | OpenAI-compatible, multi-turn |
| `multilingual-thinking` | Alpaca + explicit `cot` field | Reasoning model distillation |

`--include-cot` adds the chain-of-thought to `output` (alpaca/sharegpt) — set this when training reasoning models.

## File type: `--file-type json|jsonl|csv`

| Type | When |
|---|---|
| `json` (default for alpaca) | Small datasets (< 100 MB), human-readable |
| `jsonl` (default for sharegpt) | Streaming, large datasets, line-by-line trainer compatibility |
| `csv` | Spreadsheet inspection, tabular ML pipelines |

## Train / valid / test split: `--split 0.7,0.15,0.15`

Deterministic by SHA1 of record id — same input ⇒ same split. Writes `<output>-train.<ext>`, `<output>-valid.<ext>`, `<output>-test.<ext>` next to the main output.
