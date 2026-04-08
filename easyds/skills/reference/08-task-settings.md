# 08 — Project Task Settings

Every project carries a `task-config.json` file with the **default knobs** that
chunking, question generation, evaluation, and the multi-turn pipeline use.
Knobs you don't set per-command (like `easyds chunks split --text-split-min N`)
fall back to these.

Read with `easyds project settings show`. Write with `easyds project settings set`.

## The knobs

Defaults are taken from the upstream Easy-Dataset codebase
(`lib/db/projects.js` + per-service constants).

| Field | Default | What it controls |
|---|---|---|
| `textSplitMinLength` | `1500` | Minimum characters per chunk in `chunks split` |
| `textSplitMaxLength` | `2000` | Maximum characters per chunk in `chunks split` |
| `questionGenerationLength` | `240` | **Question density** — `questions generate` produces `floor(chunk_chars / 240)` questions per chunk. Lower = more questions per chunk. |
| `removeQuestionMarkProbability` | `60` | Percent (0–100) of generated questions whose trailing `？` is stripped. The docs explain: real users don't always end questions with marks, so a fraction are stripped to make SFT closer to user input. |
| `concurrencyLimit` | `5` | Max parallel LLM calls per batch task. **Lower this** if your provider rate-limits (free SiliconFlow / OpenRouter typically need `1`–`3`). |
| `multiTurnRounds` | `3` | Default turn count for `datasets generate --rounds N` and `multi-turn-generation` distillation tasks. |
| `multiTurnRoleA` / `multiTurnRoleB` | `用户` / `助手` | Default role names for multi-turn dialogue datasets |
| `multiTurnSystemPrompt` | (empty) | Default system prompt for multi-turn datasets — overridden per command |
| `multiTurnScenario` | (empty) | Default scenario string |
| `evalQuestionTypeRatios` | `{true_false:1, single_choice:1, multiple_choice:1, short_answer:1, open_ended:1}` | Per-type weights for `eval generate`. Set to 0 to skip a type entirely. |
| `minerUToken` | (empty) | MinerU API token for PDF parsing. **Expires every 14 days** — rotate proactively. |
| `visionConcurrencyLimit` | `2` | Max parallel pages a custom vision model parses simultaneously |
| `huggingFaceToken` | (empty) | Reserved for future HF upload — currently unused |

## Reading the current values

```bash
easyds --json project settings show
# → {"textSplitMinLength": 1500, "textSplitMaxLength": 2000, ...}
```

## Updating one field

```bash
easyds --json project settings set --key concurrencyLimit --value 2
easyds --json project settings set --key questionGenerationLength --value 300
easyds --json project settings set --key textSplitMinLength --value 2500
```

Values are auto-cast: `2` → int, `0.5` → float, `true`/`false` → bool, valid
JSON → object/array, otherwise string.

## Bulk update via JSON

```bash
easyds --json project settings set --json \
  '{"textSplitMinLength":2500, "textSplitMaxLength":4000, "concurrencyLimit":3}'
```

Server merges with the existing file — fields you don't mention are preserved.

## Setting eval question-type ratios

There's a dedicated convenience command for the 5-key eval ratio dict:

```bash
easyds --json project settings set-eval-ratios \
    --true-false 1 --single 1 --multi 1 --short 2 --open 1
```

Then trigger generation:

```bash
easyds --json eval generate --wait
```

## Why this matters

- The docs constantly refer to "在任务设置里调整 X" — that **only** translates to
  `project settings set` from the CLI. There's no per-command flag for most of
  these knobs.
- Forgetting to lower `concurrencyLimit` is the #1 cause of "tasks suddenly
  start failing in the middle" on free-tier providers (see `06-operating-rules.md`
  Rule 11).
- The MinerU token rotation is a real production gotcha — set a calendar
  reminder.

## Recipes

```bash
# A. Production-grade Chinese long-form (long chunks, fewer questions per chunk)
easyds --json project settings set --json '{
  "textSplitMinLength": 2500,
  "textSplitMaxLength": 4000,
  "questionGenerationLength": 400,
  "concurrencyLimit": 3
}'

# B. Free-tier-safe (siliconflow / openrouter free models)
easyds --json project settings set --key concurrencyLimit --value 1

# C. Eval-heavy (more open-ended questions for subjective evaluation)
easyds --json project settings set-eval-ratios \
    --true-false 0 --single 1 --multi 1 --short 1 --open 3
```
