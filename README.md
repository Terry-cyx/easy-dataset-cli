# easyds

**A stateful command-line interface for [Easy-Dataset](https://github.com/ConardLi/easy-dataset).**

`easyds` lets you (or an AI agent) drive the full LLM fine-tuning dataset-generation pipeline from a terminal:

```
upload document → chunk → generate questions → generate answers → evaluate → export Alpaca / ShareGPT
```

It is a thin HTTP client that talks to a running Easy-Dataset Next.js server. The server owns all state (project DB, LLM calls, prompts); `easyds` is the remote control.

---

## Why

Easy-Dataset is a great GUI for building LLM SFT corpora, but the GUI is hard to drive from CI, hard to call from an AI agent, and hard to script. `easyds`:

- Exposes **every** documented Easy-Dataset capability as a CLI subcommand (~80 commands across 17 groups).
- Has a **`--json` mode** that emits parseable output for every command, with a stable exit-code protocol so agents can react to failures.
- Ships an **AI-agent skill index** (`easyds/skills/SKILL.md`) so agents discover the tool and learn the operating rules without needing to crawl the source.
- Includes a polished **interactive REPL** with persistent history, branded prompt, and tab completion.
- Has been validated by two real end-to-end production runs against Kimi-K2.5, with **246 unit + integration tests** (mocked, stub-server, and subprocess-installed).

## Hard prerequisite: a running Easy-Dataset server

`easyds` does **not** reimplement chunking, domain-tree generation, or LLM calls. It forwards everything to a real Easy-Dataset server over HTTP.

```bash
git clone https://github.com/ConardLi/easy-dataset
cd easy-dataset
pnpm install        # first time only
pnpm dev            # serves http://localhost:1717
```

`easyds` defaults to `http://localhost:1717`. Override with `--base-url URL` or `EDS_BASE_URL`.

> Easy-Dataset has **no built-in authentication**. Run it on localhost or behind your own auth proxy.

## Install

```bash
# With uv (recommended — fastest, isolated tool install):
uv tool install easy-dataset-cli

# Or with uv into the current environment:
uv pip install easy-dataset-cli

# Or with plain pip:
pip install easy-dataset-cli
```

Requires Python 3.10+. The PyPI distribution is `easy-dataset-cli`; the installed binary is `easyds`.

### Editable install from a clone (developers)

```bash
git clone https://github.com/Terry-cyx/easy-dataset-cli
cd easy-dataset-cli

# uv-managed: creates .venv, locks deps, installs in editable mode
uv sync --extra test
uv run easyds --version
uv run pytest                       # → 246 passed, 1 skipped

# or, plain pip:
pip install -e ".[test]"
easyds --version
pytest
```

## Quickstart — the canonical 7-step pipeline

```bash
# 0. Verify the server is reachable.
easyds --json status

# 1. Create a project.
easyds --json project new --name my_dataset

# 2. Register an LLM model and activate it (writes both local session
#    and the server-side defaultModelConfigId — required for GA / image VQA).
easyds --json model set \
    --provider-id openai \
    --endpoint   https://api.openai.com/v1 \
    --api-key    sk-... \
    --model-id   gpt-4o-mini
easyds --json model use <id-from-step-2>

# 3. Upload a document (.md or .pdf only).
easyds --json files upload ./spec.md

# 4. Chunk it (also builds a domain tree via the LLM).
easyds --json chunks split --file spec.md

# 5. Generate questions. --ga is REQUIRED — non-GA mode is broken server-side.
easyds --json questions generate --ga --language 中文

# 6. Generate answers + chain-of-thought for every unanswered question.
easyds --json datasets generate --language 中文

# 7. Export.
easyds --json export run \
    -o ./alpaca.json \
    --format alpaca \
    --all --overwrite
```

For `--json` agent automation, scenario recipes, custom prompts, and the operating rules, read [`easyds/skills/SKILL.md`](easyds/skills/SKILL.md) — it is structured as a navigable index over the `easyds/skills/reference/` library.

## Command groups

| Group | Purpose |
|---|---|
| `status` | Server reachability + active session |
| `project` | Project lifecycle (new/list/use/info/delete) |
| `model` | Per-project LLM config (text or vision) |
| `prompts` | Custom prompt overrides via `/api/projects/{id}/custom-prompts` |
| `files` | Document & image upload, list, prune |
| `chunks` | Chunking with text/document/separator/code strategies |
| `tags` | Manually edit the LLM-built domain tree |
| `ga` | Genre-Audience pair management for question diversification |
| `questions` | Question generation, manual CRUD, templates |
| `datasets` | Answer + CoT generation, multi-dim evaluation, import/optimize |
| `task` | Background task system (`task wait` for async jobs) |
| `distill` | Zero-shot distillation from a topic tree (no source documents) |
| `eval` / `eval-task` / `blind` | Benchmark management + automated multi-model evaluation |
| `export` | Alpaca / ShareGPT / multilingual-thinking export with rich filters |
| `repl` | Interactive shell (also the default when no subcommand is given) |

Run `easyds <group> --help` or `easyds <group> <subcommand> -h` for full options.

## Output formats

| Format | Shape | Best for |
|---|---|---|
| `alpaca` | `{instruction, input, output, system}` | LoRA SFT, single-turn |
| `sharegpt` | `{conversations: [{from, value}, …]}` | OpenAI-compatible, multi-turn |
| `multilingual-thinking` | Alpaca + explicit `cot` field | Reasoning model distillation |

`--include-cot` embeds chain-of-thought into `output` for the alpaca/sharegpt formats.
`--score-gte 4` filters to records the evaluator scored ≥ 4 (out of 5).
`--split 0.7,0.15,0.15` writes deterministic train/valid/test files.

## For AI agents

The package ships an AI-agent skill index at [`easyds/skills/SKILL.md`](easyds/skills/SKILL.md) and 16 reference docs under [`easyds/skills/reference/`](easyds/skills/reference/). The most important entries:

- [`reference/03-canonical-pipeline.md`](easyds/skills/reference/03-canonical-pipeline.md) — the default 7-step recipe
- [`reference/04-custom-prompts.md`](easyds/skills/reference/04-custom-prompts.md) — **must-read** before writing a custom prompt (output format constraints)
- [`reference/06-operating-rules.md`](easyds/skills/reference/06-operating-rules.md) — 10 actionable rules learned from production runs (always `--ga`, `model use` writes server, client `ReadTimeout` ≠ failure, …)
- [`reference/07-agent-protocol.md`](easyds/skills/reference/07-agent-protocol.md) — `--json` mode + exit codes + retry policy + polling pattern
- [`reference/workflows/`](easyds/skills/reference/workflows/) — 9 scenario recipes (custom-prompt pipeline, image VQA, multi-turn distillation, quality control, GA pairs, eval & blind test, …)

## Development

See the [editable-install section above](#editable-install-from-a-clone-developers) for the standard `uv sync` flow. Opt-in live integration test:

```bash
EDS_LIVE_TESTS=1 uv run pytest tests/test_full_e2e.py::TestLiveBackend
# requires a running Easy-Dataset server + valid LLM API keys
```

## Known server quirks

A handful of Easy-Dataset server-side bugs the CLI works around. See [`docs/SERVER_QUIRKS.md`](docs/SERVER_QUIRKS.md). The most user-visible:

1. **Always pass `--ga`** to `questions generate` — non-GA mode crashes server-side.
2. **Always run `model use` after `model set`** — it writes both the local session and the server-side `defaultModelConfigId` (the latter is required for GA / image-VQA endpoints).
3. **Custom prompts must produce strict JSON** (`["...", "..."]` for question prompts; `{"score": 4.5, "evaluation": "..."}` for eval prompts). Wrong output format = silent batch loss.
4. **Client `ReadTimeout` ≠ task failure.** The server is single-threaded but persistent. After a timeout, **re-list** the resource — do not re-issue the command.

## License

AGPL-3.0-or-later. See `LICENSE`.
