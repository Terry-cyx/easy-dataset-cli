---
description: Install the easyds CLI via uv and verify that an Easy-Dataset server is reachable. Safe to re-run (idempotent).
allowed-tools: Bash, Read
---

You are about to set up **easyds** — the CLI harness for [Easy-Dataset](https://github.com/ConardLi/easy-dataset) — for this user. Follow the steps below in order. **Do not skip steps.** Report concisely after each step: what you ran, whether it succeeded, and what's next.

## Step 1 — Check prerequisites

Run these checks in **parallel** (single message, multiple Bash calls):

1. `python --version` — need Python 3.10+. If missing or too old, stop and tell the user to install Python 3.10+ first.
2. `uv --version` — preferred installer. If missing, fall back to `pip --version`. If neither exists, stop and tell the user to install [`uv`](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh` on macOS/Linux, `winget install astral-sh.uv` on Windows).
3. `easyds --version` — if this already prints `1.0.1` or later, skip step 2.
4. `curl -sS -o /dev/null -w "%{http_code}" http://localhost:1717/api/projects` — probes the Easy-Dataset server. `200` means already running, anything else (connection refused, 000, timeout) means it is not up yet.

## Step 2 — Install easyds

**Only if step 1 showed `easyds` is not installed (or older than 1.0.1).** Pick the first available path:

1. Preferred: `uv tool install --upgrade easy-dataset-cli`
2. Fallback: `uv pip install --upgrade easy-dataset-cli`
3. Last resort: `pip install --upgrade easy-dataset-cli`

After installing, run `easyds --version` to confirm the binary is on `PATH`. If it is installed but not on `PATH`, tell the user how to add `~/.local/bin` (Linux/macOS) or `%USERPROFILE%\.local\bin` (Windows) to their shell path and then exit — do not continue.

## Step 3 — Handle the Easy-Dataset server

Easy-Dataset itself is a Next.js + Prisma server that `easyds` forwards to. It is a **hard dependency**. Based on step 1's probe:

- **If the server is already running (HTTP 200):** great, skip to step 4.
- **If the server is NOT running:** explain to the user that they need to start one, and offer the three canonical options. **Do not start it yourself without permission** — starting a long-running server is a side effect the user must approve.

  Options to present:

  1. **Docker one-liner (recommended — fastest):**
     ```bash
     docker run -d --name easy-dataset -p 1717:1717 \
         -v $PWD/local-db:/app/local-db \
         -v $PWD/prisma:/app/prisma \
         ghcr.io/conardli/easy-dataset
     ```
  2. **Desktop client:** [releases page](https://github.com/ConardLi/easy-dataset/releases/latest) — Windows, macOS (Intel + Apple Silicon), and Linux AppImage.
  3. **From source (for developers):**
     ```bash
     git clone https://github.com/ConardLi/easy-dataset
     cd easy-dataset && pnpm install && pnpm dev
     ```

  Ask the user which option they prefer. After they pick and run it, re-probe with `curl -sS -o /dev/null -w "%{http_code}" http://localhost:1717/api/projects` until you see `200`. Wait up to 60 seconds.

## Step 4 — Verify end-to-end

Run `easyds --json status` and confirm the JSON response shows `server: "ok"` (or equivalent). Report the full output.

## Step 5 — Point the user at the skill

Tell the user that the **easyds agent skill** is now installed and Claude Code will automatically pick it up for any dataset-generation request. Recommend they read [`plugins/easyds/skills/easyds/SKILL.md`](../skills/easyds/SKILL.md) or ask Claude: "Use easyds to build a dataset from this document." Also mention the three most-important reference docs:

- `reference/03-canonical-pipeline.md` — the default 7-step recipe
- `reference/06-operating-rules.md` — hard rules learned from production runs (e.g. `always --ga`)
- `reference/11-dataset-eval.md` — the dataset-eval feedback loop, unique to easyds

## Failure handling

- If any step fails, report the **literal error** verbatim, state which step failed, and stop. Do not retry more than once. Do not invent workarounds.
- If the user declines to start the Easy-Dataset server, stop after step 2 and tell them the CLI is installed but no commands will work until a server is reachable.
- Never run `docker run` or `pnpm install` without explicit user approval.
