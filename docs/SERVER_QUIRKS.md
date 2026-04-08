# Easy-Dataset Server Quirks

A catalog of server-side bugs and protocol oddities discovered during real end-to-end production runs against the upstream [Easy-Dataset](https://github.com/ConardLi/easy-dataset) Next.js server. `easyds` works around all of them transparently — this document exists for **maintainers** who need to understand *why* the CLI does the things it does.

> **For end users**: read [`easyds/skills/reference/06-operating-rules.md`](../easyds/skills/reference/06-operating-rules.md) instead. That doc translates these quirks into actionable rules without the historical detail.

| # | Symptom | Root cause | CLI workaround |
|---|---|---|---|
| 1 | `400: topP required` | Server validates `topP` as required even though docs call it optional. | `core/model.set_config` always sends `topP=0.9` by default. |
| 2 | File upload silently truncates | Server route reads `request.arrayBuffer()` (not multipart). The form-field name is irrelevant; the server reads the filename from the `x-file-name` HTTP header. | `core/files.upload` uses `post_bytes` with the `x-file-name` header instead of `multipart/form-data`. |
| 3 | `UnicodeEncodeError` on Windows | GBK console default. | All `--json` output forces `ensure_ascii=True`. |
| 4 | `task-config.json` writes succeed but values vanish | The `/api/projects/{id}/config` PATCH endpoint silently no-ops; the real route is `PUT /api/projects/{id}/tasks` and it REPLACES the entire JSON file. | `core/project.set_task_config` does GET-merge-PUT to `/tasks`. |
| 5 | `/split` rejects `fileNames` as plain string array | Server expects `[{fileName, fileId}]` objects. | `core/chunks.split` accepts `files: list[dict]` and serializes accordingly. |
| 6 | LLM endpoints reject `model: "<id>"` | All LLM-driven endpoints (`/split`, `/generate-questions`, `/datasets`, `/dataset-conversations`) expect the **full model config dict**, not the id. The frontend reads it from `localStorage.selectedModelInfo` before each fetch. | `core/model.get_config_object()` looks up the full dict; every caller passes the dict instead of the id. |
| 7 | `json.dumps(..., ensure_ascii=False)` crashes on Windows GBK | Same root cause as #3. | Force `ensure_ascii=True`. |
| 7.5 | Large file upload `ReadTimeout` after 60s | `requests` default. | `EasyDatasetBackend` default `timeout=600.0`. |
| 8 | `questions generate` (without `--ga`) → `primaryGaPair is not defined` | The non-GA branch in the question-generation service has a `ReferenceError` — the prompt template helper references a GA-mode variable unconditionally. | **No fix possible from the CLI side.** Must always pass `--ga`; documented as Rule 3 in `06-operating-rules.md`. To get "no GA" behavior, generate 5 GA pairs and `set-active --inactive` 4 of them. |
| 9 | "Failed to parse questions" with custom prompts | Server's `extractJsonFromLLMOutput()` is strict — only accepts the exact JSON shape (`["...", "..."]` for question, `{"score":..,"evaluation":..}` for eval). Markdown bullets, numbered lists, prose-with-embedded-JSON all fail. | CLI does **not** rescue malformed output; user must write the prompt correctly. Documented in `04-custom-prompts.md` with `--require-var` placeholder validation. |
| 10 | Server-side export emits raw dataset rows, not Alpaca/ShareGPT shape | Server's `/datasets/export` returns the Prisma row dump. | `core/export.format_records()` does the format conversion client-side. |
| 11 | `ga generate` → `No active model available for GA generation` | Server's `getActiveModel(projectId)` reads `Projects.defaultModelConfigId` from the DB, but the original `model use` only wrote the local session file. PUT `/api/projects/{id}` with `defaultModelConfigId` is the only way to set it server-side, and the project route only defines GET/PUT/DELETE (no PATCH). | New `core/project.set_default_model()`; `model use` now writes both local session and server-side default. `core/project.update()` switched from PATCH to PUT. Regression: `TestProject::test_set_default_model`, `test_set_default_model_clear`, `test_update_uses_put`. |
| 12 | `questions list` (no params) → `prisma.questions.findMany() Argument 'take' is missing` | Server route uses `findMany({skip, take})` but treats both as required even when no pagination params are provided. | `core/questions.list_questions()` auto-injects `all=true` when neither pagination nor selectedAll is set. Regression: `TestQuestions::test_list_defaults_to_all`, `test_list_with_page_does_not_inject_all`. |
| 13 | `core/project.update()` always 405s | Same root cause as #11 — server route lacks PATCH method. | Fixed in same commit as #11. |

## Architectural notes

- **The server is single-threaded for LLM operations.** Both `/generate-questions` and `/datasets` (single-question) iterate serially with no `Promise.all` concurrency. Multiplying GA pairs multiplies wall time linearly.
- **Client `ReadTimeout` does not interrupt server work.** The server's `for` loop has no cancellation hook — when the client gives up, the loop runs to completion regardless. Re-listing resources is the only way to know if the server is done.
- **Background tasks live in a separate `Task` table** (`status: 0=processing, 1=completed, 2=failed, 3=interrupted`). The `task wait` command client-side polls this table. The server uses in-process `setImmediate` for task execution and has no streaming endpoint, so polling is unavoidable.
- **State is per-project.** Almost every API call requires a `projectId`. The CLI persists the active project id in `~/.easyds/session.json`; resolution order is `--project flag` > `EDS_PROJECT_ID` env > session file > clear error.
- **Model config is per-project, not global.** Easy-Dataset has no global default model. The CLI's `model set` registers a config in the project's `ModelConfig` table; `model use` activates it locally **and** writes the server-side `defaultModelConfigId`.

## Why a thin client and not a reimplementation?

Easy-Dataset's value lives in its prompt library, its domain-tree builder, its multi-provider LLM adapter, and its evaluation rubrics. Reimplementing those in Python would mean shipping a fork that drifts from upstream. `easyds` keeps the surface area minimal: CLI ↔ HTTP ↔ server. If upstream fixes a bug, `easyds` benefits for free; if upstream changes a route, only the affected `core/*.py` module needs an update.
