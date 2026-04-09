<div align="center">

<img src="assets/banner.svg" alt="easyds — drive Easy-Dataset from your terminal" width="820">

<br>

<img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white">
<img alt="CLI" src="https://img.shields.io/badge/CLI-Click_8-blue">
<img alt="Transport" src="https://img.shields.io/badge/transport-HTTP%2FJSON-orange">
<img alt="Tests" src="https://img.shields.io/badge/tests-287_passed-22c55e">
<img alt="Version" src="https://img.shields.io/badge/version-v1.0.1-f97316">
<img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green.svg">

**A CLI & agent harness for [Easy-Dataset](https://github.com/ConardLi/easy-dataset) — drive the full fine-tuning dataset pipeline from your terminal.**

[简体中文](./README.zh-CN.md) | **English** | [Türkçe](./README.tr.md)

[Features](#features) • [Install](#install) • [Documentation](plugins/easyds/skills/easyds/SKILL.md) • [Contributing](#contributing) • [License](#license)

If you like this project, please give it a Star ⭐️!

</div>

## Overview

**easy-dataset-cli** (`easyds`) is a stateful command-line harness that lets humans and AI agents drive every feature of [Easy-Dataset](https://github.com/ConardLi/easy-dataset) — the open-source pipeline for turning documents into LLM fine-tuning corpora — without ever touching the GUI. It speaks plain HTTP/JSON to a running Easy-Dataset Next.js server, so the upstream prompt library, chunkers, domain-tree builder, GA expander, evaluator, and exporters all keep working exactly as designed. On top of that foundation `easyds` layers a single polished CLI with 17 command groups, ~80 subcommands, a stable `--json` mode with exit-code protocol, an interactive REPL, and an embedded agent skill index — so CI pipelines, automation scripts, and LLM agents finally have a first-class interface. It is the missing layer between Easy-Dataset's powerful server and the automated workflows that want to use it.

<div align="center">
  <img src="assets/architecture.svg" alt="easyds architecture: Click CLI talks HTTP/JSON to a running Easy-Dataset Next.js server, which owns chunks, questions, datasets, eval, and export, persisted in SQLite via Prisma" width="860">
</div>

## News

🎉🎉 **easy-dataset-cli v1.0.1 — the dataset-eval feedback loop is here!** Beyond wrapping every Easy-Dataset capability, `easyds` now ships a unique closed-loop feature that the GUI cannot match: `datasets eval` runs deterministic schema checks on any final Alpaca/ShareGPT file, attributes failures to the pipeline step that owns the fix, applies safe local repairs via `--fix {chunk-join,unwrap-labels,render-placeholders}`, and optionally calls an LLM judge for groundedness/correctness/clarity scoring — all without touching the server. An LLM agent can now *evaluate its own dataset, decide which step to re-run, and repair rows locally* in a single tight loop. See [`plugins/easyds/skills/easyds/reference/11-dataset-eval.md`](plugins/easyds/skills/easyds/reference/11-dataset-eval.md) for the full story.

## Features

### 🤖 Built for AI Agents

- **`--json` on every command** with a stable exit-code protocol (`0` ok, `2` server error, `3` validation, `4` not found, …) so agents can react to failures without parsing prose
- **One-command install for Claude Code** — ship as a Claude Code plugin with a `/easyds-setup` slash command; no manual skill-path wiring
- **Embedded agent skill index** at [`plugins/easyds/skills/easyds/SKILL.md`](plugins/easyds/skills/easyds/SKILL.md) plus 16 reference docs and 11 scenario workflows — an LLM picks up the operating rules with zero prior context
- **Operating rules distilled from real production runs** — `always --ga`, `model use` writes server, client `ReadTimeout` ≠ failure, custom prompts must produce strict JSON
- **Stable session state** under `~/.easyds/session.json` so agents don't need to re-thread `--project` through every call

### 🔌 Full Easy-Dataset Coverage

- **17 command groups mapping 1:1 to the Easy-Dataset API** — projects, models, prompts, files, chunks, tags, GA pairs, questions, datasets, tasks, distill, eval, eval-task, blind, export, status, repl
- **Every documented capability wrapped** — chunking strategies (text/document/separator/code), custom prompts, GA expansion, multi-dim evaluation, blind A/B testing, zero-shot distillation, multi-turn datasets, image VQA
- **Per-project LLM configuration** supporting OpenAI, Ollama, Zhipu, Kimi, OpenRouter, Alibaba Bailian, MiniMax, and any OpenAI-compatible endpoint
- **13 known server quirks already worked around** — no more learning them one production run at a time (see [`docs/SERVER_QUIRKS.md`](docs/SERVER_QUIRKS.md))

### 📊 Dataset Evaluation & Feedback Loop (unique to easyds)

- **Deterministic schema checks** — 9 rules covering empty fields, double-encoded outputs, placeholder leaks, malformed multi-turn records, duplicates, and length outliers
- **Failure attribution** — every failing rule is cross-referenced to the pipeline step and command that owns the fix
- **Safe local repairs** — `--fix chunk-join`, `--fix unwrap-labels`, `--fix render-placeholders` repair common failure modes in-place without re-running the server
- **Optional LLM judge** — `--llm-judge` samples records and scores them on groundedness / correctness / clarity directly against any OpenAI-compatible endpoint
- **Session-scoped eval history** — `datasets eval-history` lets an agent detect retry loops and track refinement progress over time

### 📤 Export & Integration

- **Three export formats** — Alpaca, ShareGPT, multilingual-thinking — with `--include-cot`, `--score-gte`, and deterministic `--split train/valid/test`
- **Background-task orchestration** — `easyds task wait` polls long-running server jobs to completion with a timeout, so agents don't have to hand-roll polling logic
- **Per-tag balanced sampling** on export, matching Easy-Dataset's GUI semantics

### 🛠️ Developer Experience

- **287 tests green** — unit, mocked HTTP, stub-server, and installed-subprocess — plus two real end-to-end production runs against Kimi-K2.5 already shipped to disk
- **Editable install + uv-locked dependencies** for reproducible development
- **Single clean Python package** (PEP 621 + uv) with `easyds` as the only installed entry point

### 🌐 Human-Friendly Too

- **Interactive REPL** with persistent history, branded prompt, and tab completion — `easyds` with no subcommand drops you in
- **Rich human output** by default; switch to `--json` only when you want a parser
- **Multi-language documentation** — 简体中文 / English / Türkçe — including this README

## Quick Demo

> **Recording in progress.** A short terminal capture of the canonical 7-step pipeline against a real Easy-Dataset server will land here. Contributions welcome via [`vhs`](https://github.com/charmbracelet/vhs) or [`asciinema`](https://asciinema.org/) — open a PR against `assets/demo.gif`.

In the meantime, two real end-to-end runs are shipped as reproducible recipes:

- **Kimi-K2.5 + Chinese spec doc** — full Alpaca export, 200+ Q&A pairs
- **Kimi-K2.5 + ANSYS CFX tutorials** — custom prompt pipeline, English Q&A, ShareGPT export

See [`plugins/easyds/skills/easyds/reference/workflows/custom-prompt-pipeline.md`](plugins/easyds/skills/easyds/reference/workflows/custom-prompt-pipeline.md) for the production-grade recipe distilled from the CFX run.

## Install

Pick the path that matches how you'll drive `easyds`.

### 🥇 Claude Code users — one-click plugin

Inside Claude Code, run two slash commands:

```text
/plugin marketplace add Terry-cyx/easy-dataset-cli
/plugin install easyds@easy-dataset-cli
```

This bundles the **agent skill** (`SKILL.md` + 16 reference docs + 11 scenario workflows — Claude will auto-load them) and a **`/easyds-setup`** slash command. Then, still inside Claude Code, run:

```text
/easyds-setup
```

`/easyds-setup` will install the `easyds` CLI via `uv` (falling back to `pip`), probe for a running Easy-Dataset server, and — if the server isn't up — ask you which of three options you prefer (Docker one-liner, desktop client, or source). That's it; no manual `pip install`, no hand-written path to `SKILL.md`.

### 🥈 Everyone else — standalone CLI

> **⚠️ Heads-up on package names.** `easy-dataset-cli` is **not published on PyPI** (yet), and there is an unrelated PyPI package called `easyds` (a pandas helper) that will "install successfully" but ship no `easyds` binary. **Do not run `pip install easyds` or `pip install easy-dataset-cli`** — install from source instead.

Zero-install invocation (no tool install needed, runs directly from GitHub):

```bash
uvx --from git+https://github.com/Terry-cyx/easy-dataset-cli easyds --help
```

Or install once and keep it on your `PATH`:

```bash
# Preferred — isolated uv tool install from GitHub:
uv tool install --upgrade git+https://github.com/Terry-cyx/easy-dataset-cli

# Or into the current environment with uv:
uv pip install "git+https://github.com/Terry-cyx/easy-dataset-cli"

# Or with plain pip:
pip install "git+https://github.com/Terry-cyx/easy-dataset-cli"

# Or, for editable dev from a local clone:
git clone https://github.com/Terry-cyx/easy-dataset-cli
cd easy-dataset-cli && pip install -e .
```

Requires **Python 3.10+**. After install, verify with `easyds --version` — the output must report `1.0.1` or newer. If it prints `0.1.1`, you installed the unrelated namesquat package — `uv tool uninstall easyds` (or `pip uninstall easyds`) and retry the command above.

### Easy-Dataset server (hard prerequisite for both paths)

`easyds` is a thin HTTP client — it does not reimplement chunking, domain-tree generation, or LLM calls. It forwards everything to a real Easy-Dataset server, which must be reachable before any command runs. Pick one:

```bash
# Option 1 — Docker (fastest):
docker run -d --name easy-dataset -p 1717:1717 \
    -v "$PWD/local-db:/app/local-db" \
    -v "$PWD/prisma:/app/prisma" \
    ghcr.io/conardli/easy-dataset

# Option 2 — desktop client for Windows / macOS / Linux:
#   https://github.com/ConardLi/easy-dataset/releases/latest

# Option 3 — from source (developers):
git clone https://github.com/ConardLi/easy-dataset
cd easy-dataset && pnpm install && pnpm dev   # serves http://localhost:1717
```

> Easy-Dataset has **no built-in authentication** — run it on localhost or behind your own auth proxy.

## Quick Start — the canonical 7-step pipeline

```bash
# 0. Verify the server is reachable.
easyds --json status

# 1. Create a project.
easyds --json project new --name my_dataset

# 2. Register an LLM model and activate it (writes both local session and
#    server-side defaultModelConfigId — required for GA / image VQA).
easyds --json model set \
    --provider-id openai \
    --endpoint   https://api.openai.com/v1 \
    --api-key    sk-... \
    --model-id   gpt-4o-mini
easyds --json model use <id-from-step-2>

# 3. Upload a document (.md or .pdf).
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

# 8. (Unique to easyds) Evaluate and auto-repair the final file.
easyds --json datasets eval ./alpaca.json
```

That's the full loop: **status → project → model → upload → chunk → questions → answers → export → evaluate** — reproducible, scriptable, agent-driveable.

## Documentation

- **[`plugins/easyds/skills/easyds/SKILL.md`](plugins/easyds/skills/easyds/SKILL.md)** — slim agent skill index, auto-loaded by Claude Code plugin users and read manually by everyone else
- **[`plugins/easyds/skills/easyds/reference/`](plugins/easyds/skills/easyds/reference/)** — 16 reference docs including the canonical pipeline, custom-prompt rules, operating rules, agent protocol, task settings, PDF/data cleaning, question templates, and the dataset-eval feedback loop
- **[`plugins/easyds/skills/easyds/reference/workflows/`](plugins/easyds/skills/easyds/reference/workflows/)** — 11 scenario recipes (custom-prompt pipeline, sentiment classification, document cleansing, image VQA, multi-turn distillation, GA/MGA pairs, eval & blind test, domain-tree editing, import/clean/optimize, background tasks, quality control)
- **[`docs/SERVER_QUIRKS.md`](docs/SERVER_QUIRKS.md)** — 13 known Easy-Dataset server quirks the CLI already works around
- **Upstream Easy-Dataset documentation**: [https://docs.easy-dataset.com/](https://docs.easy-dataset.com/)

## Community Practice

- **Custom-prompt pipeline against Kimi-K2.5** — end-to-end English Q&A from the ANSYS CFX tutorials, with custom question + evaluation prompts
- **Sentiment classification dataset** — separator chunking + label template + `--fix chunk-join` repair, validated by the `datasets eval` feedback loop
- **Document cleansing retake** — long noisy PDF → batch cleansing → scored Q&A → score-filtered export
- **Image VQA dataset from a directory of slides** — vision-model answer generation

All of the above are encoded as runnable scenario recipes under [`plugins/easyds/skills/easyds/reference/workflows/`](plugins/easyds/skills/easyds/reference/workflows/).

## Contributing

Contributions are very welcome! To contribute to `easy-dataset-cli`:

1. Fork the repository
2. Create a new branch (`git checkout -b feature/amazing-feature`)
3. Set up the dev environment:
   ```bash
   uv sync --extra test
   uv run easyds --version
   uv run pytest                       # → 287 passed
   ```
4. Make your changes and add tests under `tests/`
5. Commit your changes (`git commit -m 'Add some amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request against the `main` branch

Please make sure `pytest` stays green and follow the existing coding style (Click for CLI, thin `requests`-based backend, one `core/` module per Easy-Dataset domain).

## License

This project is licensed under the **AGPL-3.0-or-later** license — see the [LICENSE](LICENSE) file for details. Same license as upstream Easy-Dataset.

## Related Projects

- **[Easy-Dataset](https://github.com/ConardLi/easy-dataset)** — the upstream Next.js + Prisma server `easyds` drives. Required runtime dependency.

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Terry-cyx/easy-dataset-cli&type=Date)](https://www.star-history.com/#Terry-cyx/easy-dataset-cli&Date)

<div align="center">
  <sub>A CLI harness for <a href="https://github.com/ConardLi/easy-dataset">Easy-Dataset</a> — built for humans and agents alike.</sub>
</div>
