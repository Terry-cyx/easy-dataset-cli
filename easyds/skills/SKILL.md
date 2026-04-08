---
name: "easyds"
description: "Stateful CLI for Easy-Dataset (https://github.com/ConardLi/easy-dataset). Lets AI agents drive the full LLM fine-tuning dataset pipeline over HTTP: create project → register model → upload doc → chunk → generate questions → generate answers → export Alpaca/ShareGPT JSON. Hard dependency on a running Easy-Dataset Next.js server."
---

# easyds — Skill Index

A stateful command-line interface that lets AI agents drive [Easy-Dataset](https://github.com/ConardLi/easy-dataset) end-to-end. This file is an **index**; each topic below points to a focused doc under [`reference/`](reference/).

> **Read first if new to the tool**: [01-getting-started](reference/01-getting-started.md) → [03-canonical-pipeline](reference/03-canonical-pipeline.md) → [06-operating-rules](reference/06-operating-rules.md).

## Core reference

| File | Read when you need to… |
|---|---|
| [`reference/01-getting-started.md`](reference/01-getting-started.md) | Install the CLI, start the server, verify the connection, pick a provider |
| [`reference/02-command-reference.md`](reference/02-command-reference.md) | Look up a command group or global flag |
| [`reference/03-canonical-pipeline.md`](reference/03-canonical-pipeline.md) | See the default 7-step recipe (use this first) |
| [`reference/04-custom-prompts.md`](reference/04-custom-prompts.md) | **Write a custom prompt** — read this BEFORE writing one, or it will fail silently |
| [`reference/05-decision-guide.md`](reference/05-decision-guide.md) | Choose between multiple options (chunk size, GA count, export format, etc.) |
| [`reference/06-operating-rules.md`](reference/06-operating-rules.md) | Avoid the **14** known operational pitfalls (`--ga`, `model use`, client timeout, concurrency, language, …) |
| [`reference/07-agent-protocol.md`](reference/07-agent-protocol.md) | Drive the CLI from an AI agent — exit codes, retry policy, polling pattern |
| [`reference/08-task-settings.md`](reference/08-task-settings.md) | Set project-wide knobs (chunk size, concurrency, MGA defaults, eval ratios, MinerU token) |
| [`reference/09-pdf-and-data-cleaning.md`](reference/09-pdf-and-data-cleaning.md) | Pick a PDF parser (MinerU / vision) or batch-clean noisy chunks before generating Q&A |
| [`reference/10-question-templates.md`](reference/10-question-templates.md) | Build a **classification** dataset (labels) or **structured extraction** dataset (JSON schema) |

## Workflow recipes (`reference/workflows/`)

End-to-end command sequences for specific scenarios. Each recipe assumes you've already read the core reference above.

| File | Scenario |
|---|---|
| [`workflows/custom-prompt-pipeline.md`](reference/workflows/custom-prompt-pipeline.md) | ★ End-to-end with custom question + evaluation prompts (production-grade recipe distilled from a real CFX_tutorials run) |
| [`workflows/sentiment-classification.md`](reference/workflows/sentiment-classification.md) | ★ Build a labeled classification dataset (案例 2: separator chunking + question template + label set) |
| [`workflows/document-cleansing.md`](reference/workflows/document-cleansing.md) | ★ Long noisy PDF → batch cleansing → scored Q&A → score-filtered export (案例 4: full retake using `chunks clean-task`) |
| [`workflows/image-vqa.md`](reference/workflows/image-vqa.md) | Visual Question Answering from a directory of images (案例 1) |
| [`workflows/multi-turn-distill.md`](reference/workflows/multi-turn-distill.md) | Multi-turn dialogue distillation from a topic tree (案例 3: 物理学多轮对话) |
| [`workflows/quality-control.md`](reference/workflows/quality-control.md) | Custom-separator chunking + cleaning + multi-dim evaluation + score-filtered export |
| [`workflows/ga-mga-pairs.md`](reference/workflows/ga-mga-pairs.md) | Genre-Audience pair diversification |
| [`workflows/eval-and-blind-test.md`](reference/workflows/eval-and-blind-test.md) | Benchmark evaluation + pairwise blind-test |
| [`workflows/domain-tree-editing.md`](reference/workflows/domain-tree-editing.md) | Manually curate the LLM-built domain tree |
| [`workflows/import-clean-optimize.md`](reference/workflows/import-clean-optimize.md) | Import an existing dataset, clean chunks, optimize rows |
| [`workflows/background-tasks.md`](reference/workflows/background-tasks.md) | Inspect/cancel/wait on async tasks (`task wait` pattern) |

## The 30-second elevator pitch

1. **`easyds` is a thin HTTP client** — all real work happens on the Easy-Dataset Next.js server. The server is the source of truth.
2. **Pipeline**: `files upload → chunks split → ga generate → questions generate --ga → datasets generate → datasets evaluate → export run`.
3. **Always pass `--json`** in agent code. Errors land on stderr as `{"error": "...", "message": "..."}`.
4. **`questions generate` and `datasets generate` are slow** — server iterates serially with no concurrency. Background + poll, don't await.
5. **Custom prompts must output strict JSON** — `["...", "..."]` for question prompts, `{"score": 4.5, "evaluation": "..."}` for evaluation prompts. Wrong format = silent batch loss.
6. **Client `ReadTimeout` ≠ task failure.** The server is persistent; re-list the resource, don't re-issue the command.

## When something goes wrong

1. Re-run the failing command with `--json` and split stderr from stdout.
2. Read the **literal server error** in the `BackendError.message` — the fix is usually in the message.
3. Check [`reference/06-operating-rules.md`](reference/06-operating-rules.md) — your symptom may match one of the 10 hard rules.
4. Check exit codes against [`reference/07-agent-protocol.md`](reference/07-agent-protocol.md) — they tell you whether to retry, ask the user, or fix and re-issue.

## Version

1.0.1
