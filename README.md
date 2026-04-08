<div align="center">

<img src="assets/banner.svg" alt="easyds — drive Easy-Dataset from your terminal" width="820">

<br>

**Drive [Easy-Dataset](https://github.com/ConardLi/easy-dataset) from your terminal — or your agent.**

*Easy-Dataset's GUI is great for humans clicking through a dataset build.*
*But CI pipelines and LLM agents need a CLI — so here it is.*

<p align="center">
  <a href="#-quick-start"><img src="https://img.shields.io/badge/Quick_Start-3_min-22d3ee?style=for-the-badge" alt="Quick Start"></a>
  <a href="#%EF%B8%8F-commands"><img src="https://img.shields.io/badge/Commands-~80_across_17_groups-34d399?style=for-the-badge" alt="Command coverage"></a>
  <a href="easyds/skills/SKILL.md"><img src="https://img.shields.io/badge/Agent_Skill-included-8b5cf6?style=for-the-badge" alt="Agent skill"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL_3.0-eab308?style=for-the-badge" alt="License"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/CLI-Click_8-blue" alt="Click">
  <img src="https://img.shields.io/badge/transport-HTTP%2FJSON-orange" alt="HTTP/JSON">
  <img src="https://img.shields.io/badge/tests-246_passed-22c55e" alt="Tests">
  <img src="https://img.shields.io/badge/status-v1.0.1-f97316" alt="Version">
</p>

[Why easyds](#-why-easyds) · [Quick Start](#-quick-start) · [Architecture](#-architecture) · [Commands](#%EF%B8%8F-commands) · [Agent Skill](easyds/skills/SKILL.md) · [Server quirks](docs/SERVER_QUIRKS.md)

</div>

---

## 📰 News

- **2026-04-08** 🎬 **easy-dataset-cli v1.0.1** — first public release. 17 command groups, ~80 subcommands, 246 unit + integration tests, validated by two real end-to-end production runs against Kimi-K2.5.
- **2026-04-08** 🧠 **Agent skill shipped** — `easyds/skills/SKILL.md` plus 16 reference docs and 9 scenario workflows under `easyds/skills/reference/`, so an LLM can drive the full pipeline without crawling the source.

---

## 🤔 Why easyds?

Easy-Dataset is the cleanest open-source pipeline for turning documents into LLM SFT corpora — chunking, domain trees, GA-pair diversification, question/answer generation, multi-dim eval, Alpaca/ShareGPT export. But the only first-class interface is a Next.js GUI. That makes it hard to drive from CI, hard to call from an AI agent, and hard to script.

Today's choices for "automate Easy-Dataset" are awful:

- **Click through the GUI** — fine for one project, miserable for ten.
- **Hand-roll `curl` calls** against the Next.js routes — every team rebuilds the same upload / chunk / question / answer / export loop, and silently steps on the same server quirks.
- **Re-implement the pipeline** — throws away the prompt engineering, the GA expansion, the evaluator, and the multi-format exporter that already work.

`easyds` is the missing layer:

- **One CLI**, ~80 commands across 17 groups, covering **every documented Easy-Dataset capability** — files, chunks, tags, GA pairs, questions, datasets, eval, blind test, distillation, custom prompts, and export.
- **`--json` mode** on every command, with a stable exit-code protocol so agents can react to failures.
- **A polished REPL** with persistent history, branded prompt, and tab completion — `easyds` with no subcommand drops you in.
- **An agent skill index** so an LLM picks up the operating rules (`always --ga`, `model use writes server`, `ReadTimeout ≠ failure`, …) without prior context.
- **246 tests green** — unit, mocked HTTP, stub-server, and installed-subprocess — and **two real production runs** against Kimi-K2.5 already shipped to disk.

> Like a container CLI standardized how you talk to a container runtime, **easyds** standardizes how you (and your agents) talk to Easy-Dataset.

---

## 🏛 Architecture

<div align="center">
  <img src="assets/architecture.svg" alt="easyds architecture: Click CLI talks HTTP/JSON to a running Easy-Dataset Next.js server, which owns chunks, questions, datasets, eval, and export, persisted in SQLite via Prisma" width="900">
</div>

`easyds` is a **thin HTTP client**, not a re-implementation. The Easy-Dataset Next.js server owns all the state, the prompt library, and the LLM calls. `easyds` is the remote control:

| Layer | What lives there |
|---|---|
| **`easyds` CLI** | Click app, REPL, `--json` mode, session state in `~/.easyds/`, exit-code protocol |
| **HTTP / JSON** | Plain `requests` client, default base URL `http://localhost:1717`, override via `--base-url` or `EDS_BASE_URL` |
| **Easy-Dataset server** | Next.js routes under `/api/projects/{id}/…`, Prisma ORM, background-task runner, prompt library |
| **Storage** | `prisma/db.sqlite` — projects, chunks, questions, datasets, tags, GA pairs, eval results |
| **LLM providers** | Configured *per project* via `easyds model set` (OpenAI, Ollama, Zhipu, Kimi, OpenRouter, Bailian, MiniMax) |

For the full pipeline, custom-prompt rules, and the hard-won server quirks the CLI works around, see [`docs/SERVER_QUIRKS.md`](docs/SERVER_QUIRKS.md) and [`easyds/skills/SKILL.md`](easyds/skills/SKILL.md).

---

## 🚀 Quick Start

### 1. Start an Easy-Dataset server (one-time, hard prerequisite)

`easyds` does not reimplement chunking, domain-tree generation, or LLM calls. It forwards everything to a real Easy-Dataset server.

```bash
git clone https://github.com/ConardLi/easy-dataset
cd easy-dataset
pnpm install        # first time only
pnpm dev            # serves http://localhost:1717
```

> Easy-Dataset has **no built-in authentication**. Run it on localhost or behind your own auth proxy.

### 2. Install `easyds`

```bash
# With uv (recommended — fastest, isolated tool install):
uv tool install easy-dataset-cli

# Or with uv into the current environment:
uv pip install easy-dataset-cli

# Or with plain pip:
pip install easy-dataset-cli
```

Requires **Python 3.10+**. The PyPI distribution is `easy-dataset-cli`; the installed binary is **`easyds`**.

### 3. Run the canonical 7-step pipeline

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

That's the full loop: **status → project → model → upload → chunk → questions → answers → export** — reproducible, scriptable, agent-driveable.

---

## 🎬 Demo

> **Recording in progress.** A short terminal capture of the canonical 7-step pipeline against a real Easy-Dataset server will land here. Want to contribute the recording? Use [`vhs`](https://github.com/charmbracelet/vhs) or [`asciinema`](https://asciinema.org/) and open a PR against `assets/demo.gif`.

In the meantime, two real end-to-end runs are documented in production form:

- **Kimi-K2.5 + Chinese spec doc** — full Alpaca export, 200+ Q&A pairs
- **Kimi-K2.5 + ANSYS CFX tutorials** — custom prompt pipeline, English Q&A, ShareGPT export

Reach out via Issues if you'd like the run logs as a reference.

---

## ✨ Features

### 🧠 Built for agents
- **`--json` on every command** with a stable exit-code protocol (`0` ok, `2` server error, `3` validation, `4` not found, …)
- **Agent skill index** at [`easyds/skills/SKILL.md`](easyds/skills/SKILL.md) + 16 reference docs + 9 scenario workflows
- **Operating rules** distilled from real production runs — `always --ga`, `model use` writes server, client `ReadTimeout` ≠ failure, custom prompts must produce strict JSON
- **Stable session state** under `~/.easyds/session.json` so agents don't re-thread `--project` through every call

### 🔌 Full Easy-Dataset coverage
- **17 command groups** mapping 1:1 to Easy-Dataset's API surface — projects, models, prompts, files, chunks, tags, GA pairs, questions, datasets, tasks, distill, eval, eval-task, blind, export, status, repl
- **Every documented feature** wrapped — chunking strategies, custom prompts, GA expansion, multi-dim evaluation, blind A/B testing, zero-shot distillation, multi-turn datasets, image VQA
- **Three export formats** — Alpaca, ShareGPT, multilingual-thinking — with `--include-cot`, `--score-gte`, and deterministic train/valid/test splits

### 🖥 Polished for humans too
- **Interactive REPL** with persistent history, branded prompt, and tab completion — `easyds` with no subcommand drops you in
- **Rich human output** by default; switch to `--json` only when you want a parser
- **Real test suite** — 246 unit + integration tests (mocked, stub-server, and installed-subprocess), 1 skipped, 0 failed

---

## ⚙️ Commands

`easyds` ships **17 command groups** covering **~80 subcommands**. The full surface:

| Group | Purpose | Server analogue |
|---|---|---|
| `status` | Server reachability + active session | `GET /api/projects` (cheapest probe) |
| `project` | Project lifecycle (new/list/use/info/delete) | `/api/projects` |
| `model` | Per-project LLM config (text or vision) | `/api/projects/{id}/model-config` |
| `prompts` | Custom prompt overrides | `/api/projects/{id}/custom-prompts` |
| `files` | Document & image upload, list, prune | `/api/projects/{id}/files` |
| `chunks` | Chunking with text/document/separator/code strategies | `/api/projects/{id}/split` |
| `tags` | Manually edit the LLM-built domain tree | `/api/projects/{id}/tags` |
| `ga` | Genre-Audience pair management | `/api/projects/{id}/ga-pairs` |
| `questions` | Question generation, manual CRUD, templates | `/api/projects/{id}/generate-questions` |
| `datasets` | Answer + CoT generation, multi-dim eval, import/optimize | `/api/projects/{id}/datasets` |
| `task` | Background task system (`task wait` for async jobs) | `/api/projects/{id}/tasks` |
| `distill` | Zero-shot distillation from a topic tree | `/api/projects/{id}/distill` |
| `eval` | Benchmark management | `/api/projects/{id}/eval-datasets` |
| `eval-task` | Automated multi-model evaluation | `/api/projects/{id}/eval-tasks` |
| `blind` | Blind A/B model testing | `/api/projects/{id}/blind-test` |
| `export` | Alpaca / ShareGPT / multilingual-thinking export | `/api/projects/{id}/datasets/export` |
| `repl` | Interactive shell (default when no subcommand is given) | — |

Run `easyds <group> --help` or `easyds <group> <subcommand> -h` for full options.

Environment: **`EDS_BASE_URL`**, **`EDS_PROJECT_ID`** for the client; session state lives at `~/.easyds/session.json`.

---

## 📦 Output formats

| Format | Shape | Best for |
|---|---|---|
| `alpaca` | `{instruction, input, output, system}` | LoRA SFT, single-turn |
| `sharegpt` | `{conversations: [{from, value}, …]}` | OpenAI-compatible, multi-turn |
| `multilingual-thinking` | Alpaca + explicit `cot` field | Reasoning model distillation |

`--include-cot` embeds chain-of-thought into `output` for the alpaca/sharegpt formats.
`--score-gte 4` filters to records the evaluator scored ≥ 4 (out of 5).
`--split 0.7,0.15,0.15` writes deterministic train/valid/test files.

---

## 🆚 Why not just call the API directly?

| Hand-rolled `curl` / `httpx` | easyds |
|---|---|
| Re-thread `projectId` through every request | `easyds project use` once, then forget it |
| Discover server quirks one production-run at a time | 13 known quirks already worked around (see [`docs/SERVER_QUIRKS.md`](docs/SERVER_QUIRKS.md)) |
| Re-invent the `model use` ↔ `defaultModelConfigId` dance | `easyds model use` writes both local session + server in one call |
| Parse non-uniform JSON responses by hand | `--json` mode emits a stable, documented shape per command |
| No agent guidance — LLM has to read the source | Ships an agent skill index with 9 scenario workflows |
| Long-running task = "did it work?" guesswork | `easyds task wait` polls until completion, with timeout |
| `ReadTimeout` looks like a fatal error | Documented as "re-list the resource, do not retry the command" |

---

## 🤖 For AI agents

The package ships an agent skill index at [`easyds/skills/SKILL.md`](easyds/skills/SKILL.md) and 16 reference docs under [`easyds/skills/reference/`](easyds/skills/reference/). The most important entries:

- [`reference/03-canonical-pipeline.md`](easyds/skills/reference/03-canonical-pipeline.md) — the default 7-step recipe
- [`reference/04-custom-prompts.md`](easyds/skills/reference/04-custom-prompts.md) — **must-read** before writing a custom prompt (output format constraints)
- [`reference/06-operating-rules.md`](easyds/skills/reference/06-operating-rules.md) — 10 actionable rules learned from production runs
- [`reference/07-agent-protocol.md`](easyds/skills/reference/07-agent-protocol.md) — `--json` mode + exit codes + retry policy + polling pattern
- [`reference/workflows/`](easyds/skills/reference/workflows/) — 9 scenario recipes (custom-prompt pipeline, image VQA, multi-turn distillation, quality control, GA pairs, eval & blind test, …)

---

## 🛠 Development

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

Opt-in live integration test (requires a running server + valid LLM API keys):

```bash
EDS_LIVE_TESTS=1 uv run pytest tests/test_full_e2e.py::TestLiveBackend
```

---

## 📂 Project layout

```
easy-dataset-cli/
├── easyds/              Python package (PyPI dist: easy-dataset-cli)
│   ├── cli.py             Click app, all 17 groups, ~80 subcommands
│   ├── core/              one module per Easy-Dataset domain
│   │   ├── project.py     /api/projects + defaultModelConfigId
│   │   ├── model.py       per-project model config
│   │   ├── files.py       multipart upload, prune, list-images
│   │   ├── chunks.py      split + edit + batch-edit + clean
│   │   ├── ga.py          genre × audience pair management
│   │   ├── questions.py   generate, templates, manual CRUD
│   │   ├── datasets.py    answers + cot + multi-dim eval
│   │   ├── export.py      alpaca / sharegpt / multilingual-thinking
│   │   └── …              13 more modules
│   ├── utils/
│   │   ├── backend.py     thin requests-based HTTP client
│   │   └── repl_skin.py   prompt-toolkit REPL chrome
│   └── skills/
│       ├── SKILL.md         slim agent skill index
│       └── reference/       16 reference docs + 9 workflow recipes
├── tests/
│   ├── test_core.py       234 unit tests (mocked HTTP)
│   └── test_full_e2e.py   13 E2E tests (stub server + subprocess)
├── docs/
│   └── SERVER_QUIRKS.md   13 known Easy-Dataset bugs the CLI works around
├── assets/                logo · banner · architecture (SVG)
├── pyproject.toml         PEP 621 + uv, entry: easyds = easyds.cli:main
├── uv.lock                committed for reproducibility
└── LICENSE                AGPL-3.0-or-later
```

---

## 🔗 Related projects

- **[Easy-Dataset](https://github.com/ConardLi/easy-dataset)** — the upstream Next.js + Prisma server `easyds` drives. Required dependency.

---

## 📄 License

AGPL-3.0-or-later — see [LICENSE](LICENSE). Same license as upstream Easy-Dataset.
