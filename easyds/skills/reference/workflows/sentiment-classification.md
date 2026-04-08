# Workflow — Sentiment / Text Classification Dataset

Build a labeled classification dataset from a flat file of short texts.
Reproduces 案例 2 (评论情感分类). Same pattern works for: topic classification,
intent detection, severity tagging, anything where the answer is one of N
fixed labels.

## When to use this

- You have many short, independent text items in one file
- You want every item classified into one of a fixed label set
- You want `(text, label)` pairs ready for SFT, not free-form Q&A

## Inputs

| Item | Example |
|---|---|
| Source file | `reviews.md` — comments separated by `---------` (or any unique sep) |
| Label set | `正面,负面,中性` |
| LLM | gpt-4o-mini, deepseek-v3, qwen-2.5-72b-instruct |

## Recipe

```bash
# 0. Sanity
easyds --json status

# 1. Project + model
easyds --json project new --name reviews-zh --description "Chinese review sentiment"
mc=$(easyds --json model set \
        --provider-id openai \
        --endpoint https://api.openai.com/v1 \
        --api-key sk-... \
        --model-id gpt-4o-mini \
     | jq -r .id)
easyds --json model use "$mc"

# 2. Upload + custom-separator chunking (1 chunk per comment)
easyds --json files upload ./reviews.md
easyds --json chunks split \
    --file reviews.md \
    --separator '---------' \
    --content-file ./reviews.md
# Verify: should see N chunks, one per comment
easyds --json chunks list | jq length

# 3. Create the labeled question template
tmpl=$(easyds --json questions template create \
    --question "请判断这条评论的情感倾向。" \
    --source-type text \
    --type label \
    --label-set "正面,负面,中性" \
    | jq -r .id)
echo "template: $tmpl"

# 4. Materialize across all chunks
easyds --json questions template apply "$tmpl"
easyds --json questions list | jq length    # should equal chunk count

# 5. Generate the labels
easyds --json datasets generate

# 6. Smoke-check the answer space
easyds --json datasets list --page-size 1000 \
    | jq -r '.[].answer' | sort | uniq -c
# Expected: only "正面", "负面", "中性" lines

# 7. Export — embed the comment text so the trainer sees (text → label)
easyds --json export run \
    -o ./output/sentiment-alpaca.json \
    --format alpaca \
    --include-chunk \
    --all --overwrite
```

## Smoke-test before scaling

If `step 6` shows answers outside the label set (e.g. `"正面，但有保留"`), the
LLM is freelancing. Two fixes:

1. Switch to a stronger model (Rule 12).
2. Lower `temperature`:
   ```bash
   easyds --json model set ... --temperature 0.0
   easyds --json model use <new-mc-id>
   ```

For mass label drift, also raise the prompt's strictness via custom prompts —
see [`../10-question-templates.md`](../10-question-templates.md) for the JSON
schema variant.

## Variations

| Goal | Change |
|---|---|
| English reviews | `--language English`; rewrite the question text in English |
| Topic classification | `--label-set "tech,sports,finance,entertainment,other"` |
| Severity tagging for support tickets | `--label-set "P0,P1,P2,P3,not-a-bug"` |
| Multi-label | Use `--type json-schema` with `{"type":"array","items":{"enum":[...]}}` instead of `--type label` |

## Time estimate

| Items | Wall time (concurrencyLimit=5) |
|---|---|
| 30 | ~30 sec |
| 200 | ~3 min |
| 1000 | ~15 min |
| 1000 (free-tier, concurrencyLimit=1) | ~75 min |

Classification is much faster than free-form Q&A because each call returns a
single token from a fixed vocabulary — no sentence generation.
