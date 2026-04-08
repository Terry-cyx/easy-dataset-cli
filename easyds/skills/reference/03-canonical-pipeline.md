# 03 — Canonical Pipeline

The default 7-step recipe. **Use this first.** All scenario workflows in [`workflows/`](workflows/) are variations on this skeleton.

## Mental model

```
.md / .pdf
   ↓ files upload
chunks  (each ≤ chunkSize chars)
   ↓ chunks split
[optional]  GA pairs  (Genre × Audience, ≤ 5 per file)
   ↓ ga generate / set-active
questions  (per chunk × per active GA pair × ≈ 5–10 each)
   ↓ questions generate --ga
datasets  (= question + answer + cot)
   ↓ datasets generate
[optional]  evaluation  (score 0–5 + 评语)
   ↓ datasets evaluate + task wait
output.json[l]  (Alpaca / ShareGPT / multilingual-thinking)
   ↓ export run
```

Every layer is persisted in Prisma+SQLite — you can `list`/`delete`/`re-run` independently. **Never restart from scratch.**

## The 7 commands

```bash
# 0. Verify the server is up.
easyds --json status

# 1. Create a project (becomes the active project automatically).
easyds --json project new --name my_dataset

# 2. Register an LLM model config (becomes the active model automatically;
#    also writes server-side defaultModelConfigId — required for GA / image-VQA).
easyds --json model set \
    --provider-id openai \
    --endpoint https://api.openai.com/v1 \
    --api-key sk-... \
    --model-id gpt-4o-mini
easyds --json model use <id-from-step-2>

# 3. Upload a source document (.md or .pdf).
easyds --json files upload ./spec.md

# 4. Chunk + build domain tree.
easyds --json chunks split --file spec.md

# 5. Generate questions (--ga is REQUIRED — non-GA mode is broken server-side).
easyds --json questions generate --ga --language 中文

# 6. Generate answers + CoT for every unanswered question.
easyds --json datasets generate --language 中文

# 7. Export.
easyds --json export run \
    -o ./alpaca.json \
    --format alpaca \
    --all --overwrite
```

## Time budget warnings

| Step | Speed | What to do |
|---|---|---|
| 0–4 | Seconds | Synchronous, fine to await |
| 5 (`questions generate`) | **Slow** — server iterates chunks serially. Client `ReadTimeout` (default 600s) is **expected**; the server keeps going | Run in background; poll `questions list` until count is stable for ≥ 90s |
| 6 (`datasets generate`) | **Slow** — client fan-out is also serial (~1 record/min) | Same: background + poll `datasets list --all` |
| 7 (`export run`) | Instant | — |

> ⚠️ **Client `ReadTimeout` ≠ task failure.** The server is single-threaded but persistent — it will finish whatever it started, regardless of client disconnect. After a timeout, **always re-list** before assuming anything is broken.

For an end-to-end shell-script template that does the polling correctly, see [`workflows/custom-prompt-pipeline.md`](workflows/custom-prompt-pipeline.md).

## Want more?

| Goal | Recipe |
|---|---|
| Add custom prompts | [`04-custom-prompts.md`](04-custom-prompts.md) + [`workflows/custom-prompt-pipeline.md`](workflows/custom-prompt-pipeline.md) |
| Add quality evaluation | [`workflows/quality-control.md`](workflows/quality-control.md) |
| Image VQA from a directory | [`workflows/image-vqa.md`](workflows/image-vqa.md) |
| Multi-turn dialogue | [`workflows/multi-turn-distill.md`](workflows/multi-turn-distill.md) |
| Pick chunk size / GA count / format | [`05-decision-guide.md`](05-decision-guide.md) |
