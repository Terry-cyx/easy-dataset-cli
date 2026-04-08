# 06 — Operating Rules

Hard rules learned from real production runs. **Follow these and most "weird" failures disappear.** If you violate one, the failure mode is usually a 500 error or silent data loss, not a friendly warning.

---

## Rule 1 — Always pass `--json` in agent code

```bash
easyds --json status        # ✅ parseable
easyds status               # ❌ pretty banner with box-drawing chars
```

Why: the human-mode output uses Unicode box characters that crash on Windows GBK consoles, and is impossible to parse reliably. `--json` mode also forces `ensure_ascii=True` so multilingual content survives.

---

## Rule 2 — `model use <id>` after every `model set`

```bash
mc=$(easyds --json model set ... | jq -r .id)
easyds --json model use "$mc"
```

Why: `model set` creates the config but does **not** activate it. `model use` writes both:
- the local session file (so subsequent commands know which model to use)
- the **server-side** `defaultModelConfigId` on the project (required for GA generation, image-VQA, and any endpoint that calls `getActiveModel(projectId)`)

Skipping `model use` will make `ga generate` fail with `No active model available for GA generation`.

---

## Rule 3 — `questions generate` always with `--ga`

```bash
easyds --json questions generate --ga --language 中文     # ✅
easyds --json questions generate --language 中文          # ❌ server crash
```

Why: the non-GA code path on the server has a `primaryGaPair is not defined` ReferenceError. `--ga` is the only mode that works.

If you don't want diversification, just generate 5 GA pairs and `set-active --inactive` 4 of them — you get 1 nominal pair and the same effect as "no GA".

---

## Rule 4 — Custom prompts must output the exact JSON shape the server expects

See [`04-custom-prompts.md`](04-custom-prompts.md) for the full table.

- `question` prompt → `["...", "..."]` (JSON array of strings)
- `datasetEvaluation` prompt → `{"score": 4.5, "evaluation": "..."}` (JSON object)

Wrong format = silent batch loss. The server's JSON extractor is strict; Markdown bullets and prose-with-embedded-JSON both fail.

**Smoke-test on 1 chunk** (`--chunk <id>`) before scaling.

---

## Rule 5 — Client `ReadTimeout` ≠ task failure

```
{"error": "ReadTimeout", "message": "...read timeout=600.0..."}
```

This means **the client gave up waiting**, not that the server stopped working. The Easy-Dataset server is single-threaded but persistent — it will keep iterating through your chunks/questions even after the client disconnects.

**After a timeout, always re-list** the relevant resource:

```bash
# Was it questions generation?
easyds --json questions list

# Was it datasets generation?
easyds --json datasets list --all --page-size 1000
```

Numbers will continue to grow until the server's loop finishes. **Do NOT re-issue the original generate command** — that just spawns a second server-side loop competing with the first.

For a polling pattern that handles this correctly, see [`workflows/custom-prompt-pipeline.md`](workflows/custom-prompt-pipeline.md).

---

## Rule 6 — Long batch operations belong in the background

Both `questions generate` and `datasets generate` are fan-out loops with no concurrency. For non-trivial documents:

```bash
nohup easyds --json questions generate --ga > /tmp/q.log 2>&1 &
# Then poll until stable:
while sleep 30; do
    n=$(easyds --json questions list | jq length)
    echo "questions: $n"
done
```

A document with 50 chunks × 5 GA pairs × ~10 questions/chunk ≈ 2500 questions can take **hours**. Plan accordingly.

---

## Rule 7 — Only upload `.md` and `.pdf`

Other formats (DOCX, TXT, EPUB) are accepted by the server but routinely silently truncated or mis-parsed. The CLI rejects non-`.md`/`.pdf` uploads at the client side.

If you have a DOCX/EPUB, **convert to Markdown first** (e.g. with `pandoc`), inspect the result, then upload.

---

## Rule 8 — Async batches use the `task wait` pattern

```bash
task_id=$(easyds --json datasets evaluate | jq -r .data.taskId)
easyds --json task wait "$task_id" --timeout 3600
```

`datasets evaluate` (and a few other long-running ops) return a `taskId` instead of blocking. **Always pair them with `task wait`** — otherwise you have no way to know when the job is done.

The poll interval is client-side; the server has no streaming endpoint.

---

## Rule 9 — Don't trust local state when in doubt

The session file (`~/.easyds/session.json`) caches project/model ids. If you're seeing "wrong project" or "wrong model" symptoms, **always verify against the server**:

```bash
easyds --json status                    # what the CLI thinks
easyds --json project info              # what the server has
easyds --json model list                # all configs on the server
```

The server is the source of truth. If they disagree, fix the local session by re-running `project use <id>` / `model use <id>`.

---

## Rule 10 — Read error messages literally

`BackendError` messages contain the **raw server response** — Prisma errors, LLM provider errors, validation errors, all unmodified. The CLI does **not** sanitize or simplify them, by design.

```
"BackendError: POST /api/projects/.../split -> 400: {'error': 'fileNames must be array of {fileName, fileId}'}"
```

The fix is in the message itself. Read it, change the request, retry. Don't guess.

---

## Rule 11 — Tune `concurrencyLimit` to your provider's free-tier rate limit

```bash
# Symptom: "tasks suddenly start failing in the middle"
easyds --json project settings set --key concurrencyLimit --value 1
```

Why: the default is **5** parallel LLM calls. Free-tier providers (SiliconFlow,
OpenRouter free models, public Ollama proxies) commonly rate-limit at 1–3 RPS.
A 50-question batch will run fine for the first ~10 then start spitting 429s.
The CLI surfaces those as task-row errors that look mysterious.

Set this **per project** in `task-config.json` via `project settings set`. There
is no per-command flag — see [`08-task-settings.md`](08-task-settings.md).

---

## Rule 12 — Use a model that can produce stable JSON

Many endpoints (`questions generate`, `datasets evaluate`, custom prompts) rely
on the LLM emitting parseable JSON. Small / older / heavily-quantized local
models routinely fail this:

- They prepend `Sure, here is the JSON:`
- They wrap the array in Markdown code fences
- They add trailing commas
- They forget to close the brackets

**Don't waste time debugging the prompt** if you're seeing intermittent batch
loss with a small model. Switch to a competent model (gpt-4o-mini, deepseek-v3,
qwen-2.5-32b-instruct or larger). Re-test on **one chunk** first.

The server's JSON extractor is strict; there is no "best-effort repair" path.

---

## Rule 13 — `language` is per-call AND project-wide. Set both.

```bash
# Per-call (used by question gen / dataset gen / eval gen)
easyds questions generate --ga --language 中文
easyds questions generate --ga --language English

# Project-wide default for tasks that don't expose --language
easyds project settings set --key language --value 中文
```

The official docs note that the GUI's "current user language" decides what
language **all newly generated content** is in. From the CLI: pass `--language`
on every generation command, **and** set the project-wide default for the
batch/distillation tasks that read from task-config.

Mixed-language datasets (some Chinese, some English) almost always trace back
to forgetting one of these.

---

## Rule 14 — Choose a domain-tree action when adding/removing files

When `chunks split` runs, it triggers domain-tree (re)building. After your
**first** file the tree is built fresh; after the second file you have a
choice:

| `--domain-tree-action` | Behavior | When |
|---|---|---|
| `rebuild` (default) | Discard the existing tree and re-derive from all current files | Most cases — tree stays consistent |
| `modify` | Diff-update for added/removed files only | Cheap, but the tree drifts over time |
| `keep` | Don't touch the tree | **Use after you've manually curated it** with the `tags` group |

The single most painful pattern: spend 30 minutes editing the tree by hand,
upload one more file, lose all your edits. Set `--domain-tree-action keep` on
incremental uploads after curation.
