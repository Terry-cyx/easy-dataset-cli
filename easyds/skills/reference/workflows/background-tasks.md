# Workflow — Background Task System

Long-running operations create rows in the server's `Task` table. Inspect, cancel, and wait on them via the `task` group.

## When to use this

- You triggered an async operation (e.g. `datasets evaluate`) that returned a `taskId`
- You want to inspect what's currently processing
- You need to interrupt a stuck task
- You want to block until a specific task is done

## Recipe

```bash
# 1. List currently-processing tasks
#    status: 0=processing, 1=completed, 2=failed, 3=interrupted
easyds task list --status 0

# 2. Filter by task type
easyds task list --task-type generate-questions
easyds task list --task-type generate-datasets
easyds task list --task-type dataset-evaluation

# 3. Inspect a specific task
easyds --json task get TASK_ID

# 4. Cancel a stuck task
easyds task cancel TASK_ID

# 5. Block until terminal status (client-side polling — Easy-Dataset has no
#    streaming endpoint; the server runs tasks via in-process setImmediate)
easyds task wait TASK_ID --poll-interval 2.0 --timeout 600

# 6. Delete a task row from the table (does NOT delete its data)
easyds task delete TASK_ID
```

## Async APIs that use the task system

| Command | Returns | How to wait |
|---|---|---|
| `datasets evaluate` (no `--dataset`) | `{"data": {"taskId": "..."}}` | `task wait <id>` |
| `eval-task run` | `{"taskId": "..."}` | `task wait <id>` |
| `blind run` | `{"taskId": "..."}` | `task wait <id>` |

Synchronous APIs (`questions generate`, `datasets generate`) do **not** create Task rows — they block the HTTP request. See [Rule 5](../06-operating-rules.md) for handling those.

## Idiom

```bash
task_id=$(easyds --json datasets evaluate | jq -r .data.taskId)
echo "Evaluation task: $task_id"
easyds --json task wait "$task_id" --timeout 3600 --poll-interval 5
echo "Done."
```

`--timeout 0` means wait forever. Pick a reasonable upper bound based on dataset size — 1 evaluation ≈ 10–30s, so 1 hour handles ~120–360 records.

## Notes

- `task wait` is **client-side polling** — the server has no streaming endpoint. Pick a sensible `--poll-interval` (default 2s) to balance latency vs server load.
- Status codes: `0=processing, 1=completed, 2=failed, 3=interrupted`. Treat `0` as "still in flight"; everything else is terminal.
- `task cancel` PATCHes the row to status=3 (`interrupted`); the in-process server loop checks this flag between iterations and stops on the next check.
