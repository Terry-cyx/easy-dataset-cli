# Workflow — Long PDF → Cleansed → Scored Q&A

The most demanding case study (案例 4 AI 智能体安全): a long, noisy PDF
whitepaper, with chapter-aware chunking, **batch LLM cleansing** of every
chunk, custom evaluation rubric, and a final score-filtered export.

## When to use this

- Source is a long PDF (whitepaper, report, textbook) with structural noise
- You want to spend tokens cleaning chunks **before** generating questions
- You want a quality score on every Q&A pair so you can drop bad ones

## Inputs

| Item | Example |
|---|---|
| Source PDF | `whitepaper.pdf` (~50 pages) |
| Chapter separator | `## 第` (chapter heading prefix) |
| Cleansing prompt | `prompts/clean.md` — strips citations, broken images, re-joins paragraphs |
| Evaluation rubric | `prompts/eval.md` — emits `{"score": 4.5, "evaluation": "..."}` |

## Recipe

```bash
# 0. Sanity + project + model (see workflows/custom-prompt-pipeline.md for the model boilerplate)
easyds --json status
easyds --json project new --name ai-safety
mc=$(easyds --json model set --provider-id openai --endpoint ... --api-key sk-... --model-id deepseek-v3 | jq -r .id)
easyds --json model use "$mc"

# 1. Tighten task config FIRST (keeps the rest of the recipe consistent)
easyds --json project settings set --json '{
  "textSplitMinLength": 2500,
  "textSplitMaxLength": 4500,
  "concurrencyLimit": 3
}'

# 2. Upload the PDF; the default basic parser is fine for most reports
easyds --json files upload ./whitepaper.pdf

# 2a. (Optional) If the basic parse loses tables/formulas, re-process with MinerU
#     easyds --json project settings set --key minerUToken --value <token>
#     easyds --json files process --file whitepaper.pdf --strategy mineru --wait

# 3. Custom-separator chunking on chapter headings — preserves structure
easyds --json chunks split \
    --file whitepaper.pdf \
    --separator '## 第' \
    --content-file ./local/whitepaper.md     # local copy MinerU/default produced

# 4. Save BOTH custom prompts before any task runs
easyds --json prompts set \
    --type dataClean --key DATA_CLEAN_PROMPT --language zh-CN \
    --file ./prompts/clean.md \
    --require-var text --require-var textLength

easyds --json prompts set \
    --type datasetEvaluation --key DATASET_EVALUATION_PROMPT --language zh-CN \
    --file ./prompts/eval.md \
    --require-var chunkContent --require-var question --require-var answer

# 5. ★ NEW — kick off batch cleansing across ALL chunks in the background
clean_task=$(easyds --json chunks clean-task --wait --timeout 1800 \
    | jq -r .data.taskId)
echo "cleansing task: $clean_task"
# (or: --no-wait, then `easyds --json task wait $clean_task`)

# 6. Smoke-check that cleansing actually changed something
easyds --json chunks list | jq '.[0]' | head -20
# Expect cleaner content (no [1] [2] markers, no broken image links)

# 7. Generate questions with GA expansion (Rule 3)
easyds --json questions generate --ga --language 中文 || true
# Poll on `questions list | jq length` until stable — see Rule 5/6

# 8. Generate answers
easyds --json datasets generate --language 中文 || true
# Same polling pattern on `datasets list --all --page-size 1000`

# 9. Run quality evaluation against the custom rubric
eval_task=$(easyds --json datasets evaluate | jq -r .data.taskId)
easyds --json task wait "$eval_task" --timeout 3600

# 10. Manual review on the borderline rows (optional)
easyds --json datasets list --score-lte 3 --page-size 100 | jq -r '.[].id' \
| while read did; do
    # Tag low-quality rows for follow-up
    easyds --json datasets edit "$did" --tag "needs-review" --note "auto-flagged"
  done

# 11. Score-filtered export — only the high-quality 4+ records
easyds --json export run \
    -o ./output/whitepaper-alpaca.json \
    --format alpaca \
    --score-gte 4 \
    --include-cot \
    --all --overwrite
```

## What can go wrong (and how to tell)

| Symptom | Likely cause | Fix |
|---|---|---|
| Step 5 task succeeds but chunks look unchanged | Cleansing prompt's variables (`{{text}}`, `{{textLength}}`) were edited | Re-`prompts set` and re-run |
| Step 9 task succeeds but all scores are 0 | Eval prompt produced text instead of JSON | Re-read [`../04-custom-prompts.md`](../04-custom-prompts.md), smoke-test on 1 dataset |
| `--score-gte 4` exports nothing | Eval task hasn't been run, OR all rows scored < 4 | Run `eval-task list`; if needed, soften the rubric |
| Half the chunks come out empty after step 5 | LLM JSON-wrapped its output instead of returning bare cleaned text | Cleansing prompt is wrong; the output must be the cleaned text **verbatim**, no wrapper |

## Reference assets

The case-study `prompts/clean.md` opens with these noise rules and **prepends a
chapter summary**. Mirror this shape:

```markdown
你是一个数据清洗助手。请处理以下文本块：

要求：
- 删除 [1] [24] 等引用标识
- 删除 ![](images/...) 图片引用及其说明
- 合并被列断行的句子
- 表格转列表
- 在内容前面加一段 100 字左右的章节摘要

需清洗文本（{{textLength}} 字）：
{{text}}

直接输出"摘要 + 清洗后的内容"，不要任何其他说明、代码块或 JSON 包装。
```

## Time estimate (50-page PDF, ~40 chunks, concurrencyLimit=3)

| Step | Wall time |
|---|---|
| 1–4 (config + upload + chunk + prompts) | ~1 min |
| 5 (batch cleansing) | **~15 min** ★ new |
| 7 (questions, 1 GA pair active) | ~45 min |
| 8 (datasets) | **~2 hrs** ← bottleneck |
| 9 (evaluate) | ~25 min |
| 11 (export) | seconds |

Plan **3–4 hours total**. Halve it by skipping cleansing if your source is
already clean Markdown.
