# 04 — Custom Prompts (MUST-READ before writing one)

Easy-Dataset's server parses LLM output with a strict JSON extractor. **If your custom prompt does not produce the exact format the server expects, the entire batch is silently dropped.** This is the #1 source of mysterious "0 questions generated" failures.

## How to set a custom prompt

```bash
easyds --json prompts set \
    --type question \
    --key QUESTION_PROMPT \
    --language zh-CN \
    --file ./my_prompt.md \
    --require-var text --require-var number --require-var textLength
```

Three-key tuple: `(promptType, promptKey, language)`. The CLI rejects the prompt if any `--require-var` placeholder is missing — use this to fail fast.

## The two prompt types you'll use most

### Type `question` — `QUESTION_PROMPT`

**Required placeholders** (use `--require-var` for all of them):

| Placeholder | What it gets replaced with |
|---|---|
| `{{text}}` | The chunk content |
| `{{number}}` | Target number of questions |
| `{{textLength}}` | Character count of the chunk |
| `{{gaPromptNote}}` | Inline GA hint (empty when GA disabled) |
| `{{gaPromptCheck}}` | GA quality-check hint (empty when GA disabled) |
| `{{gaPrompt}}` | Full GA pair description (empty when GA disabled) |

**Required output format** — a JSON array of strings, **nothing else**:

```json
["问题1", "问题2", "问题3"]
```

❌ Markdown bullet lists, numbered lists, prose with embedded JSON, YAML — all rejected.
✅ JSON array of plain strings, ASCII or unicode, double-quoted.

### Type `datasetEvaluation` — `DATASET_EVALUATION_PROMPT`

**Required placeholders**:

| Placeholder | What it gets replaced with |
|---|---|
| `{{chunkContent}}` | The source chunk text |
| `{{question}}` | The generated question |
| `{{answer}}` | The generated answer |

**Required output format** — a JSON object:

```json
{"score": 4.5, "evaluation": "评语 ≤ 150 字"}
```

`score` must be a float in `[0, 5]` (0.5 step is conventional). Anything else gets stored as 0 and the dataset becomes unfilterable.

## Recommended workflow

1. **Write the prompt** with explicit "Output Format: JSON array of strings, nothing else" instructions in the system role. Give a 1-shot example of the *exact* JSON shape.
2. **Set with `--require-var`** for every required placeholder so the CLI catches missing variables before the server does.
3. **Smoke-test on 1 chunk first**:
   ```bash
   easyds --json questions generate --chunk <one-chunk-id> --ga
   easyds --json questions list   # count should grow
   ```
4. **Inspect one generated question** for sanity. If `questions list` count is 0, your prompt's output is being rejected — re-read it and re-test.
5. **Only then** scale to all chunks.

## Other prompt types

| `promptType` | `promptKey` | When to override |
|---|---|---|
| `answer` | `ANSWER_PROMPT` | Custom answer style (e.g. role-playing tutor) |
| `dataClean` | `DATA_CLEAN_PROMPT` | Used by `chunks clean` to scrub noisy chunks |
| `domainTree` | `DOMAIN_TREE_PROMPT` | Custom hierarchical tagging |
| `globalPrompt` | — | Inject project-wide context into every LLM call |
| `gaGeneration` | — | Control GA pair generation style |

All take the same `--type / --key / --language / --file / --require-var` pattern.

## Restoring defaults

```bash
easyds --json prompts reset --type question --key QUESTION_PROMPT --language zh-CN
```
