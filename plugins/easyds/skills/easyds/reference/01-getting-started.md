# 01 — Getting Started

Goal: from zero to a successful `easyds --json status` in under 5 minutes.

## Step 1 — Start the Easy-Dataset server

`easyds` is a thin HTTP client. **A running Easy-Dataset Next.js server is mandatory** — it owns all state, all LLM calls, and the SQLite DB.

```bash
git clone https://github.com/ConardLi/easy-dataset
cd easy-dataset
pnpm install        # first time only
pnpm dev            # serves http://localhost:1717
```

There is **no auth**. Keep it on localhost or behind your own proxy.

## Step 2 — Install `easyds`

`easy-dataset-cli` is **not on PyPI**. Install from source (GitHub or a local clone).

```bash
# Preferred — isolated tool install from GitHub:
uv tool install --upgrade git+https://github.com/Terry-cyx/easy-dataset-cli

# Or, if you already cloned the repo (or use the Claude Code plugin marketplace copy):
uv tool install --upgrade /path/to/easy-dataset-cli
# Windows Git Bash example for the marketplace clone:
# uv tool install --upgrade /c/Users/$USERNAME/.claude/plugins/marketplaces/easy-dataset-cli

# Editable dev install from a local clone:
pip install -e .
```

Installed binary: **`easyds`**. Requires Python 3.10+. Verify with `easyds --version` and confirm the output reports `1.0.1` (or later).

> ⚠️ **Do not run `pip install easyds`.** There is an unrelated PyPI package named `easyds` (a pandas helper, version 0.1.x) that will "install successfully" but ship no `easyds` binary. If you accidentally installed it, uninstall with `uv tool uninstall easyds` (or `pip uninstall easyds`) before installing the real package. Likewise, `pip install easy-dataset-cli` will simply fail because that distribution name is not published on PyPI.

## Step 3 — Verify

```bash
easyds --json status
```

Expected:
```json
{
  "base_url": "http://localhost:1717",
  "server_status": "ok",
  "current_project_id": null,
  ...
}
```

If you get `BackendUnavailable`, the server is not reachable. Read the stderr message — it tells you exactly how to start the server. **Do not retry blindly.**

## Step 4 — Pick a connection target (optional)

| Method | Example |
|---|---|
| CLI flag | `easyds --base-url http://10.0.0.5:1717 status` |
| Env var | `export EDS_BASE_URL=http://10.0.0.5:1717` |
| Default | `http://localhost:1717` |

## Step 5 — Pick an LLM provider

Easy-Dataset is OpenAI-compatible: any provider that speaks the OpenAI Chat
Completions protocol works. The server ships with these built-in IDs (you can
also pass arbitrary IDs — the value is just a label):

| `--provider-id` | Display name | Default API URL |
|---|---|---|
| `openai` | OpenAI | `https://api.openai.com/v1/` |
| `deepseek` | DeepSeek | `https://api.deepseek.com/v1/` |
| `siliconcloud` | 硅基流动 | `https://api.ap.siliconflow.com/v1/` |
| `zhipu` | 智谱 AI | `https://open.bigmodel.cn/api/paas/v4/` |
| `Doubao` | 火山引擎 | `https://ark.cn-beijing.volces.com/api/v3/` |
| `alibailian` | 阿里云百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `openRouter` | OpenRouter | `https://openrouter.ai/api/v1/` |
| `groq` | Groq | `https://api.groq.com/openai` |
| `grok` | Grok | `https://api.x.ai` |
| `302ai` | 302.AI | `https://api.302.ai/v1/` |
| `ollama` | Ollama (local) | `http://127.0.0.1:11434/api` |

Note: free tiers (siliconcloud, openRouter free models, public Ollama) often
rate-limit. Lower the project's `concurrencyLimit` to `1`–`3` first — see
[`06-operating-rules.md` Rule 11](06-operating-rules.md#rule-11--tune-concurrencylimit-to-your-providers-free-tier-rate-limit).

## What's next

- New to the tool? → [`03-canonical-pipeline.md`](03-canonical-pipeline.md) — the default 7-step recipe
- Need to use custom prompts? → [`04-custom-prompts.md`](04-custom-prompts.md) (read **before** writing prompts)
- Want a command list? → [`02-command-reference.md`](02-command-reference.md)
- Want the project-level knobs? → [`08-task-settings.md`](08-task-settings.md)
