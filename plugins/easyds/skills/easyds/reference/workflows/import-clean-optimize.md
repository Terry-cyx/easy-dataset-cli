# Workflow — Import / Clean / Optimize Existing Datasets

Bring in an existing dataset, clean its source chunks, and optimize individual rows.

## When to use this

- You already have an SFT dataset (JSONL/CSV) and want to load it into Easy-Dataset for editing/evaluation
- Some chunks are noisy and need LLM-driven cleaning
- A few specific Q&A rows need rewriting based on natural-language advice

## Recipe

```bash
# 1. Import an existing JSONL dataset with field renaming
easyds datasets import seed.jsonl \
    --mapping instruction=question \
    --mapping output=answer
# .json (array), .jsonl/.ndjson (one object per line), and .csv all work.
# Rows missing question/answer after mapping are filtered client-side.

# 2. Clean a noisy chunk using a project-level prompt
easyds chunks clean CHUNK_ID --prompt-file ./clean.txt
# This first writes the prompt as the project's `dataClean` template
# (the server's clean endpoint reads from there), then triggers the LLM clean.

# 3. Manually edit a chunk in place
easyds chunks edit CHUNK_ID --content-file ./fixed.md

# 4. Batch-prepend or append text to multiple chunks
easyds chunks batch-edit \
    --chunk c1 --chunk c2 --chunk c3 \
    --position start --content "[REVIEWED] "

# 5. Optimize a single dataset row with natural-language advice
easyds datasets optimize DATASET_ID --advice "更简洁,删掉重复段落"

# 6. CRUD individual questions
easyds questions create --chunk CHUNK_ID --label "中级" \
    --question "牛顿第二定律是什么?"
easyds questions edit QID --question "重新表述..."
easyds questions list --status unanswered --all
easyds questions delete QID
```

## Notes

- `datasets import` filters rows missing required fields after mapping — check the result count.
- `chunks clean` rewrites the chunk in place; the original is **not** preserved. Snapshot the project first if you need rollback.
- `datasets optimize` is per-row, synchronous, and uses the same LLM as the rest of the project — no separate model config.
- `--mapping src=dst` is repeatable; unmapped fields are dropped silently.
