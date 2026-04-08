# Workflow — End-to-End with Custom Prompts

The most realistic real-world recipe: take a domain-specific document, apply a custom **question** prompt and a custom **evaluation** prompt, generate a high-quality scored Q&A dataset, and export to Alpaca/ShareGPT.

This is the recipe distilled from the CFX_tutorials production run (`tests_examples/CFX_tutorials/E2E_REAL_RUN.md`).

## When to use this

- You have a single source document (long-form prose **or** case collection)
- You want questions to follow a domain-specific style (engineering, medical, legal, scientific)
- You want quality scoring with a custom rubric
- You're OK waiting hours (the server is single-threaded)

## Inputs you need before starting

| Item | Example |
|---|---|
| Source document | `cases.md` (≤ 200 KB ideal) |
| Question prompt | `prompt_question.md` outputting `["...", "..."]` JSON array |
| Evaluation prompt | `prompt_eval.md` outputting `{"score": 4.5, "evaluation": "..."}` JSON object |
| LLM credentials | endpoint URL + API key + model id |

Read [`../04-custom-prompts.md`](../04-custom-prompts.md) **before** writing the prompt files. The single most common failure mode is the prompt outputting Markdown bullets instead of strict JSON, and the server silently dropping the entire batch.

## The recipe (8 steps)

```bash
# 0) sanity
easyds --json status

# 1) project
easyds --json project new --name "myproj-$(date +%s)" --description "..."

# 2) model — note: model use also writes server-side defaultModelConfigId
mc=$(easyds --json model set \
        --provider-id openai --provider-name MyProvider \
        --endpoint https://api.example.com/v1 \
        --api-key sk-... \
        --model-id my-model --model-name my-model \
        --temperature 0.6 --top-p 0.9 \
     | jq -r .id)
easyds --json model use "$mc"

# 3) custom prompts (validated client-side via --require-var)
easyds --json prompts set \
    --type question --key QUESTION_PROMPT --language zh-CN \
    --file prompt_question.md \
    --require-var text --require-var number --require-var textLength

easyds --json prompts set \
    --type datasetEvaluation --key DATASET_EVALUATION_PROMPT --language zh-CN \
    --file prompt_eval.md \
    --require-var chunkContent --require-var question --require-var answer

# 4) upload + chunk
fid=$(easyds --json files upload ./cases.md | jq -r .fileId)
easyds --json chunks split --file cases.md \
    --strategy text --text-split-min 1500 --text-split-max 3000

# 5) GA pairs — generate 5, then keep only 1 active to save 5× time
easyds --json ga generate --file "$fid" --language zh-CN --overwrite
# deactivate pairs 2..5; loop ids from `easyds --json ga list "$fid"`

# 6) questions (the slow step) — client will time out, server keeps going
easyds --json questions generate --ga --language 中文 || true

# Poll until question count is stable for 90s
prev=-1; stable_since=
while :; do
    sleep 30
    n=$(easyds --json questions list | jq length)
    echo "  questions=$n"
    if [ "$n" -eq "$prev" ]; then
        stable_since="${stable_since:-$(date +%s)}"
        [ $(( $(date +%s) - stable_since )) -ge 90 ] && break
    else
        prev=$n; stable_since=
    fi
done

# 7) datasets (also slow, also client-serial fan-out)
easyds --json datasets generate --language 中文 || true
# Same polling pattern on `datasets list --all --page-size 1000`

# 8) evaluate + export
task_id=$(easyds --json datasets evaluate | jq -r .data.taskId)
easyds --json task wait "$task_id" --timeout 3600

mkdir -p output
easyds --json export run --format alpaca \
    --score-gte 4 --include-cot \
    --all --overwrite \
    --output output/dataset-alpaca.json

easyds --json export run --format sharegpt --include-cot \
    --all --overwrite \
    --output output/dataset-sharegpt.jsonl
```

## Smoke-test before scaling

**Before running step 6 against all chunks**, run it against ONE chunk first to verify the custom prompt's output is being parsed correctly:

```bash
chunk1=$(easyds --json chunks list | jq -r '.[0].id')
easyds --json questions generate --ga --chunk "$chunk1" --language 中文
easyds --json questions list | jq length    # > 0 ⇒ prompt is OK
```

If the count is 0, your prompt is producing something the server's JSON extractor can't parse. Re-read [`../04-custom-prompts.md`](../04-custom-prompts.md) and fix the format before scaling.

## PowerShell version

A complete PowerShell port of this recipe (with proper UTF-8 handling, polling, and error checks) lives at `tests_examples/CFX_tutorials/run_pipeline.ps1` in this repo.

## Time estimate

For a 170 KB document split into ~50 chunks with 1 active GA pair:

| Step | Wall time |
|---|---|
| 0–4 (config + upload + split) | seconds |
| 5 (ga generate) | ~30s |
| 6 (questions, ~10/chunk) | **30–60 min** |
| 7 (datasets, ~1/min) | **2–4 hours** ← biggest bottleneck |
| 8 (evaluate ≈ 20 min) + (export instant) | ~25 min |

Plan for **3–5 hours total** for a small document. Budget half a day if you have 100+ chunks.
