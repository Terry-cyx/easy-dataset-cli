---
description: Install the easyds CLI from the marketplace-cloned source and verify that an Easy-Dataset server is reachable. Safe to re-run (idempotent).
allowed-tools: Bash, Read
---

You are about to set up **easyds** — the CLI harness for [Easy-Dataset](https://github.com/ConardLi/easy-dataset) — for this user. Follow the steps below **in order, sequentially**. Report concisely after each step: what you ran, whether it succeeded, and what's next.

## ⚠️ Important rules

- **DO NOT** `pip install easyds` — there is an unrelated PyPI package called `easyds` (a pandas helper by a different author) that will "install successfully" but provide no `easyds` binary and has no connection to this project. This is a namesquat trap.
- **DO NOT** `pip install easy-dataset-cli` — `easy-dataset-cli` is **not published on PyPI**. Any `pip`/`uv pip` command targeting that name will fail with "not found in the package registry".
- The canonical source is the **marketplace-cloned copy** that Claude Code already downloaded to disk when the user ran `/plugin marketplace add Terry-cyx/easy-dataset-cli`. Install from that local path. If it is missing for some reason, fall back to the GitHub URL.
- **Run the probes in Step 1 sequentially, not in parallel.** Harness parallel blocks abort sibling commands when one of them fails with a non-zero exit code, which makes failure diagnosis harder (e.g. Windows boxes often lack `python` but do have `py` or `python3`, and a failing `python --version` would otherwise cancel the other checks).

## Step 1 — Check prerequisites

Run these probes **one at a time** (separate Bash calls, NOT a single parallel block). For each probe, tolerate failure — you are gathering facts, not gating. A non-zero exit on any one probe is fine; just record the result.

1. Python interpreter — try `python --version`, then `python3 --version`, then `py --version` (Windows launcher). As long as **one** of them prints `Python 3.10` or newer, mark Python as available and remember which command name works. If all three fail, stop and tell the user to install Python 3.10+ first.
2. `uv --version` — preferred installer. If missing, fall back to `pip --version`. If neither exists, stop and ask the user to install [`uv`](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh` on macOS/Linux, `winget install astral-sh.uv` on Windows).
3. `easyds --version` — if this prints `easyds, version 1.0.1` (or a later `1.x`), you can skip Step 2 entirely. **Any other version, "command not found", or a version like `0.1.1` means Step 2 must run.** (A `0.1.1` in particular means the user has the namesquat `easyds` package installed — you will need to uninstall it in Step 2 before reinstalling the real thing.)
4. `curl -sS -o /dev/null -w "%{http_code}" http://localhost:1717/api/projects` — probes the Easy-Dataset server. `200` means already running, anything else (connection refused, `000`, timeout) means it is not up yet.

## Step 2 — Install easyds from the marketplace-cloned source

**Only if step 1 showed `easyds` is not installed (or not at version 1.0.1+).**

### 2a. Detect the marketplace clone

When the user ran `/plugin marketplace add Terry-cyx/easy-dataset-cli`, Claude Code cloned the full repository to:

- **macOS / Linux**: `~/.claude/plugins/marketplaces/easy-dataset-cli`
- **Windows (Git Bash / WSL path)**: `/c/Users/$USERNAME/.claude/plugins/marketplaces/easy-dataset-cli`
- **Windows (native)**: `%USERPROFILE%\.claude\plugins\marketplaces\easy-dataset-cli`

Verify it exists and contains `pyproject.toml`:

```bash
ls ~/.claude/plugins/marketplaces/easy-dataset-cli/pyproject.toml 2>/dev/null \
    || ls /c/Users/$USERNAME/.claude/plugins/marketplaces/easy-dataset-cli/pyproject.toml 2>/dev/null \
    || echo "NOT_FOUND"
```

Remember the path that worked.

### 2b. If the user has the namesquat `easyds==0.1.x` installed, remove it first

```bash
uv tool uninstall easyds 2>/dev/null || true
pip uninstall -y easyds 2>/dev/null || true
```

Both are best-effort and safe to ignore failures.

### 2c. Install from the marketplace clone

Use the path you confirmed in 2a:

```bash
# Preferred (uv tool install — isolated, adds easyds to PATH automatically):
uv tool install --upgrade "$MARKETPLACE_PATH"

# Fallback 1 — if uv is not available, plain pip editable install:
pip install --upgrade "$MARKETPLACE_PATH"

# Fallback 2 — if the marketplace clone is missing, install directly from GitHub:
uv tool install --upgrade git+https://github.com/Terry-cyx/easy-dataset-cli
```

**Do not** try `uv tool install easy-dataset-cli` or `pip install easy-dataset-cli` — those names are not on PyPI and will fail.

### 2d. Verify — this check is mandatory

```bash
easyds --version
```

The output **must** contain `1.0.1` (or a newer `1.x`). If it prints `0.1.1`, the namesquat package is still installed — re-run 2b and 2c. If the binary is not on `PATH`, tell the user how to add `~/.local/bin` (Linux/macOS) or `%USERPROFILE%\.local\bin` (Windows) to their shell path, then stop — do not continue until `easyds --version` reports a 1.x version.

## Step 3 — Handle the Easy-Dataset server

Easy-Dataset itself is a Next.js + Prisma server that `easyds` forwards to. It is a **hard dependency**. Based on step 1's probe:

- **If the server is already running (HTTP 200):** great, skip to step 4.
- **If the server is NOT running:** explain to the user that they need to start one, and offer the three canonical options. **Do not start it yourself without permission** — starting a long-running server is a side effect the user must approve.

  Options to present:

  1. **Docker one-liner (recommended — fastest):**
     ```bash
     docker run -d --name easy-dataset -p 1717:1717 \
         -v "$PWD/local-db:/app/local-db" \
         -v "$PWD/prisma:/app/prisma" \
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

Run `easyds --json status` and confirm the JSON response shows `server_status: "ok"` (or equivalent). Report the full output.

## Step 5 — Point the user at the skill

Tell the user that the **easyds agent skill** is now installed and Claude Code will automatically pick it up for any dataset-generation request. Recommend they ask Claude: "Use easyds to build a dataset from this document." Also mention the three most-important reference docs:

- `reference/03-canonical-pipeline.md` — the default 7-step recipe
- `reference/06-operating-rules.md` — hard rules learned from production runs (e.g. `always --ga`)
- `reference/11-dataset-eval.md` — the dataset-eval feedback loop, unique to easyds

## Failure handling

- If any step fails, report the **literal error** verbatim, state which step failed, and stop. Do not retry more than once. Do not invent workarounds.
- If the user declines to start the Easy-Dataset server, stop after step 2 and tell them the CLI is installed but no commands will work until a server is reachable.
- Never run `docker run` or `pnpm install` without explicit user approval.
- Never fall back to `pip install easyds` or `pip install easy-dataset-cli` — both are wrong (namesquat and non-existent, respectively).
