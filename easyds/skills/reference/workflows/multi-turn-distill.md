# Workflow — Multi-Turn Distillation (No Source Documents)

Generate a multi-turn dialogue dataset using **only** an LLM and a topic tree — no source documents. Reproduces spec/03-case-studies §案例 3 (物理学多轮对话).

## When to use this

- You don't have a source document; you have a topic / curriculum / label tree
- You want a role-playing dialogue dataset (tutor/student, doctor/patient, etc.)
- You want each generated question turned into a multi-turn conversation

## Recipe

```bash
# 1. Project + text model (not vision — distillation is text-only)
easyds --json project new --name physics-tutor
mc=$(easyds --json model set \
        --provider-id openai \
        --endpoint https://api.openai.com/v1 \
        --api-key sk-... \
        --model-id gpt-4o-mini \
        --type text \
     | jq -r .id)
easyds --json model use "$mc"

# 2. Write a label tree (JSON or YAML; JSON works without PyYAML installed)
cat > physics-tree.json <<'EOF'
{
  "name": "物理学",
  "children": [
    {"name": "经典力学", "children": [
      {"name": "牛顿定律"},
      {"name": "动量守恒"}
    ]},
    {"name": "相对论", "children": [
      {"name": "狭义相对论"},
      {"name": "广义相对论"}
    ]}
  ]
}
EOF

# 3. Walk the tree and generate questions at every leaf in one shot
easyds --json distill auto \
    --label-tree-file physics-tree.json \
    --questions-per-leaf 5 \
    --language zh

# 4. Save a role-playing system prompt (must keep {{student}} placeholder)
easyds --json prompts set \
    --type answer --key EINSTEIN_PERSONA --language zh-CN \
    --file ./einstein.md \
    --require-var student

# 5. Turn each distilled question into a 4-round dialogue dataset
easyds --json datasets generate \
    --question q-distill-1 --question q-distill-2 \
    --rounds 4 \
    --role-a 学生 --role-b 爱因斯坦 \
    --system-prompt-file ./einstein.md \
    --scenario "中学物理课" \
    --language 中文

# 6. List the multi-turn datasets
easyds --json datasets conversations-list

# 7. Export — multi-turn datasets ONLY support ShareGPT
easyds --json export conversations -o ./physics-multi-turn.json \
    --format sharegpt --overwrite
```

## Shortcut: do steps 3 + 5 in one call

```bash
easyds --json distill auto \
    --label-tree-file physics-tree.json \
    --type multi --rounds 4 \
    --role-a 学生 --role-b 爱因斯坦 \
    --system-prompt-file ./einstein.md
```

## Notes

- Multi-turn datasets **must** be exported via `export conversations` (not `export run`).
- Format is locked to **ShareGPT** — alpaca will be rejected.
- The system prompt file is mandatory for `--rounds N` mode and must contain placeholder(s) referenced by `--require-var`.
- `distill auto` with `--root-topic "Physics"` (instead of `--label-tree-file`) lets the server build the tree itself.
