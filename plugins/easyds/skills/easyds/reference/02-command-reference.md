# 02 — Command Reference

Compact catalog of every command group. For "how to combine these into a real task", see [`03-canonical-pipeline.md`](03-canonical-pipeline.md) and the [`workflows/`](workflows/) folder.

## Global flags

| Flag | Purpose |
|---|---|
| `--base-url URL` | Override the Easy-Dataset server URL |
| `--project ID` | Override the active project id |
| `--json` | Machine-readable JSON output. **Always use this in agent code.** |
| `--version`, `-h` / `--help` | Standard |

## Session state

The CLI persists `current_project_id`, `current_project_name`, `current_model_config_id`, `base_url` at `~/.easyds/session.json`.

Resolution order on every command:

```
explicit CLI flag  >  environment variable  >  session file  >  clear error
```

Environment variables: `EDS_BASE_URL`, `EDS_PROJECT_ID`, `EDS_MODEL_CONFIG_ID`.

## Command groups

| Group | Subcommands | Purpose |
|---|---|---|
| `status` | — | Server reachability + active session |
| `project` | `new`, `list`, `info`, `use`, `delete` | Project lifecycle |
| `model` | `set`, `list`, `use` | Per-project LLM config (`--type text\|vision`). **`use` writes both local session and server-side `defaultModelConfigId`** — required for GA / image-VQA |
| `prompts` | `list`, `get`, `set`, `reset` | Project-level prompt overrides. Three-key tuple `(promptType, promptKey, language)`. `set` validates `{{var}}` placeholders — see [`04-custom-prompts.md`](04-custom-prompts.md) |
| `files` | `upload`, `list`, `delete`, `import`, `list-images`, `prune` | Document & image management. `import --type image --dir DIR` zips & POSTs to `/images/zip-import`; `import --type image --from-pdf F` forwards a PDF to `/images/pdf-convert` |
| `chunks` | `split`, `list`, `get`, `edit`, `delete`, `clean`, `batch-edit` | Chunking + LLM-built domain tree. `split --strategy text\|document\|fixed\|code`, `--text-split-min/max`, `--separator --content-file` for custom splits |
| `tags` | `list`, `create`, `rename`, `move`, `delete`, `questions` | Edit the LLM-built domain tree |
| `ga` | `generate`, `list`, `add-manual`, `set-active`, `estimate` | Genre-Audience (MGA) pair management for question diversification (5 pairs/file max) |
| `questions` | `generate`, `list`, `template`, `create`, `edit`, `delete` | Question generation + manual CRUD. **`generate --ga` is required** — non-GA mode is broken on the server |
| `datasets` | `generate`, `list`, `confirm`, `evaluate`, `conversations-list`, `import`, `optimize` | Answer + CoT generation, multi-dim quality scoring, import/optimize |
| `task` | `list`, `get`, `cancel`, `delete`, `wait` | Background task system. `wait TASK_ID` blocks until terminal status |
| `distill` | `auto`, `step tags`, `step questions` | Zero-shot distillation (no source documents) |
| `eval` | `list`, `get`, `create`, `count`, `sample`, `export`, `import`, `delete`, `copy-from-dataset`, `variant` | Evaluation-dataset (benchmark) management |
| `eval-task` | `run`, `list`, `get`, `interrupt`, `delete` | Automated multi-model evaluation |
| `blind` | `run`, `list`, `get`, `question`, `vote`, `auto-vote` | Pairwise blind-test |
| `export` | `run`, `conversations` | Alpaca / ShareGPT / multilingual-thinking export with `--score-gte/--score-lte`, `--include-cot`, `--field-map`, `--split` |
| `repl` | — | Interactive REPL (also the default when no subcommand given) |

## Output formats (`export run --format`)

- `alpaca` — `[{"instruction":..., "input":"", "output":..., "system":""}, ...]`
- `sharegpt` — `[{"conversations":[{"from":"user",...},{"from":"gpt",...}]}, ...]`
- `multilingual-thinking` — Alpaca with explicit Chain-of-Thought field

Multi-turn datasets (`--rounds N`) **must** be exported via `export conversations` and only support ShareGPT.

## Selective generation

`questions generate` and `datasets generate` accept repeated `--chunk` / `--question` flags to target specific items. With no targets, both fan out across **all** chunks / **all unanswered** questions.

```bash
easyds questions generate --chunk c1 --chunk c2 --ga
easyds datasets generate --question q-5
```

## Per-command help

Always available with `-h` or `--help`:

```bash
easyds chunks split -h
easyds questions generate --help
```

## Inline help for the 7 most-used commands

Saved here so an agent doesn't need to shell out to read the most common signatures.

### `easyds project new`

```
--name TEXT         [required]
--description TEXT
```

### `easyds model set`

```
--provider-id TEXT    Provider id, e.g. 'openai'.  [required]
--provider-name TEXT  Display name (defaults to provider-id).
--endpoint TEXT       [required]
--api-key TEXT        [required]
--model-id TEXT       Model identifier sent to the provider.  [required]
--model-name TEXT
--type [text|vision]  Model type. 'vision' is used by image VQA workflows.  [default: text]
--temperature FLOAT
--max-tokens INTEGER
--top-p FLOAT         [default: 0.9]   ← server schema requires topP
```

### `easyds files upload FILE_PATH`

Single positional argument. Only `.md` and `.pdf` are accepted.

### `easyds chunks split`

```
--file TEXT                                          [required]
--strategy [document|fixed|text|code]                [default: document]
--text-split-min INTEGER                             override project task config
--text-split-max INTEGER                             override project task config
--separator TEXT                                     custom separator → routes through /custom-split
--content-file FILE                                  required when --separator is set
--language TEXT
```

### `easyds questions generate`

```
--chunk TEXT                  Chunk id (repeatable). Empty = all chunks.
--image TEXT                  Image id (repeatable, --source image only).
--source [chunk|image]        [default: chunk]
--ga                          Enable Genre/Audience expansion. ★ ALWAYS pass this — see Rule 3
--language TEXT
```

### `easyds datasets generate`

```
--question TEXT                Question id (repeatable). Empty = all unanswered.
--language TEXT
--rounds INTEGER               Multi-turn dialogue mode (routes to /dataset-conversations)
--role-a TEXT                  [default: 用户]
--role-b TEXT                  [default: 助手]
--system-prompt-file FILE      Required for --rounds N
--system-prompt TEXT           Inline alternative
--scenario TEXT
```

### `easyds export run`

```
-o, --output PATH                                    [required]
--format [alpaca|sharegpt|multilingual-thinking]
--all / --confirmed-only
--overwrite
--score-gte FLOAT                                    Filter by score (0-5)
--score-lte FLOAT
--file-type [json|jsonl|csv]                         [default: json]
--field-map src=dst                                  Rename columns. Repeatable.
--include-chunk                                      Embed source chunk content
--include-image-path                                 Surface imagePath top-level
--include-cot                                        Embed <think>{cot}</think> before answer
--system-prompt TEXT                                 Alpaca `system` / ShareGPT system message
--reasoning-language TEXT                            multilingual-thinking only  [default: English]
--split TEXT                                         '0.7,0.15,0.15' → -train/-valid/-test files
```

For everything else, run `easyds <group> <subcommand> -h`.
