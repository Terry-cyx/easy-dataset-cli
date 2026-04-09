# 07 — Agent Protocol

How an AI agent (or any automation) should drive `easyds` reliably.

## The protocol in 5 lines

1. Always pass `--json` — `easyds --json <subcommand> ...`
2. Check the **exit code** before parsing stdout
3. Errors go to **stderr** as `{"error": "<class>", "message": "..."}`
4. Treat `BackendUnavailable` and `ReadTimeout` as **non-retryable from the same call** — see below
5. Project/model state lives in the **session file**, not in shell env — set once with `project use` / `model use`, then forget

## Exit codes

| Code | Meaning | What to do |
|---|---|---|
| `0` | Success | Parse stdout JSON, proceed |
| `1` | Unexpected error | Read stderr — usually a bug; report to user, do not retry |
| `2` | `BackendUnavailable` (server unreachable) | Read stderr restart instructions; **stop and ask the user to start the server**; do not retry |
| `3` | `BackendError` (server returned non-2xx) | Read stderr — body contains the actual server error. Fix the request and retry |
| `4` | `NoProjectSelected` / `NoModelConfigSelected` | Run `project use <id>` or `model use <id>` (or pass `--project`/`--model-config`) and retry |

## stdout vs stderr

```bash
easyds --json questions list 2> /tmp/err.json > /tmp/out.json
echo "exit=$?"
```

- **stdout** → the parsed result (always valid JSON in `--json` mode, even on success returning `null` or `[]`)
- **stderr** → diagnostic info; in `--json` mode also formatted as JSON: `{"error": "<class>", "message": "<full body>"}`

Never mix them (`2>&1`) when machine-parsing.

## Retry policy

| Error | Retry? | Backoff |
|---|---|---|
| `ReadTimeout` (client gave up) | **No.** Re-list the resource instead — server is still running | n/a |
| `BackendUnavailable` | **No.** Server is down; ask the user | n/a |
| `BackendError 5xx` (server bug) | Maybe once after a few seconds | 5–10s |
| `BackendError 4xx` | **No.** Your request is wrong; fix it | n/a |
| Network connection refused (during a transient blip) | Yes, ≤ 3 times | exponential 1/2/4s |

## The "never restart from scratch" rule

Every layer of the pipeline (chunks, GA pairs, questions, datasets) is persisted in the server's SQLite DB. If a generation step fails halfway through:

1. **Don't delete what already succeeded.**
2. **Re-run the same command** — the server skips already-generated items (e.g. `datasets generate` only processes unanswered questions).
3. **Or target the missing items explicitly** with `--chunk` / `--question`.

## Polling pattern (use this for slow generators)

```bash
prev=-1; stable_since=
while :; do
    sleep 30
    n=$(easyds --json questions list | jq length)
    echo "questions=$n"
    if [ "$n" -eq "$prev" ]; then
        stable_since="${stable_since:-$(date +%s)}"
        elapsed=$(( $(date +%s) - stable_since ))
        [ "$elapsed" -ge 90 ] && break
    else
        prev=$n
        stable_since=
    fi
done
```

Translation: "if count hasn't changed for 90 seconds, assume the server's loop is done."

This is how you wait out a `ReadTimeout` correctly. PowerShell version is in [`workflows/custom-prompt-pipeline.md`](workflows/custom-prompt-pipeline.md).

## Concurrency

`easyds` writes the session file with file locking on POSIX (`fcntl`) and unlocked on Windows. Concurrent CLI invocations against the **same session** are not safe to run in parallel — they may overwrite each other's `current_project_id`. Either:

- Use `EDS_PROJECT_ID` / `EDS_MODEL_CONFIG_ID` env vars per process, or
- Pass `--project ID` explicitly to each invocation

The **server** itself is single-threaded for LLM operations — running two `easyds questions generate` against the same project at the same time will not double your throughput, just compete for the same loop.

## Debugging an unexpected response

```bash
easyds --json <command> 2> err.json > out.json
cat err.json    # has the actual server error if BackendError
cat out.json    # has the parsed body on success
```

The CLI never modifies server error messages — what you see is what the server said. The fix is usually visible in the message itself (missing field, wrong type, missing prerequisite).
