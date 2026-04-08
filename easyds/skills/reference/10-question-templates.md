# 10 — Question Templates (Classification & Structured Outputs)

A **question template** is a fixed question that gets applied to many sources
(text chunks or images), with a constrained answer space. It's the right tool
when you want the dataset to be **classification-shaped** rather than
free-form Q&A.

Three answer types:

| `--type` | Answer space | Best for |
|---|---|---|
| `text` | Free-form text | Generic Q&A with a fixed prompt (e.g. "Summarize this in one sentence") |
| `label` | A fixed list of strings | **Classification** (sentiment, topic, intent, severity) |
| `custom_format` (alias `json-schema`) | A JSON Schema | Structured extraction (NER, slot filling, table extraction) |

Two source types:

| `--source-type` | What gets bound to the prompt |
|---|---|
| `text` | The chunk content. Pair with `--separator` chunking to make every chunk = one item to classify. |
| `image` | The image bytes. Used by image VQA workflows. |

---

## Pattern A — Sentiment classification (text + label)

The canonical 案例 2 recipe: split a comment file by separator so every chunk
is one comment, then apply a labeled template to every chunk.

```bash
# 1. Project + model
easyds --json project new --name sentiment-zh
mc=$(easyds --json model set --provider-id openai --endpoint ... \
       --api-key sk-... --model-id gpt-4o-mini | jq -r .id)
easyds --json model use "$mc"

# 2. Upload + custom-separator chunking
easyds --json files upload ./reviews.md
easyds --json chunks split --file reviews.md \
    --separator '---------' --content-file ./reviews.md

# 3. Create the template
tmpl=$(easyds --json questions template create \
    --question "请判断这条评论的情感倾向。" \
    --source-type text \
    --type label \
    --label-set "正面,负面,中性" \
    | jq -r .id)

# 4. Materialize across all chunks
easyds --json questions template apply "$tmpl"

# 5. Generate the answers (constrained to the label set)
easyds --json datasets generate

# 6. Export with chunk content embedded so a trainer can see (text → label)
easyds --json export run -o ./sentiment.json --format alpaca \
    --include-chunk --all --overwrite
```

### Why this works

- `chunks split --separator` ignores the chunk size limits and splits exactly
  on the separator, so each chunk = one self-contained item to classify.
- `template apply` re-binds the template to every chunk that doesn't yet have
  it, making one question per chunk.
- The `label` answer type forces the LLM to pick from `--label-set`. The
  server's prompt construction includes the label list as a hard constraint;
  the model rarely strays.
- `--include-chunk` in the export embeds the chunk content into each record,
  giving you `(text, label)` pairs ready for SFT.

---

## Pattern B — Structured extraction (text + json-schema)

```bash
cat > schemas/contact.json <<'EOF'
{
  "type": "object",
  "properties": {
    "name":  {"type": "string"},
    "email": {"type": "string"},
    "phone": {"type": "string"}
  },
  "required": ["name"]
}
EOF

easyds --json questions template create \
    --question "Extract the contact details from this snippet." \
    --source-type text \
    --type json-schema \
    --schema-file ./schemas/contact.json \
    --auto-generate
```

Server validates the LLM output against the schema; failed records land with
an empty answer field — filter them out at export time with `--score-gte`
after running `datasets evaluate`.

---

## Pattern C — Image classification

See [`workflows/image-vqa.md`](workflows/image-vqa.md) for the full image
recipe. The same `template create --type label` syntax works with
`--source-type image` plus `easyds files import --type image --dir`.

---

## Gotchas

1. **Always create the template AFTER chunking** — the materialization step
   binds to existing sources. If you create it first, you'll need
   `template apply` to re-trigger.
2. **`--label-set` is comma-separated** — escape commas in label names with
   a backslash if you have them.
3. **`--auto-generate` is required for `json-schema`** to actually invoke the
   LLM after creation. Without it the template exists but generates nothing
   until you call `template apply`.
4. **Templates don't go through `questions generate`** — they're materialized
   by `template apply` (or `--auto-generate` at create time). Don't try to
   pass `--ga` or chunk filters; those are for the free-form question pipeline.
