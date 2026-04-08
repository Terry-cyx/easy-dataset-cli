# Workflow — Quality Control (Custom Separator + Clean + Eval)

Custom-separator chunking + custom cleaning prompt + multi-dim evaluation + score-filtered export. Reproduces spec/03-case-studies §案例 4 (AI 安全数据集).

## When to use this

- Your source has clean structural delimiters (`## 第N章`, `---`, `===`)
- You need to scrub noise from chunks before generating questions
- You want a quality score on every Q&A pair and only export the high-quality ones

## Recipe

```bash
# 0. Project + model + custom prompts (see workflows/custom-prompt-pipeline.md
#    for the model boilerplate)
easyds --json project new --name ai-safety
# ... model set + model use ...

# 1. Custom-separator chunking
#    --separator "## 第" splits at chapter boundaries
#    --content-file points at the LOCAL copy so the CLI can compute split positions
easyds --json chunks split \
    --file ai-safety.md \
    --separator '## 第' \
    --content-file ./local/ai-safety.md

# 2. Override the data-cleaning prompt (must keep {{text}} {{textLength}})
easyds --json prompts set \
    --type dataClean --key DATA_CLEAN_PROMPT --language zh-CN \
    --file ./prompts/clean.md \
    --require-var text --require-var textLength

# 3. Override the dataset evaluation prompt
easyds --json prompts set \
    --type datasetEvaluation --key DATASET_EVALUATION_PROMPT --language zh-CN \
    --file ./prompts/eval.md \
    --require-var chunkContent --require-var question --require-var answer

# 4. (Optional) Clean specific noisy chunks before generating questions
easyds --json chunks clean CHUNK_ID --prompt-file ./prompts/clean.md
# `chunks clean` first writes the prompt as the project's dataClean template
# (the server's clean endpoint reads from there), then triggers the LLM clean.

# 5. Generate questions + answers as usual
easyds --json questions generate --ga --language 中文
easyds --json datasets generate --language 中文

# 6. Run batch quality evaluation (server scores 0-5 across 4 dimensions)
task_id=$(easyds --json datasets evaluate | jq -r .data.taskId)
easyds --json task wait "$task_id" --timeout 3600

# 7. Export ONLY the high-quality records
easyds --json export run \
    -o ./high-quality.json \
    --format alpaca \
    --score-gte 4 \
    --all --overwrite
```

## Notes

- `chunks clean` is per-chunk and synchronous — slow for many chunks; consider only cleaning the worst offenders.
- The 4-dimension scoring rubric is embedded in the `datasetEvaluation` prompt — customize it to your domain.
- `--score-gte 4` is the conventional cutoff for SFT-quality data.
- Steps 5–6 are LLM-bound and can be slow for large documents. They block until the server returns. See [Rule 6](../06-operating-rules.md) for backgrounding.
