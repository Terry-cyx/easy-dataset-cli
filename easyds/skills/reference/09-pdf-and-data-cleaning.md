# 09 — PDF Parsing & Background Data Cleaning

Two production-only features that **don't run during `files upload`** and need
explicit follow-up commands.

---

## A. PDF parsing strategies

`easyds files upload doc.pdf` only stores the file. The actual PDF → Markdown
conversion happens in a separate background task. By default it uses the
**basic** parser (`pdf-parse`), which is fast but loses tables, formulas, and
images.

For complex PDFs, re-process with one of four strategies via:

```bash
easyds --json files process --file doc.pdf --strategy mineru --wait
```

### Strategies

| `--strategy` | Backend | When | Cost |
|---|---|---|---|
| `default` | `pdf-parse` (built-in) | Plain prose, simple reports | Free, fast |
| `mineru` | [MinerU API](https://mineru.net/apiManage/token) (cloud) | Academic papers, technical docs with formulas/tables/images | Free w/ token (14-day rotation) — set via `project settings set --key minerUToken --value sk-...` |
| `mineru-local` | [MinerU self-hosted](https://opendatalab.github.io/MinerU/) at `mineru-api --host 0.0.0.0 --port 8000` | Same accuracy as `mineru`, no quota, no token rotation | Free, needs GPU/server |
| `vision` | Custom vision LLM (page-by-page OCR) | Image-heavy PDFs, scanned docs, slides | LLM token cost; needs `easyds model set --type vision` first |

### Recipes

```bash
# A1. Basic re-process (default parser, no extra config)
easyds --json files process --file complicated.pdf --wait

# A2. MinerU API (recommended for academic papers)
easyds --json project settings set --key minerUToken --value <YOUR_TOKEN>
easyds --json files process --file paper.pdf --strategy mineru --wait

# A3. MinerU self-hosted
# (assumes you've started `mineru-api --port 8000` and pointed
#  the project's task-config minerULocalUrl at http://localhost:8000)
easyds --json files process --file paper.pdf --strategy mineru-local --wait

# A4. Custom vision model (best for image-heavy slides)
easyds --json model set --type vision \
    --provider-id openai --endpoint https://api.openai.com/v1 \
    --api-key sk-... --model-id gpt-4o
easyds --json model use <vision-model-id>
easyds --json files process --file slides.pdf --strategy vision --wait
```

### Domain-tree action when re-processing

`files process` accepts `--domain-tree-action`:

| Value | Behavior |
|---|---|
| `rebuild` (default) | Wipe and regenerate the domain tree from all current files. Use for the first run or after major file changes. |
| `modify` | Diff-update the tree based on what was added/removed. Cheaper. |
| `keep` | Don't touch the existing tree. Use when you've manually curated it (`tags` group) and don't want to lose your edits. |

---

## B. Batch data cleaning (`chunks clean-task`)

The default chunking pipeline produces noisy chunks: dangling `[1]` citation
markers, broken `![](images/foo.png)` references, mid-sentence breaks where the
PDF column wrapped, etc. The single-chunk `easyds chunks clean <ID>` command
fixes one at a time. For a project with 50+ chunks, use the **batch task**:

```bash
# Clean every chunk in the project, with a custom prompt
easyds --json chunks clean-task \
    --prompt-file ./prompts/clean.md \
    --wait --timeout 1800

# Clean only specific chunks (e.g. the worst offenders)
easyds --json chunks clean-task --chunk c1 --chunk c2 --chunk c3 --wait
```

### What the prompt must produce

The cleaning prompt is invoked once per chunk with `{{text}}` and
`{{textLength}}` placeholders bound. **The output must be the cleaned text
verbatim** — no JSON wrapper, no Markdown bullets, no preamble. The server
replaces the chunk's `content` with whatever the LLM returns.

A good cleaning prompt:

1. Lists the **specific noise patterns** you want removed (cite by example):
   ```
   - Strip [N] reference markers
   - Strip ![](images/...) image embeds and their captions
   - Re-join paragraphs where a sentence was cut by a column break
   - Convert tables to ordered lists
   ```
2. Asks for a **chapter-level summary** prepended to the cleaned content (case 4
   used this — it gives downstream questions a global hook).
3. **Preserves the `{{text}}` and `{{textLength}}` placeholders** — if you
   delete them the prompt will be silently ignored.

### When to clean before vs. after questions

**Clean BEFORE generating questions** if your source is from a sloppy PDF
extraction. Bad chunks → bad questions → bad answers → throw it all away.

**Don't clean** if your source is already a hand-curated Markdown — you'll
spend tokens for no gain.

### Time estimate

| Chunks | Concurrency | Wall time |
|---|---|---|
| 20 | 5 | ~3 min |
| 50 | 5 | ~8 min |
| 200 | 5 | ~30 min |
| 200 | 1 (free-tier) | **~2.5 hrs** |

If you've lowered `concurrencyLimit` (Rule 11), budget proportionally.
