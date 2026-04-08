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
