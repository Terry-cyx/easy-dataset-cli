# Case 3 — Multi-Turn Distillation (Physics Tutor as Einstein)

**Status:** ✅ live run completed against Easy-Dataset server with real Kimi-K2.5 LLM

Reproduces 案例 3 (物理学多轮对话数据集). Distills questions from a physics
topic tree, then turns each question into a 3-round student↔Einstein dialogue.

## Layout

```
input/
  physics-tree.json    # 5-leaf topic tree (经典力学/牛顿/动量, 光学/折射)
prompt/
  einstein.md          # System prompt: Einstein persona with {{student}} placeholder
output/
  01_distill.json
  02_questions.json    # 4 distilled questions (one per leaf)
  03_generate_multiturn.json
  04_conversations.json   # 2 conversations (3 rounds each = 7 messages incl. system)
  multiturn-sharegpt.json # ★ FINAL — ShareGPT-shaped multi-turn export
  run_meta.txt
```

## Pipeline (live commands)

```bash
# 1. Project + model + bootstrap
PROJ=$(easyds --json project new --name case3-physics-$(date +%s) | jq -r .id)
easyds --json project use $PROJ
easyds --json model set --provider-id openai --provider-name Paratera \
    --endpoint https://llmapi.paratera.com/v1/ --api-key sk-... \
    --model-id Kimi-K2.5 --temperature 0.6
easyds --json model use <new-mc-id>

# 2. Distill questions from the topic tree (1 question per leaf)
easyds --json distill auto \
    --label-tree-file tests_examples/case3-multi-turn/input/physics-tree.json \
    --questions-per-leaf 1 \
    --language zh
# → 4 questions populated under the leaves

# 3. Generate multi-turn dialogues for two questions, 3 rounds each
easyds --json datasets generate \
    --question <q1-id> --question <q2-id> \
    --rounds 3 \
    --role-a 中学生 \
    --role-b 爱因斯坦 \
    --system-prompt-file tests_examples/case3-multi-turn/prompt/einstein.md \
    --scenario "中学物理课提问" \
    --language 中文

# 4. Verify
easyds --json datasets conversations-list

# 5. Export — multi-turn ONLY supports ShareGPT
easyds --json export conversations \
    -o tests_examples/case3-multi-turn/output/multiturn-sharegpt.json \
    --format sharegpt --overwrite
```

## Result

2 conversations, ShareGPT-shaped, **real Einstein-style scientific dialogue**:

- **Conv 1**: Elastic vs inelastic collision mechanics (动量守恒, 完全非弹性碰撞,
  Coefficient of Restitution)
- **Conv 2**: Total internal reflection in optical fibers (临界角, 倏逝波,
  受抑全反射, 量子隧穿类比, 光子晶体光纤色散补偿)

Each conversation has 7 messages: `[system, user, assistant, user, assistant, user, assistant]`.

## CLI capabilities verified

- ✅ `distill auto --label-tree-file` (zero-shot from a JSON tree)
- ✅ `datasets generate --rounds N --role-a --role-b --system-prompt-file --scenario`
- ✅ `datasets conversations-list`
- ✅ `export conversations --format sharegpt`

## Bug found and fixed

The CLI's `export conversations` was sending **POST** to
`/api/projects/{id}/dataset-conversations/export`. The server route is
**GET-only**. Fixed in `easyds/core/export.py` and the matching unit + E2E
test (`test_export_conversations_writes_file`,
`test_full_e2e.py::TestDistillationAndMultiTurnAndImageVQA`).

The fix is committed; the GET path returns the data directly as a ShareGPT
array (`[{messages: [...]}, ...]`), no `--format` parameter needed server-side.

## Run metadata

- **Project:** `Wiu7NjrJPBcF` (`case3-physics-1775637...`)
- **Model:** Kimi-K2.5 via Paratera, temperature=0.6
- **Wall time:** ~3 min for distill + ~5 min for two 3-round dialogues
