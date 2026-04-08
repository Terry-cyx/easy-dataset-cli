# Case 4 — Noisy AI Safety Doc → Cleansed → Eval-Ready

**Status:** ✅ live run completed against Easy-Dataset server with real Kimi-K2.5 LLM

Reproduces the new-command demos for 案例 4 (AI 智能体安全数据集), exercising
the **five new CLI commands** added in this refine pass:

1. `project settings show` / `set` / `set-eval-ratios`
2. `chunks clean-task --wait`
3. `eval generate --wait`
4. `datasets edit --score --tag --note`
5. (also exercises `datasets confirm` against the corrected route)

## Layout

```
input/
  noisy-safety.md      # 2-chapter excerpt with [1] cite markers, broken images, and a table
prompt/
  clean.md             # Custom dataClean prompt (strips refs/images, joins lines, prepends summary)
output/
  01_settings_before.json   # ★ project settings show (default 1500/2000/5)
  02_settings_set.json      # ★ project settings set --json
  03_settings_after.json    # ★ project settings show (now 300/1500/2)
  04_upload.json
  05_split.json
  06_chunks_before_clean.json   # 3 chunks: 25, 431, 276 chars (raw)
  07_clean_task.json            # ★ chunks clean-task --wait result
  08_chunks_after_clean.json    # 3 chunks: 2576, 491, 321 chars (cleaned + summary prepended)
  09_eval_ratios.json           # ★ project settings set-eval-ratios
  10_eval_generate.json         # ★ eval generate --wait result
  11_eval_list.json             # 13 eval rows distributed across 4 question types
  12_questions_gen.json         # questions generate --ga
  13_questions.json             # 18 SFT questions
  14_datasets_gen.json
  15_datasets.json              # 3 SFT datasets (one per first 3 questions)
  16_dataset_edit.json          # ★ datasets edit --score --tag --tag --note
  17_datasets_after_edit.json   # confirms persistence
  safety-alpaca.json            # ★ FINAL Alpaca export with --include-cot
  run_meta.txt
```

## Pipeline (live commands)

```bash
# 1. Project + model + bootstrap (Paratera Kimi-K2.5)
PROJ=$(easyds --json project new --name case4-cleansing-$(date +%s) | jq -r .id)
easyds --json project use $PROJ
easyds --json model set --provider-id openai ... | jq -r .id
easyds --json model use <new-mc-id>

# 2. ★ project settings — read defaults, then tighten
easyds --json project settings show
easyds --json project settings set --json '{
  "textSplitMinLength":300,
  "textSplitMaxLength":1500,
  "concurrencyLimit":2
}'

# 3. Upload + chapter-aware split
easyds --json files upload tests_examples/case4-cleansing/input/noisy-safety.md
easyds --json chunks split --file noisy-safety.md \
    --separator "## 第" \
    --content-file tests_examples/case4-cleansing/input/noisy-safety.md
# → 3 chunks

# 4. ★ Batch cleansing — kicks off a server-side data-cleaning task and waits
easyds --json chunks clean-task \
    --chunk q-gFwYmS --chunk nqi8CsKZ \
    --prompt-file tests_examples/case4-cleansing/prompt/clean.md \
    --wait --timeout 600
# → status: completed, all 3 chunks cleaned (server cleaned the third small one
#   too because it satisfied the default chunk filter)

# 5. ★ Set eval question-type ratios
easyds --json project settings set-eval-ratios \
    --true-false 2 --single 1 --multi 1 --short 1 --open 0
# → evalQuestionTypeRatios = {tf:2, single:1, multi:1, short:1, open:0}

# 6. ★ Generate eval dataset (reads ratios from task-config)
easyds --json eval generate --wait --timeout 600
easyds --json eval list
# → 13 eval rows: 7 true_false, 2 single_choice, 2 multiple_choice, 2 short_answer

# 7. SFT pipeline (questions + answers — small batch)
easyds --json questions generate --ga --language 中文
easyds --json datasets generate --question <q1> --question <q2> --question <q3> --language 中文

# 8. ★ Manual review (split across two server endpoints automatically)
easyds --json datasets edit <did> --score 4.5 \
    --tag needs-review --tag low-quality \
    --note "测试人工审核字段"
# → score=4.5, tags=["needs-review","low-quality"], note=测试人工审核字段
#   (verified persisted via datasets list)

# 9. Final export
easyds --json export run -o tests_examples/case4-cleansing/output/safety-alpaca.json \
    --format alpaca --include-cot --all --overwrite
```

## Result

### Cleansing effect (verbatim diff)

| Chunk | Before | After |
|---|---|---|
| `noisy-safety-part-1` (heading) | 25 chars | 2576 chars (LLM expanded with summary) |
| `noisy-safety-part-2` (chapter 1 body) | 431 chars w/ `[1][2][3]` markers + image link + Markdown table | 491 chars: clean prose + bullet list + 80-char chapter summary prepended |
| `noisy-safety-part-3` (chapter 2 body) | 276 chars w/ `[5][6]` markers + image link | 321 chars: cleaned + summary |

The cleaning prompt's instructions were honored verbatim: bracket-number citations stripped, `![](images/...)` removed, table converted to bullet list, summary prepended.

### Eval ratio honored

Asked for `tf:2, single:1, multi:1, short:1, open:0` and got exactly that proportion (true_false dominates, no open_ended questions).

### Manual review persists

`datasets edit --score 4.5 --tag needs-review --tag low-quality --note ...` correctly:
- splits into TWO server PATCH calls (content endpoint vs metadata endpoint)
- sends `tags` as a JSON array (server requirement, was previously `customTag` string)
- persists across re-list

## Bugs found and fixed

1. **`datasets confirm`** was sending `PUT` to `/datasets/{id}` — server only has `PATCH` on that route, and `confirmed` is on the *parent* route `/datasets?id={id}`. Both routes are PATCH. CLI was 100% silent failure on real servers.
2. **`datasets edit --tag`** was sending `customTag: "..."` — server expects `tags: ["..."]` (array). Field was silently dropped.
3. The fixes split `core/datasets.update()` into typed `update_metadata()` and `update_content()` helpers, with a back-compat `update()` shim that dispatches automatically.

Both fixes have unit tests (`test_update_routes_to_two_endpoints`, `test_update_metadata_review_only`, `test_update_content_uses_query_id`). Stub server PATCH handler also extended.

## CLI capabilities verified

- ✅ `project settings show` (live, returns full task-config.json)
- ✅ `project settings set --json` (live, merge-update)
- ✅ `project settings set-eval-ratios` (live, 5 ratio fields written)
- ✅ `chunks clean-task --prompt-file --wait` (live, all 3 chunks cleaned, summary prepended)
- ✅ `eval generate --wait` (live, ratio honored)
- ✅ `datasets edit --score --tag --tag --note` (live, persisted across re-list)
- ✅ `datasets confirm` (now uses correct route)

## Run metadata

- **Project:** `GTf8sev4sCpa` (`case4-cleansing-1775637...`)
- **Model:** Kimi-K2.5 via Paratera, temperature=0.4
- **Wall time:** ~12 min total (1 min config, 2 min cleanse, 1 min eval-gen, 8 min Q&A)
