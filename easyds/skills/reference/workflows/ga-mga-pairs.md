# Workflow — GA / MGA Pair Diversification

Genre-Audience pairs cross-multiply your question generation, producing more diverse training data from the same source files.

## When to use this

- You want question style diversification (different genres × different audiences)
- You're OK with a 2–5× cost multiplier on `questions generate`
- You're past smoke-testing and ready to scale

## Recipe

```bash
# 1. Estimate the cost BEFORE running (client-side; no server call)
easyds ga estimate --files 4 --questions 100
# → max 20 pairs total, ~500 questions, ~3.9× token inflation

# 2. Generate 5 GA pairs per file (overwrite mode is the default)
easyds --json ga generate --file file-1 --file file-2 --language 中文

# Or append to existing pairs instead of replacing:
easyds --json ga generate --file file-3 --append

# 3. List, toggle, and add manual pairs
easyds --json ga list file-1
easyds --json ga set-active --file file-1 --id ga-file-1-3 --inactive

easyds --json ga add-manual \
    --file file-1 \
    --genre-title "Tutorial" \
    --audience-title "Beginner" \
    --genre-desc "step-by-step style" \
    --audience-desc "no prior background"

# 4. Run the standard pipeline. Active GA pairs automatically diversify questions.
easyds --json chunks split --file spec.md
easyds --json questions generate --ga --language 中文
easyds --json datasets generate --language 中文

# 5. Export with renamed fields and metadata
easyds --json export run -o ./train.jsonl --format alpaca \
    --file-type jsonl \
    --field-map instruction=prompt --field-map output=response \
    --include-chunk \
    --include-image-path \
    --all --overwrite

# 6. Train/valid/test split (deterministic by SHA1 of record id)
easyds --json export run -o ./dataset.json --format alpaca \
    --split 0.7,0.15,0.15 \
    --all --overwrite
# → writes dataset-train.json, dataset-valid.json, dataset-test.json
```

## Cost trade-off

| Active GA pairs | Question generation time | Diversity |
|---|---|---|
| 0 (don't run `ga generate`) | 1× | Low |
| 1 | 1× | Medium |
| 5 (the official default) | **5×** | Maximum |

**Always generate 5, then `set-active --inactive` what you don't need.** Faster than re-running generation.

## Notes

- Server enforces a max of 5 pairs per file via DB unique constraint.
- `ga generate` requires server-side `defaultModelConfigId` to be set — see [Rule 2](../06-operating-rules.md). Always do `model use` after `model set`.
- `--split` writes 3 files next to your `--output`; the original file is also written, so set `--overwrite`.
