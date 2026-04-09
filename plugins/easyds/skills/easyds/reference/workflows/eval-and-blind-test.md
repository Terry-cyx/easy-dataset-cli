# Workflow — Benchmark Evaluation + Pairwise Blind-Test

Build a benchmark, run two models against it with a judge, then pit them in a pairwise blind-test driven entirely from the CLI.

## When to use this

- You need to benchmark candidate models on a curated test set
- You want pairwise model comparison ("which is better, A or B?") without a GUI
- You want a CI-friendly automated voting rule, not human voting

## Recipe

```bash
# 1. Create benchmark rows (the CLI JSON-encodes options/correctAnswer)
easyds eval create \
    --question "Which planet is closest to the sun?" \
    --type single_choice \
    --option Mercury --option Venus --option Earth --option Mars \
    --correct '[0]' \
    --tag astronomy

easyds eval create \
    --question "Pick the rocky planets." \
    --type multiple_choice \
    --option Mercury --option Jupiter --option Earth --option Saturn \
    --correct '[0,2]'

easyds eval create \
    --question "What is entropy?" --type short_answer \
    --correct "A measure of disorder in a system."

# Or seed the benchmark from existing SFT datasets
easyds eval copy-from-dataset d-12
easyds eval variant --dataset d-12 --type single_choice --count 3

easyds eval count                          # type breakdown
easyds eval export -o ./bench.jsonl --format jsonl

# 2. Run an evaluation task (judge model scores subjective answers)
easyds eval-task run \
    --model gpt-4o-mini:openai \
    --model claude-haiku:anthropic \
    --judge-model gpt-4o:openai \
    --sample-limit 50 \
    --language en

easyds eval-task list
easyds eval-task get task-1 --type single_choice --correct
easyds eval-task interrupt task-1          # if it's running too long

# 3. Pairwise blind-test: model A vs model B
easyds blind run \
    --model-a gpt-4o:openai \
    --model-b claude-opus:anthropic \
    --sample-limit 30

# 4a. Manual voting (one question at a time)
easyds blind question bt-1                 # returns leftAnswer/rightAnswer/isSwapped
easyds blind vote bt-1 \
    --vote left \
    --question-id q-3 \
    --is-swapped \
    --left-answer "..." --right-answer "..."

# 4b. OR auto-vote with a deterministic rule (great for CI smoke tests)
easyds blind auto-vote bt-1 --judge-rule longer

# 5. Final scores
easyds blind get bt-1                      # → scores: {modelA, modelB, tie}
```

## Notes

- The `--model M:provider` syntax: `model-id:provider-id` (e.g. `gpt-4o:openai`).
- The blind-test vote endpoint is plain HTTP — **no GUI required** despite what the upstream docs imply.
- `eval` benchmarks are independent from SFT `datasets` — they live in their own table. Use `copy-from-dataset` to promote SFT rows into benchmark rows.
- `--judge-rule longer` is one of several built-in deterministic rules; useful as a CI baseline so you can detect regressions without LLM noise.
