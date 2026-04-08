"""End-to-end tests for the `easyds` CLI.

Strategy:
- An in-process Python ``http.server`` impersonates Easy-Dataset's API.
- Subprocess tests invoke the installed CLI via ``_resolve_cli``. They are the same tests an AI agent would run.
- A separate ``TestLiveBackend`` class is gated on ``EDS_LIVE_TESTS=1`` and
  hits a real server (skipped, not faked, when not set — see TEST.md §Part 1).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest


# ── _resolve_cli helper for subprocess tests ──────────────────────────


def _resolve_cli(name: str) -> list[str]:
    """Resolve installed CLI command; falls back to ``python -m`` for dev.

    Set env ``EASYDS_FORCE_INSTALLED=1`` to require the installed command.
    """
    import shutil

    force = os.environ.get("EASYDS_FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(name)
    if path:
        print(f"[_resolve_cli] Using installed command: {path}")
        return [path]
    if force:
        raise RuntimeError(
            f"{name} not found in PATH. Install with: pip install -e ."
        )
    module = "easyds.cli"
    print(f"[_resolve_cli] Falling back to: {sys.executable} -m {module}")
    return [sys.executable, "-m", module]


CLI_BASE = _resolve_cli("easyds")


# ── Stub HTTP server ──────────────────────────────────────────────────


def _walk_tags(tree):
    """Yield every tag node in a nested tag tree."""
    for node in tree:
        if not isinstance(node, dict):
            continue
        yield node
        for child in node.get("children", []) or []:
            yield from _walk_tags([child])


def _find_tag_node(tree, tag_id):
    """DFS lookup for a tag node by id."""
    for node in _walk_tags(tree):
        if node.get("id") == tag_id:
            return node
    return None


def _detach_tag(tree, tag_id):
    """Remove a tag node from its parent's children list (or root)."""
    # Try root level first.
    for i, node in enumerate(tree):
        if node.get("id") == tag_id:
            tree.pop(i)
            return True
    # Walk children.
    for node in _walk_tags(tree):
        children = node.get("children") or []
        for i, child in enumerate(children):
            if child.get("id") == tag_id:
                children.pop(i)
                return True
    return False


class _StubState:
    """Shared state recorded by the stub HTTP server."""

    def __init__(self):
        self.requests: list[dict] = []
        self.projects: dict[str, dict] = {}
        self.next_project = 0
        self.files: list[dict] = []
        self.chunks: list[dict] = []
        self.questions: list[dict] = []
        self.datasets: list[dict] = []
        self.model_configs: list[dict] = []
        # Refine round 1: prompts + custom-split + evaluation + score filtering
        self.custom_prompts: list[dict] = []
        self.task_config: dict = {
            "textSplitMinLength": 1500,
            "textSplitMaxLength": 2000,
            "questionGenerationLength": 240,
            "concurrencyLimit": 5,
        }
        self.evaluation_calls: list[dict] = []
        # Refine round 2: templates, images, multi-turn, distill
        self.templates: list[dict] = []
        self.images: list[dict] = []
        self.conversations: list[dict] = []
        self.distill_tag_calls: list[dict] = []
        self.distill_question_calls: list[dict] = []
        # Refine round 3: eval-datasets, eval-tasks, blind-test, GA pairs
        self.eval_datasets: list[dict] = []
        self.eval_tasks: list[dict] = []
        self.blind_tasks: list[dict] = []
        self.blind_votes: list[dict] = []
        self.ga_pairs: dict[str, list[dict]] = {}  # fileId -> [pairs]
        # Refine round 4: domain tags, background tasks, chunks crud
        self.tag_tree: list[dict] = []
        self.bg_tasks: list[dict] = []
        self.chunk_clean_calls: list[dict] = []
        self.optimize_calls: list[dict] = []


def _build_handler(state: _StubState):
    """Return an HTTPRequestHandler class bound to the given state.

    Implements just enough of the Easy-Dataset API to drive Scenario A.
    """

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # silence default logging

        # ── helpers ──
        def _read_json_body(self):
            length = int(self.headers.get("Content-Length", "0") or 0)
            if not length:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"_raw": raw.decode("utf-8", errors="replace")}

        def _send_json(self, status: int, payload):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _record(self, body=None):
            state.requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "body": body,
                }
            )

        # ── HTTP verbs ──
        def do_GET(self):
            self._record()
            url = urlparse(self.path)
            path = url.path

            if path == "/api/projects":
                self._send_json(200, list(state.projects.values()))
                return
            if path.startswith("/api/projects/") and path.count("/") == 3:
                pid = path.split("/")[-1]
                proj = state.projects.get(pid)
                if not proj:
                    self._send_json(404, {"error": "not found"})
                    return
                self._send_json(200, proj)
                return
            if path.endswith("/files"):
                self._send_json(200, state.files)
                return
            if path.endswith("/split"):
                self._send_json(200, {"chunks": state.chunks})
                return
            if path.endswith("/questions"):
                self._send_json(200, state.questions)
                return
            if path.endswith("/custom-prompts"):
                # Filter by query params if given
                params = parse_qs(url.query)
                wanted_type = params.get("promptType", [None])[0]
                wanted_lang = params.get("language", [None])[0]
                matching = [
                    p for p in state.custom_prompts
                    if (not wanted_type or p.get("promptType") == wanted_type)
                    and (not wanted_lang or p.get("language") == wanted_lang)
                ]
                self._send_json(200, {
                    "success": True,
                    "customPrompts": matching,
                    "templates": [],
                })
                return
            if path.endswith("/config"):
                self._send_json(200, state.task_config)
                return
            if path.endswith("/datasets"):
                # Apply filters from query params
                params = parse_qs(url.query)
                result = list(state.datasets)
                if "scoreRange" in params:
                    lo, hi = (float(x) for x in params["scoreRange"][0].split("-"))
                    result = [d for d in result if lo <= d.get("score", 0) <= hi]
                if "status" in params:
                    want = params["status"][0]
                    if want == "confirmed":
                        result = [d for d in result if d.get("confirmed")]
                    elif want == "unconfirmed":
                        result = [d for d in result if not d.get("confirmed")]
                self._send_json(200, {"data": result})
                return
            if path.endswith("/model-config"):
                self._send_json(200, state.model_configs)
                return
            if path.endswith("/questions/templates"):
                self._send_json(200, {"templates": state.templates})
                return
            if "/questions/templates/" in path:
                tid = path.rsplit("/", 1)[-1]
                tpl = next((t for t in state.templates if t.get("id") == tid), None)
                if not tpl:
                    self._send_json(404, {"error": "not found"})
                    return
                self._send_json(200, tpl)
                return
            if path.endswith("/images"):
                self._send_json(200, {"images": state.images})
                return
            if path.endswith("/dataset-conversations/export"):
                # GET-only on the server. Return a ShareGPT-shaped array.
                payload = [
                    {"messages": c.get("messages", [])}
                    for c in state.conversations
                ]
                self._send_json(200, payload)
                return
            if path.endswith("/dataset-conversations"):
                self._send_json(200, {"data": state.conversations})
                return

            # ── Round 3: eval-datasets ──
            if path.endswith("/eval-datasets"):
                params = parse_qs(url.query)
                qt = params.get("questionType", [None])[0]
                items = state.eval_datasets
                if qt:
                    items = [e for e in items if e.get("questionType") == qt]
                self._send_json(200, {"items": items, "total": len(items)})
                return
            if path.endswith("/eval-datasets/count"):
                by_type: dict[str, int] = {}
                for e in state.eval_datasets:
                    by_type[e["questionType"]] = by_type.get(e["questionType"], 0) + 1
                self._send_json(200, {
                    "code": 0,
                    "data": {
                        "total": len(state.eval_datasets),
                        "byType": by_type,
                        "hasSubjective": any(
                            e["questionType"] in ("short_answer", "open_ended")
                            for e in state.eval_datasets
                        ),
                    },
                })
                return
            if path.endswith("/eval-datasets/tags"):
                self._send_json(200, {"tags": ["seed", "auto"]})
                return
            if "/eval-datasets/" in path and path.count("/") == 5:
                eid = path.rsplit("/", 1)[-1]
                e = next((x for x in state.eval_datasets if x.get("id") == eid), None)
                if not e:
                    self._send_json(404, {"error": "not found"})
                    return
                self._send_json(200, e)
                return

            # ── Round 3: eval-tasks ──
            if path.endswith("/eval-tasks"):
                self._send_json(200, {
                    "code": 0,
                    "data": {"items": state.eval_tasks, "total": len(state.eval_tasks)},
                })
                return
            if "/eval-tasks/" in path:
                tid = path.rsplit("/", 1)[-1]
                task = next((t for t in state.eval_tasks if t.get("id") == tid), None)
                if not task:
                    self._send_json(404, {"error": "not found"})
                    return
                self._send_json(200, {
                    "code": 0,
                    "data": {
                        "task": task,
                        "results": task.get("results", []),
                        "total": len(task.get("results", [])),
                        "stats": {"correct": 0, "total": len(task.get("results", []))},
                    },
                })
                return

            # ── Round 3: blind-test tasks ──
            if path.endswith("/blind-test-tasks"):
                self._send_json(200, {
                    "code": 0,
                    "data": {"items": state.blind_tasks, "total": len(state.blind_tasks)},
                })
                return
            if path.endswith("/current") or path.endswith("/question"):
                # Both routes return the next un-voted question for the task.
                tid = path.split("/")[-2]
                task = next((t for t in state.blind_tasks if t.get("id") == tid), None)
                if not task:
                    self._send_json(404, {"error": "not found"})
                    return
                idx = task.get("currentIndex", 0)
                queue = task.get("queue", [])
                if idx >= len(queue):
                    self._send_json(200, {"completed": True, "currentIndex": idx, "totalQuestions": len(queue)})
                    return
                q = queue[idx]
                # Server randomly swaps left/right; here we use a deterministic swap by index parity.
                is_swapped = idx % 2 == 1
                if is_swapped:
                    left, right = q["answerB"], q["answerA"]
                else:
                    left, right = q["answerA"], q["answerB"]
                self._send_json(200, {
                    "questionId": q["id"],
                    "question": q["question"],
                    "leftAnswer": left,
                    "rightAnswer": right,
                    "isSwapped": is_swapped,
                    "questionIndex": idx,
                    "totalQuestions": len(queue),
                })
                return
            if "/blind-test-tasks/" in path and path.count("/") == 5:
                tid = path.rsplit("/", 1)[-1]
                task = next((t for t in state.blind_tasks if t.get("id") == tid), None)
                if not task:
                    self._send_json(404, {"error": "not found"})
                    return
                self._send_json(200, {"code": 0, "data": task})
                return

            # ── Round 3: GA pairs (per-file GET) ──
            if path.endswith("/ga-pairs"):
                file_id = path.split("/")[-2]
                self._send_json(200, {
                    "success": True,
                    "data": state.ga_pairs.get(file_id, []),
                })
                return

            # ── Round 4: domain tree tags ──
            if path.endswith("/tags"):
                self._send_json(200, {"tags": state.tag_tree})
                return

            # ── Round 4: background tasks ──
            if path.endswith("/tasks/list"):
                params = parse_qs(url.query)
                tt = params.get("taskType", [None])[0]
                items = state.bg_tasks
                if tt:
                    items = [t for t in items if t.get("taskType") == tt]
                self._send_json(200, {
                    "code": 0,
                    "data": items,
                    "total": len(items),
                    "page": int(params.get("page", ["0"])[0]),
                    "limit": int(params.get("limit", ["50"])[0]),
                })
                return
            if "/tasks/" in path and not path.endswith("/list"):
                tid = path.rsplit("/", 1)[-1]
                task = next((t for t in state.bg_tasks if t.get("id") == tid), None)
                if not task:
                    self._send_json(404, {"error": "not found"})
                    return
                self._send_json(200, {"code": 0, "data": task})
                return

            # ── Round 4: per-chunk GET ──
            if "/chunks/" in path and path.count("/") == 5:
                cid = path.rsplit("/", 1)[-1]
                ch = next((c for c in state.chunks if c.get("id") == cid), None)
                if not ch:
                    self._send_json(404, {"error": "not found"})
                    return
                self._send_json(200, ch)
                return

            self._send_json(404, {"error": f"unhandled GET {path}"})

        def do_POST(self):
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" in ctype:
                # Drain the body but don't parse — we just need the file count.
                length = int(self.headers.get("Content-Length", "0") or 0)
                self.rfile.read(length)
                body = {"_multipart": True}
            else:
                body = self._read_json_body()
            self._record(body)
            path = urlparse(self.path).path

            if path == "/api/projects":
                state.next_project += 1
                pid = f"proj-{state.next_project}"
                proj = {"id": pid, "name": body.get("name", ""), "description": body.get("description", "")}
                state.projects[pid] = proj
                self._send_json(200, proj)
                return

            if path.endswith("/model-config"):
                mid = f"mc-{len(state.model_configs) + 1}"
                cfg = {"id": mid, **body}
                state.model_configs.append(cfg)
                self._send_json(200, cfg)
                return

            if path.endswith("/files"):
                # Real Easy-Dataset reads filename from the x-file-name header.
                # Tests that go through the installed CLI hit our raw-body
                # upload code, which sets that header. Tests that hit the
                # stub directly (no upload) fall back to "uploaded.md".
                xfn = self.headers.get("x-file-name")
                if xfn:
                    from urllib.parse import unquote
                    fname = unquote(xfn)
                else:
                    fname = "uploaded.md"
                fid = f"file-{len(state.files) + 1}"
                file_rec = {"id": fid, "fileName": fname}
                state.files.append(file_rec)
                self._send_json(200, file_rec)
                return

            if path.endswith("/split"):
                state.chunks = [
                    {"id": "chunk-1", "fileName": "uploaded.md", "content": "..."},
                    {"id": "chunk-2", "fileName": "uploaded.md", "content": "..."},
                ]
                self._send_json(200, {"chunks": state.chunks})
                return

            if path.endswith("/generate-questions"):
                if body.get("sourceType") == "image":
                    img_ids = body.get("imageIds") or [i["id"] for i in state.images]
                    new_qs = [
                        {
                            "id": f"q-img-{len(state.questions) + i + 1}",
                            "question": f"VQA-{i + 1}?",
                            "answered": False,
                            "imageId": img_ids[i % len(img_ids)] if img_ids else None,
                            "sourceType": "image",
                        }
                        for i in range(len(img_ids))
                    ]
                    state.questions.extend(new_qs)
                    self._send_json(200, {"count": len(new_qs), "questions": new_qs})
                    return
                state.questions = [
                    {"id": f"q-{i}", "question": f"Q{i}?", "answered": False, "chunkId": "chunk-1"}
                    for i in range(1, 4)
                ]
                self._send_json(200, {"count": len(state.questions)})
                return

            if path.endswith("/custom-prompts"):
                if "prompts" in body and isinstance(body["prompts"], list):
                    state.custom_prompts.extend(body["prompts"])
                    self._send_json(200, {"success": True, "results": body["prompts"]})
                    return
                # Single save
                state.custom_prompts.append({k: v for k, v in body.items()})
                self._send_json(200, {"success": True, "result": body})
                return

            if path.endswith("/custom-split"):
                # 案例 2 / 案例 4: separator-based custom split
                points = body.get("splitPoints", [])
                state.chunks = [
                    {
                        "id": f"chunk-{i + 1}",
                        "fileId": body.get("fileId"),
                        "fileName": body.get("fileName"),
                        "content": "...",
                    }
                    for i in range(len(points) + 1)
                ]
                self._send_json(200, {
                    "success": True,
                    "totalChunks": len(state.chunks),
                })
                return

            if "/datasets/" in path and path.endswith("/evaluate"):
                # Per-dataset evaluation
                state.evaluation_calls.append({"path": path, "body": body})
                dataset_id = path.split("/")[-2]
                # Simulate a multi-dimensional score (4 dims, 0-5 with 0.5 step)
                self._send_json(200, {
                    "success": True,
                    "data": {
                        "datasetId": dataset_id,
                        "score": 4.5,
                        "questionQuality": 4.5,
                        "answerQuality": 4.5,
                        "textRelevance": 5.0,
                        "overallConsistency": 4.0,
                    },
                })
                return

            if path.endswith("/datasets/batch-evaluate"):
                state.evaluation_calls.append({"path": path, "body": body})
                self._send_json(200, {
                    "success": True,
                    "data": {"taskId": "task-eval-1"},
                })
                return

            if path.endswith("/datasets/export"):
                # Honor selectedIds if present (for score-filtered exports)
                selected = body.get("selectedIds")
                if selected:
                    payload = [
                        {
                            "instruction": d["question"],
                            "input": "",
                            "output": d.get("answer", ""),
                        }
                        for d in state.datasets
                        if d["id"] in selected
                    ]
                else:
                    payload = [
                        {"instruction": q["question"], "input": "", "output": f"A for {q['question']}"}
                        for q in state.questions
                    ]
                self._send_json(200, payload)
                return

            # ── Refine round 2: question templates ──
            if path.endswith("/questions/templates"):
                tid = f"tpl-{len(state.templates) + 1}"
                tpl = {"id": tid, **body}
                state.templates.append(tpl)
                # If autoGenerate, materialize a question per matching source.
                if body.get("autoGenerate"):
                    if body.get("sourceType") == "image":
                        for img in state.images:
                            state.questions.append({
                                "id": f"q-img-{len(state.questions) + 1}",
                                "question": body.get("question", ""),
                                "answered": False,
                                "imageId": img.get("id"),
                                "templateId": tid,
                            })
                    else:
                        for ch in state.chunks:
                            state.questions.append({
                                "id": f"q-tpl-{len(state.questions) + 1}",
                                "question": body.get("question", ""),
                                "answered": False,
                                "chunkId": ch.get("id"),
                                "templateId": tid,
                            })
                self._send_json(200, tpl)
                return

            # ── Refine round 2: image imports ──
            if path.endswith("/images/zip-import"):
                # Stub: pretend we extracted 2 images per zip upload.
                for i in range(1, 3):
                    state.images.append({
                        "id": f"img-{len(state.images) + 1}",
                        "fileName": f"car{i}.png",
                    })
                self._send_json(200, {
                    "success": True,
                    "imported_count": 2,
                    "images": state.images[-2:],
                })
                return
            if path.endswith("/images/pdf-convert"):
                # Stub: pretend the PDF had 3 pages.
                for i in range(1, 4):
                    state.images.append({
                        "id": f"img-{len(state.images) + 1}",
                        "fileName": f"page-{i}.png",
                    })
                self._send_json(200, {
                    "success": True,
                    "pages": 3,
                    "images": state.images[-3:],
                })
                return

            # ── Refine round 2: distillation ──
            if path.endswith("/distill/tags"):
                state.distill_tag_calls.append(body)
                # Generate child tags based on parentTag for visibility.
                children = [
                    {"label": f"{body.get('parentTag', 'root')}-子{i}"}
                    for i in range(1, body.get("count", 3) + 1)
                ]
                self._send_json(200, {"success": True, "tags": children})
                return
            if path.endswith("/distill/questions"):
                state.distill_question_calls.append(body)
                # Materialize question rows so the multi-turn step can find them.
                count = body.get("count", 3)
                tag = body.get("currentTag", "?")
                new_qs = [
                    {
                        "id": f"q-distill-{len(state.questions) + i + 1}",
                        "question": f"{tag} 问题 {i + 1}?",
                        "answered": False,
                        "tagPath": body.get("tagPath"),
                    }
                    for i in range(count)
                ]
                state.questions.extend(new_qs)
                self._send_json(200, {"success": True, "questions": new_qs})
                return

            # ── Refine round 2: multi-turn dialogue datasets ──
            if path.endswith("/dataset-conversations"):
                qid = body.get("questionId", "?")
                rounds = body.get("rounds", 3)
                msgs = []
                for r in range(rounds):
                    msgs.append({"from": body.get("roleA", "user"), "value": f"Q{r + 1}"})
                    msgs.append({"from": body.get("roleB", "assistant"), "value": f"A{r + 1}"})
                conv = {
                    "id": f"conv-{len(state.conversations) + 1}",
                    "questionId": qid,
                    "rounds": rounds,
                    "roleA": body.get("roleA"),
                    "roleB": body.get("roleB"),
                    "messages": msgs,
                }
                state.conversations.append(conv)
                self._send_json(200, conv)
                return

            # ── Round 3: eval-datasets ──
            if path.endswith("/eval-datasets/sample"):
                ids = [e["id"] for e in state.eval_datasets][: body.get("limit", 50)]
                self._send_json(200, {
                    "code": 0,
                    "data": {"total": len(state.eval_datasets), "selectedCount": len(ids), "ids": ids},
                })
                return
            if path.endswith("/eval-datasets/export"):
                fmt = body.get("format", "json")
                if fmt == "jsonl":
                    payload = b"\n".join(
                        json.dumps(e).encode("utf-8") for e in state.eval_datasets
                    )
                elif fmt == "csv":
                    payload = b"id,question\n" + b"\n".join(
                        f'{e["id"]},{e.get("question","")}'.encode("utf-8")
                        for e in state.eval_datasets
                    )
                else:
                    payload = json.dumps(state.eval_datasets).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if path.endswith("/eval-datasets/import"):
                # Stub: pretend we imported 3 rows.
                for i in range(3):
                    state.eval_datasets.append({
                        "id": f"e-imp-{len(state.eval_datasets) + 1}",
                        "question": f"imported {i}",
                        "questionType": "short_answer",
                        "correctAnswer": "x",
                    })
                self._send_json(200, {"code": 0, "data": {"total": 3}})
                return
            if path.endswith("/eval-datasets"):
                # Single create
                eid = f"e-{len(state.eval_datasets) + 1}"
                row = {
                    "id": eid,
                    "question": body.get("question", ""),
                    "questionType": body.get("questionType", "short_answer"),
                    "options": body.get("options"),
                    "correctAnswer": body.get("correctAnswer"),
                    "tags": body.get("tags", ""),
                    "note": body.get("note", ""),
                }
                state.eval_datasets.append(row)
                self._send_json(200, {"success": True, "evalDataset": row, **row})
                return

            if "/datasets/" in path and path.endswith("/copy-to-eval"):
                ds_id = path.split("/")[-2]
                src = next((d for d in state.datasets if d["id"] == ds_id), None)
                eid = f"e-copy-{len(state.eval_datasets) + 1}"
                row = {
                    "id": eid,
                    "question": (src or {}).get("question", "?"),
                    "questionType": "short_answer",
                    "correctAnswer": (src or {}).get("answer", ""),
                }
                state.eval_datasets.append(row)
                self._send_json(200, {"success": True, "evalDataset": row})
                return

            if path.endswith("/datasets/generate-eval-variant"):
                count = body.get("count", 3)
                variants = [
                    {"question": f"variant-{i}", "options": ["A", "B"], "correctAnswer": "[0]"}
                    for i in range(count)
                ]
                self._send_json(200, {"success": True, "data": variants})
                return

            # ── Round 3: eval-tasks ──
            if path.endswith("/eval-tasks"):
                tasks_created = []
                for m in body.get("models", []):
                    task = {
                        "id": f"task-{len(state.eval_tasks) + 1}",
                        "model": m,
                        "evalDatasetIds": body.get("evalDatasetIds", []),
                        "language": body.get("language"),
                        "results": [
                            {"evalDatasetId": eid, "isCorrect": True, "score": 4.5}
                            for eid in body.get("evalDatasetIds", [])
                        ],
                    }
                    state.eval_tasks.append(task)
                    tasks_created.append(task)
                self._send_json(200, {"code": 0, "data": tasks_created, "message": "ok"})
                return

            # ── Round 3: blind-test tasks ──
            if path.endswith("/blind-test-tasks"):
                queue = []
                for eid in body.get("evalDatasetIds", []):
                    src = next((e for e in state.eval_datasets if e["id"] == eid), None)
                    queue.append({
                        "id": eid,
                        "question": (src or {}).get("question", "?"),
                        "answerA": f"A-says: {(src or {}).get('question', '?')}",
                        "answerB": f"B-says: {(src or {}).get('question', '?')} (longer answer)",
                    })
                task = {
                    "id": f"bt-{len(state.blind_tasks) + 1}",
                    "modelA": body.get("modelA"),
                    "modelB": body.get("modelB"),
                    "queue": queue,
                    "currentIndex": 0,
                    "scores": {"modelA": 0, "modelB": 0, "tie": 0},
                }
                state.blind_tasks.append(task)
                self._send_json(200, {"code": 0, "data": task, "message": "created"})
                return
            if "/blind-test-tasks/" in path and path.endswith("/vote"):
                tid = path.split("/")[-2]
                task = next((t for t in state.blind_tasks if t.get("id") == tid), None)
                if not task:
                    self._send_json(404, {"error": "not found"})
                    return
                state.blind_votes.append({"taskId": tid, **body})
                # Map vote → real model accounting for swap
                vote_value = body.get("vote")
                is_swapped = body.get("isSwapped", False)
                if vote_value == "left":
                    winner = "modelB" if is_swapped else "modelA"
                elif vote_value == "right":
                    winner = "modelA" if is_swapped else "modelB"
                else:
                    winner = "tie"
                task["scores"][winner] = task["scores"].get(winner, 0) + 1
                task["currentIndex"] += 1
                completed = task["currentIndex"] >= len(task["queue"])
                self._send_json(200, {
                    "code": 0,
                    "data": {
                        "success": True,
                        "isCompleted": completed,
                        "currentIndex": task["currentIndex"],
                        "totalCount": len(task["queue"]),
                        "scores": task["scores"],
                    },
                })
                return

            # ── Round 4: datasets import + optimize ──
            if path.endswith("/datasets/import"):
                records = body.get("datasets", [])
                for r in records:
                    state.datasets.append({
                        "id": f"d-imp-{len(state.datasets) + 1}",
                        "question": r.get("question"),
                        "answer": r.get("answer"),
                        "cot": r.get("cot", ""),
                        "score": r.get("score", 0),
                        "confirmed": r.get("confirmed", False),
                    })
                self._send_json(200, {
                    "success": len(records),
                    "total": len(records),
                    "failed": 0,
                    "skipped": 0,
                    "errors": [],
                })
                return
            if path.endswith("/datasets/optimize"):
                state.optimize_calls.append(body)
                ds_id = body.get("datasetId")
                target = next((d for d in state.datasets if d["id"] == ds_id), None)
                if target:
                    target["answer"] = f"[optimized: {body.get('advice', '')}] {target.get('answer', '')}"
                    target["cot"] = "(re-generated)"
                self._send_json(200, {"success": True, "dataset": target or {"id": ds_id}})
                return

            # ── Round 4: chunks clean (per-id) ──
            if "/chunks/" in path and path.endswith("/clean"):
                cid = path.split("/")[-2]
                state.chunk_clean_calls.append({"chunkId": cid, "body": body})
                target = next((c for c in state.chunks if c["id"] == cid), None)
                if target:
                    original_len = len(target.get("content", ""))
                    target["content"] = "[cleaned] " + target.get("content", "")
                    cleaned_len = len(target["content"])
                else:
                    original_len = cleaned_len = 0
                self._send_json(200, {
                    "chunkId": cid,
                    "originalLength": original_len,
                    "cleanedLength": cleaned_len,
                    "success": True,
                    "message": "ok",
                })
                return
            # batch-edit / batch-content
            if path.endswith("/chunks/batch-edit"):
                count = 0
                for cid in body.get("chunkIds", []):
                    target = next((c for c in state.chunks if c["id"] == cid), None)
                    if target:
                        if body.get("position") == "start":
                            target["content"] = body.get("content", "") + "\n\n" + target.get("content", "")
                        else:
                            target["content"] = target.get("content", "") + "\n\n" + body.get("content", "")
                        count += 1
                self._send_json(200, {"success": True, "updatedCount": count, "message": "ok"})
                return
            if path.endswith("/chunks/batch-content"):
                names = body.get("chunkNames", [])
                lookup = {
                    n: next(
                        (c.get("content", "") for c in state.chunks if c.get("name") == n or c.get("id") == n),
                        "",
                    )
                    for n in names
                }
                self._send_json(200, lookup)
                return

            # ── Round 4: domain tree tags (POST = lookup by tag name) ──
            if path.endswith("/tags") and "tagName" in body:
                # Return matching questions (stub: filter by label)
                wanted = body["tagName"]
                matches = [q for q in state.questions if q.get("label") == wanted]
                self._send_json(200, {"questions": matches})
                return

            # ── Round 4: background tasks (create) ──
            if path.endswith("/tasks") and not path.endswith("/list"):
                # POST /tasks creates a new background task row. The body
                # carries taskType, modelInfo, etc.
                task_type = body.get("taskType")
                task = {
                    "id": f"bg-{len(state.bg_tasks) + 1}",
                    "taskType": task_type,
                    "status": 0,  # processing
                    "modelInfo": body.get("modelInfo"),
                    "language": body.get("language"),
                    "completedCount": 0,
                    "totalCount": body.get("totalCount", 0),
                    "note": body.get("note", ""),
                }
                # Stub side-effect: image-question-generation creates one
                # VQA question per image immediately and marks the task done.
                if task_type == "image-question-generation":
                    note_data = body.get("note") or {}
                    if isinstance(note_data, str):
                        try:
                            note_data = json.loads(note_data)
                        except Exception:
                            note_data = {}
                    qcount = int(note_data.get("questionCount", 1)) if isinstance(note_data, dict) else 1
                    created = 0
                    for img in state.images:
                        for n in range(qcount):
                            state.questions.append({
                                "id": f"q-img-{img['id']}-{n + 1}",
                                "question": f"VQA-{img['id']}-{n + 1}?",
                                "chunkId": None,
                                "imageId": img.get("id"),
                                "answered": False,
                            })
                            created += 1
                    task["status"] = 1  # completed
                    task["completedCount"] = created
                    task["totalCount"] = created
                state.bg_tasks.append(task)
                self._send_json(200, {"code": 0, "data": task, "message": "ok"})
                return

            # ── Round 4: questions create (overlaps with /generate-questions) ──
            if path.endswith("/questions") and "question" in body and "model" not in body:
                qid = f"q-manual-{len(state.questions) + 1}"
                row = {
                    "id": qid,
                    "question": body["question"],
                    "chunkId": body.get("chunkId"),
                    "imageId": body.get("imageId"),
                    "label": body.get("label"),
                    "answered": False,
                }
                state.questions.append(row)
                self._send_json(200, row)
                return

            # ── Round 3: GA pairs ──
            if path.endswith("/batch-generateGA"):
                generated = []
                for fid in body.get("fileIds", []):
                    pairs = []
                    for n in range(1, 6):
                        pairs.append({
                            "id": f"ga-{fid}-{n}",
                            "fileId": fid,
                            "pairNumber": n,
                            "genreTitle": f"genre-{n}",
                            "genreDesc": "...",
                            "audienceTitle": f"audience-{n}",
                            "audienceDesc": "...",
                            "isActive": True,
                        })
                    if body.get("appendMode"):
                        state.ga_pairs.setdefault(fid, []).extend(pairs)
                    else:
                        state.ga_pairs[fid] = pairs
                    generated.extend(pairs)
                self._send_json(200, {
                    "success": True,
                    "data": generated,
                    "summary": {
                        "total": len(generated),
                        "success": len(generated),
                        "failure": 0,
                        "processed": len(body.get("fileIds", [])),
                    },
                })
                return
            if path.endswith("/batch-add-manual-ga"):
                pair = body.get("gaPair", {})
                added = []
                for fid in body.get("fileIds", []):
                    n = len(state.ga_pairs.get(fid, [])) + 1
                    new_pair = {
                        "id": f"ga-{fid}-manual-{n}",
                        "fileId": fid,
                        "pairNumber": n,
                        "isActive": True,
                        **pair,
                    }
                    state.ga_pairs.setdefault(fid, []).append(new_pair)
                    added.append(new_pair)
                self._send_json(200, {"success": True, "data": added})
                return
            if path.endswith("/ga-pairs"):
                # Per-file generate (single file)
                file_id = path.split("/")[-2]
                pairs = []
                for n in range(1, 6):
                    pairs.append({
                        "id": f"ga-{file_id}-{n}",
                        "pairNumber": n,
                        "genreTitle": f"genre-{n}",
                        "audienceTitle": f"audience-{n}",
                        "isActive": True,
                    })
                if body.get("appendMode"):
                    state.ga_pairs.setdefault(file_id, []).extend(pairs)
                else:
                    state.ga_pairs[file_id] = pairs
                self._send_json(200, {"success": True, "data": pairs, "total": 5})
                return

            if path.endswith("/datasets"):
                qid = body.get("questionId", "?")
                rec = {
                    "id": f"d-{len(state.datasets) + 1}",
                    "questionId": qid,
                    "question": next(
                        (q["question"] for q in state.questions if q["id"] == qid),
                        "?",
                    ),
                    "answer": f"A for {qid}",
                    "cot": "",
                    "score": 0,
                    "confirmed": False,
                }
                state.datasets.append(rec)
                self._send_json(200, rec)
                return

            self._send_json(404, {"error": f"unhandled POST {path}"})

        def do_PUT(self):
            body = self._read_json_body()
            self._record(body)
            path = urlparse(self.path).path
            if path.endswith("/config"):
                # Easy-Dataset PUT /config wraps everything under "prompts"
                merged = body.get("prompts", body)
                state.task_config.update(merged)
                # Also score-bump every dataset on a "confirm + score" PUT,
                # see TestFullPipelineCase4 below.
                self._send_json(200, dict(state.task_config))
                return
            if "/questions/templates/" in path:
                tid = path.rsplit("/", 1)[-1]
                tpl = next((t for t in state.templates if t.get("id") == tid), None)
                if tpl:
                    tpl.update(body)
                    # Re-trigger autoGenerate materialization on update
                    if body.get("autoGenerate"):
                        if tpl.get("sourceType") == "image":
                            for img in state.images:
                                if not any(
                                    q.get("imageId") == img.get("id") and q.get("templateId") == tid
                                    for q in state.questions
                                ):
                                    state.questions.append({
                                        "id": f"q-img-{len(state.questions) + 1}",
                                        "question": tpl.get("question", ""),
                                        "answered": False,
                                        "imageId": img.get("id"),
                                        "templateId": tid,
                                    })
                self._send_json(200, tpl or {"id": tid, **body})
                return
            # Round 3: eval-task interrupt + blind-task interrupt
            if "/eval-tasks/" in path:
                tid = path.rsplit("/", 1)[-1]
                self._send_json(200, {"code": 0, "message": "Task interrupted", "taskId": tid})
                return
            if "/blind-test-tasks/" in path:
                tid = path.rsplit("/", 1)[-1]
                self._send_json(200, {"code": 0, "message": "Task interrupted", "taskId": tid})
                return
            # Round 4: tags PUT (save / create / update)
            if path.endswith("/tags"):
                tag_body = body.get("tags", {})
                tid = tag_body.get("id")
                if not tid:
                    tid = f"tag-{sum(1 for _ in self._walk_tag_tree(state.tag_tree)) + 1}" if hasattr(self, "_walk_tag_tree") else f"tag-{len([n for n in _walk_tags(state.tag_tree)]) + 1}"
                    new_node = {
                        "id": tid,
                        "label": tag_body.get("label"),
                        "parentId": tag_body.get("parentId"),
                        "children": [],
                    }
                    parent_id = tag_body.get("parentId")
                    if parent_id is None:
                        state.tag_tree.append(new_node)
                    else:
                        parent = _find_tag_node(state.tag_tree, parent_id)
                        if parent is not None:
                            parent.setdefault("children", []).append(new_node)
                        else:
                            state.tag_tree.append(new_node)
                    self._send_json(200, {"tags": new_node})
                    return
                # Update path
                node = _find_tag_node(state.tag_tree, tid)
                if node is None:
                    self._send_json(404, {"error": "tag not found"})
                    return
                if "label" in tag_body:
                    node["label"] = tag_body["label"]
                if "parentId" in tag_body and tag_body["parentId"] != node.get("parentId"):
                    # Reparent: detach from old parent, attach to new
                    _detach_tag(state.tag_tree, tid)
                    node["parentId"] = tag_body["parentId"]
                    if tag_body["parentId"] is None:
                        state.tag_tree.append(node)
                    else:
                        parent = _find_tag_node(state.tag_tree, tag_body["parentId"])
                        if parent is not None:
                            parent.setdefault("children", []).append(node)
                self._send_json(200, {"tags": node})
                return
            # Round 4: questions PUT (update existing)
            if path.endswith("/questions"):
                qid = body.get("id")
                target = next((q for q in state.questions if q.get("id") == qid), None)
                if target:
                    target.update(body)
                self._send_json(200, target or body)
                return
            self._send_json(404, {"error": f"unhandled PUT {path}"})

        def do_PATCH(self):
            body = self._read_json_body()
            self._record(body)
            url = urlparse(self.path)
            path = url.path
            # Round 3: GA pair activation toggle
            if path.endswith("/ga-pairs"):
                file_id = path.split("/")[-2]
                gid = body.get("gaPairId")
                for p in state.ga_pairs.get(file_id, []):
                    if p.get("id") == gid:
                        p["isActive"] = body.get("isActive", True)
                        self._send_json(200, {"success": True, "data": p})
                        return
                self._send_json(404, {"error": "ga_pair not found"})
                return
            # Datasets PATCH — TWO endpoints, both PATCH-only:
            #   /datasets/{id}      → score / tags / note (review metadata)
            #   /datasets?id={id}   → answer / cot / question / confirmed (content)
            if path.endswith("/datasets") and "id" in parse_qs(url.query):
                ds_id = parse_qs(url.query)["id"][0]
                for d in state.datasets:
                    if d["id"] == ds_id:
                        d.update(body)
                        self._send_json(200, {"success": True, "dataset": d})
                        return
                self._send_json(404, {"error": "dataset not found"})
                return
            if "/datasets/" in path and "/datasets/export" not in path and "/datasets/import" not in path and "/datasets/optimize" not in path and path.count("/") >= 5:
                ds_id = path.rsplit("/", 1)[-1]
                for d in state.datasets:
                    if d["id"] == ds_id:
                        d.update(body)
                        self._send_json(200, {"success": True, "dataset": d})
                        return
                self._send_json(404, {"error": "dataset not found"})
                return
            # Round 4: chunks PATCH (content update)
            if "/chunks/" in path and path.count("/") == 5:
                cid = path.rsplit("/", 1)[-1]
                target = next((c for c in state.chunks if c["id"] == cid), None)
                if target:
                    target.update(body)
                    self._send_json(200, target)
                    return
                self._send_json(404, {"error": "chunk not found"})
                return
            # Round 4: background tasks PATCH (status / progress / interrupt)
            if "/tasks/" in path:
                tid = path.rsplit("/", 1)[-1]
                target = next((t for t in state.bg_tasks if t.get("id") == tid), None)
                if target:
                    target.update(body)
                    self._send_json(200, {"code": 0, "data": target})
                    return
                self._send_json(404, {"error": "task not found"})
                return
            self._send_json(200, {"updated": True, **body})

        def do_DELETE(self):
            self._record()
            url = urlparse(self.path)
            path = url.path
            if path.endswith("/images"):
                params = parse_qs(url.query)
                img_id = params.get("imageId", [None])[0]
                state.images = [i for i in state.images if i.get("id") != img_id]
                self._send_json(200, {"deleted": True, "imageId": img_id})
                return
            if "/questions/templates/" in path:
                tid = path.rsplit("/", 1)[-1]
                state.templates = [t for t in state.templates if t.get("id") != tid]
                self._send_json(200, {"deleted": True, "id": tid})
                return
            # Round 3: eval-datasets bulk + single delete
            if path.endswith("/eval-datasets"):
                # bulk delete with json body
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = {}
                if length:
                    try:
                        body = json.loads(self.rfile.read(length))
                    except json.JSONDecodeError:
                        body = {}
                ids = set(body.get("ids", []))
                state.eval_datasets = [
                    e for e in state.eval_datasets if e.get("id") not in ids
                ]
                self._send_json(200, {"success": True, "deleted": len(ids)})
                return
            if "/eval-datasets/" in path:
                eid = path.rsplit("/", 1)[-1]
                state.eval_datasets = [
                    e for e in state.eval_datasets if e.get("id") != eid
                ]
                self._send_json(200, {"success": True, "id": eid})
                return
            if "/eval-tasks/" in path:
                tid = path.rsplit("/", 1)[-1]
                state.eval_tasks = [t for t in state.eval_tasks if t.get("id") != tid]
                self._send_json(200, {"code": 0, "message": "Deleted"})
                return
            if "/blind-test-tasks/" in path:
                tid = path.rsplit("/", 1)[-1]
                state.blind_tasks = [t for t in state.blind_tasks if t.get("id") != tid]
                self._send_json(200, {"code": 0, "message": "Task deleted"})
                return
            # Round 4: tags DELETE (cascade)
            if path.endswith("/tags"):
                params = parse_qs(url.query)
                tid = params.get("id", [None])[0]
                if tid:
                    _detach_tag(state.tag_tree, tid)
                self._send_json(200, {"success": True, "message": "deleted"})
                return
            # Round 4: chunks single DELETE
            if "/chunks/" in path and path.count("/") == 5:
                cid = path.rsplit("/", 1)[-1]
                state.chunks = [c for c in state.chunks if c.get("id") != cid]
                self._send_json(200, {"message": "Text block deleted successfully"})
                return
            # Round 4: questions single DELETE
            if "/questions/" in path:
                qid = path.rsplit("/", 1)[-1]
                state.questions = [q for q in state.questions if q.get("id") != qid]
                self._send_json(200, {"success": True, "message": "Delete successful"})
                return
            # Round 4: background tasks DELETE
            if "/tasks/" in path:
                tid = path.rsplit("/", 1)[-1]
                state.bg_tasks = [t for t in state.bg_tasks if t.get("id") != tid]
                self._send_json(200, {"code": 0, "message": "deleted"})
                return
            self._send_json(200, {"deleted": True})

    return Handler


@pytest.fixture
def stub_server():
    state = _StubState()
    server = HTTPServer(("127.0.0.1", 0), _build_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base_url, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# ── helper to invoke the CLI ──────────────────────────────────────────


def _run(args, *, env_extra=None, check=True):
    env = os.environ.copy()
    # Force CLI to ignore any pre-existing user session for isolation
    env["EDS_BASE_URL"] = ""  # cleared per-call by callers if needed
    env["NO_COLOR"] = "1"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        CLI_BASE + list(args),
        capture_output=True,
        text=True,
        env=env,
        check=check,
    )


# ── 1. CLI subprocess sanity ──────────────────────────────────────────


class TestCLISubprocess:
    def test_help(self):
        r = _run(["--help"], check=False)
        assert r.returncode == 0
        assert "Usage:" in r.stdout

    def test_version(self):
        r = _run(["--version"], check=False)
        assert r.returncode == 0
        # Just assert the binary name is present and a semver-ish token follows.
        # Avoid hard-coding the exact version so the test doesn't break on bumps.
        assert "easyds" in r.stdout
        assert "version" in r.stdout

    def test_json_help_does_not_crash(self):
        r = _run(["--json", "--help"], check=False)
        assert r.returncode == 0

    def test_status_unreachable_server_clear_error(self, tmp_path):
        # Use a port nothing listens on (bind+close trick).
        import socket as _s

        s = _s.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        env = {
            "EDS_BASE_URL": f"http://127.0.0.1:{port}",
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
        }
        r = _run(["--json", "status"], env_extra=env, check=False)
        assert r.returncode == 2
        assert "BackendUnavailable" in r.stderr or "not reachable" in r.stderr


# ── 2. Stub-server end-to-end ─────────────────────────────────────────


class TestFullPipelineSubprocess:
    """Scenario A: full pipeline driven through the installed CLI subprocess."""

    def test_scenario_a(self, stub_server, tmp_path):
        base_url, state = stub_server
        env = {
            "EDS_BASE_URL": base_url,
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
        }

        def run(*args, expect_json=True):
            r = _run(["--json", *args], env_extra=env, check=False)
            assert r.returncode == 0, f"command {args} failed: {r.stderr}"
            if expect_json and r.stdout.strip():
                try:
                    return json.loads(r.stdout)
                except json.JSONDecodeError:
                    return r.stdout
            return r.stdout

        # 1. status
        status = run("status")
        assert status["server_status"] == "ok"

        # 2. project new — also persists current_project_id in session
        proj = run("project", "new", "--name", "demo")
        assert proj["id"].startswith("proj-")
        pid = proj["id"]

        # 3. model set
        mc = run(
            "model", "set",
            "--provider-id", "openai",
            "--endpoint", "https://api.openai.com/v1",
            "--api-key", "sk-test",
            "--model-id", "gpt-4o-mini",
        )
        assert mc["id"].startswith("mc-")

        # 4. files upload
        sample = tmp_path / "spec.md"
        sample.write_text("# Spec\n\nSome content.\n")
        upload = run("files", "upload", str(sample))
        assert upload["id"].startswith("file-")

        # 5. chunks split
        split = run("chunks", "split", "--file", "spec.md")
        assert "chunks" in split or isinstance(split, dict)

        # 6. questions generate
        qg = run("questions", "generate")
        assert qg["count"] == 3

        # 7. datasets generate
        dg = run("datasets", "generate")
        assert isinstance(dg, list)
        assert len(dg) == 3

        # 8. export
        out = tmp_path / "alpaca.json"
        export_result = run(
            "export", "run",
            "-o", str(out),
            "--format", "alpaca",
            "--all",
            "--overwrite",
        )
        assert export_result["count"] == 3
        assert os.path.exists(out)

        records = json.loads(out.read_text())
        assert isinstance(records, list)
        assert len(records) == 3
        for rec in records:
            assert "instruction" in rec and "output" in rec
        print(f"\n  Alpaca export: {out} ({os.path.getsize(out):,} bytes, {len(records)} records)")

        # Spot-check that the requests we recorded match the API contract.
        recorded_paths = [(r["method"], urlparse(r["path"]).path) for r in state.requests]
        assert ("POST", "/api/projects") in recorded_paths
        assert ("POST", f"/api/projects/{pid}/model-config") in recorded_paths
        assert ("POST", f"/api/projects/{pid}/files") in recorded_paths
        assert ("POST", f"/api/projects/{pid}/split") in recorded_paths
        assert ("POST", f"/api/projects/{pid}/generate-questions") in recorded_paths
        assert ("POST", f"/api/projects/{pid}/datasets") in recorded_paths
        assert ("POST", f"/api/projects/{pid}/datasets/export") in recorded_paths


# ── 2b. Refine round 1: Case 4 — clean → eval → score-filter export ──


class TestFullPipelineCase4:
    """Reproduces spec/03-case-studies.md §案例 4 (AI 智能体安全数据集) end-to-end:

    custom-separator split → custom prompts (cleaning + evaluation) → questions
    → datasets → multi-dim evaluation → score-filtered Alpaca export.

    Every CLI command is invoked through the installed `easyds` binary so this
    test exercises the same code path AI agents will hit.
    """

    def test_case_4_workflow(self, stub_server, tmp_path):
        base_url, state = stub_server
        env = {
            "EDS_BASE_URL": base_url,
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
        }

        def run(*args, expect_json=True):
            r = _run(["--json", *args], env_extra=env, check=False)
            assert r.returncode == 0, f"command {args} failed: {r.stderr}"
            if expect_json and r.stdout.strip():
                try:
                    return json.loads(r.stdout)
                except json.JSONDecodeError:
                    return r.stdout
            return r.stdout

        # 1. Bootstrap project + model
        proj = run("project", "new", "--name", "ai-safety-corpus")
        pid = proj["id"]
        mc = run(
            "model", "set",
            "--provider-id", "openai",
            "--endpoint", "https://api.openai.com/v1",
            "--api-key", "sk-test",
            "--model-id", "gpt-4o-mini",
        )
        assert mc["id"].startswith("mc-")

        # 2. Upload the white paper
        whitepaper = tmp_path / "ai-safety.md"
        whitepaper.write_text(
            "## 第一章 引言\n这是引言段落。\n"
            "## 第二章 风险\n风险描述。\n"
            "## 第三章 治理\n治理框架。\n",
            encoding="utf-8",
        )
        upload = run("files", "upload", str(whitepaper))
        assert upload["id"].startswith("file-")

        # 3. Custom-separator split using '## 第' (案例 4)
        split = run(
            "chunks", "split",
            "--file", "ai-safety.md",  # match the upload's filename
            "--separator", "## 第",
            "--content-file", str(whitepaper),
        )
        assert split.get("totalChunks", 0) > 0
        # Verify the stub received a /custom-split call (NOT the regular /split)
        custom_split_calls = [
            r for r in state.requests
            if r["method"] == "POST" and r["path"].endswith("/custom-split")
        ]
        assert len(custom_split_calls) == 1
        cs_body = custom_split_calls[0]["body"]
        assert cs_body["fileName"] == "ai-safety.md"
        assert isinstance(cs_body["splitPoints"], list)
        assert len(cs_body["splitPoints"]) >= 2

        # 4. Set a custom data-cleaning prompt with required template vars
        clean_prompt = tmp_path / "clean.md"
        clean_prompt.write_text(
            "请清洗以下文本（长度 {{textLength}} 字符）：\n\n{{text}}\n\n"
            "去除引用标记、HTML 标签，并生成约 100 字摘要。",
            encoding="utf-8",
        )
        prompt_save = run(
            "prompts", "set",
            "--type", "dataClean",
            "--key", "DATA_CLEAN_PROMPT",
            "--language", "zh-CN",
            "--file", str(clean_prompt),
            "--require-var", "text",
            "--require-var", "textLength",
        )
        assert prompt_save["success"] is True

        # 5. List custom prompts and confirm the clean prompt is registered
        prompts_list = run("prompts", "list", "--type", "dataClean")
        assert any(
            p.get("promptType") == "dataClean"
            and p.get("promptKey") == "DATA_CLEAN_PROMPT"
            for p in prompts_list
        )

        # 6. Generate questions and answers
        run("questions", "generate")
        run("datasets", "generate")
        assert len(state.datasets) >= 1

        # 7. Set a custom datasetEvaluation prompt then run a batch eval
        eval_prompt = tmp_path / "eval.md"
        eval_prompt.write_text(
            "评估问题: {{question}}\n答案: {{answer}}\n"
            "上下文: {{chunk}}\n请按 4 个维度打分（0-5）。",
            encoding="utf-8",
        )
        eval_result = run(
            "datasets", "evaluate",
            "--prompt-file", str(eval_prompt),
            "--language", "zh-CN",
        )
        assert eval_result["success"] is True
        assert eval_result["data"]["taskId"] == "task-eval-1"

        # Manually score one of the datasets server-side so the
        # score-filtered export has something to return. (In real usage the
        # async batch task would do this; here we shortcut via PATCH — the
        # server route is PATCH-only since the route.js refactor.)
        first_id = state.datasets[0]["id"]
        run("datasets", "confirm", first_id)
        run("datasets", "edit", first_id, "--score", "4.5", "--confirmed")

        # 8. Filter list with --score-gte
        filtered = run("datasets", "list", "--score-gte", "4")
        assert isinstance(filtered, list)
        assert len(filtered) >= 1
        assert all(d.get("score", 0) >= 4 for d in filtered)

        # 9. Score-filtered export
        out = tmp_path / "high-quality.json"
        export_result = run(
            "export", "run",
            "-o", str(out),
            "--format", "alpaca",
            "--all",
            "--overwrite",
            "--score-gte", "4",
        )
        assert export_result["count"] >= 1
        records = json.loads(out.read_text())
        assert all("instruction" in r and "output" in r for r in records)
        print(
            f"\n  Case 4 export: {out} ({os.path.getsize(out):,} bytes, "
            f"{len(records)} high-quality records)"
        )

        # 10. Spot-check the recorded request log for every key endpoint
        recorded = [(r["method"], urlparse(r["path"]).path) for r in state.requests]
        expected = [
            ("POST", "/api/projects"),
            ("POST", f"/api/projects/{pid}/model-config"),
            ("POST", f"/api/projects/{pid}/files"),
            ("POST", f"/api/projects/{pid}/custom-split"),
            ("POST", f"/api/projects/{pid}/custom-prompts"),
            ("POST", f"/api/projects/{pid}/generate-questions"),
            ("POST", f"/api/projects/{pid}/datasets"),
            ("POST", f"/api/projects/{pid}/datasets/batch-evaluate"),
            ("POST", f"/api/projects/{pid}/datasets/export"),
        ]
        for entry in expected:
            assert entry in recorded, f"missing API call: {entry}"


# ── 2c. Refine round 2: Case 1 — image VQA full workflow ────────────


class TestFullPipelineCase1:
    """Reproduces spec/03-case-studies.md §案例 1 (汽车图片识别 VQA) end-to-end:

    register vision model → import image directory → create three template
    types (text/label/json-schema) → questions generate --source image →
    datasets generate → export.
    """

    def test_case_1_image_vqa_workflow(self, stub_server, tmp_path):
        base_url, state = stub_server
        env = {
            "EDS_BASE_URL": base_url,
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
        }

        def run(*args, expect_json=True):
            r = _run(["--json", *args], env_extra=env, check=False)
            assert r.returncode == 0, f"command {args} failed: {r.stderr}"
            if expect_json and r.stdout.strip():
                try:
                    return json.loads(r.stdout)
                except json.JSONDecodeError:
                    return r.stdout
            return r.stdout

        # 1. Bootstrap project
        proj = run("project", "new", "--name", "car-vqa")
        pid = proj["id"]

        # 2. Register a VISION model (--type vision)
        mc = run(
            "model", "set",
            "--provider-id", "openai",
            "--endpoint", "https://api.openai.com/v1",
            "--api-key", "sk-test",
            "--model-id", "gpt-4o",
            "--type", "vision",
        )
        assert mc["id"].startswith("mc-")
        assert mc.get("type") == "vision"

        # 3. Build a local directory of car images and import it
        img_dir = tmp_path / "cars"
        img_dir.mkdir()
        (img_dir / "car1.png").write_bytes(b"\x89PNG\x0a")
        (img_dir / "car2.jpg").write_bytes(b"\xff\xd8\xff")
        imp = run("files", "import", "--type", "image", "--dir", str(img_dir))
        assert imp.get("imported_count") == 2
        assert len(state.images) == 2

        # 4. List images via the new sub-command
        listed = run("files", "list-images")
        assert isinstance(listed, list)
        assert len(listed) == 2

        # 5. Create three templates: text, label, json-schema (案例 1)
        t_text = run(
            "questions", "template", "create",
            "--question", "请描述这张图中的车辆。",
            "--source-type", "image",
            "--type", "text",
        )
        assert t_text["id"].startswith("tpl-")

        t_label = run(
            "questions", "template", "create",
            "--question", "这辆车是什么品牌？",
            "--source-type", "image",
            "--type", "label",
            "--label-set", "宝马,奔驰,奥迪,丰田,其他",
        )
        assert t_label.get("answerType") == "label"
        assert t_label.get("labels") == ["宝马", "奔驰", "奥迪", "丰田", "其他"]

        # JSON-schema template using a local schema file
        schema = tmp_path / "car-schema.json"
        schema.write_text(
            '{"type":"object","properties":{"brand":{"type":"string"},"color":{"type":"string"}},"required":["brand"]}',
            encoding="utf-8",
        )
        t_json = run(
            "questions", "template", "create",
            "--question", "提取车辆结构化信息",
            "--source-type", "image",
            "--type", "json-schema",
            "--schema-file", str(schema),
            "--auto-generate",
        )
        assert t_json.get("answerType") == "custom_format"
        assert "brand" in (t_json.get("customFormat") or "")

        # 6. List templates with sourceType filter
        templates = run("questions", "template", "list", "--source-type", "image")
        assert isinstance(templates, list)
        assert len(templates) == 3

        # autoGenerate=True on the JSON template should have produced 2 questions
        # (one per image). Check via the questions list.
        q_after_template = run("questions", "list")
        assert len(q_after_template) >= 2

        # 7. Generate VQA questions explicitly via --source image
        #    (auto-resolves to the vision model since it's the only one)
        run("questions", "generate", "--source", "image")
        # Stub generates one VQA question per image
        all_qs = run("questions", "list")
        assert len(all_qs) >= 4  # 2 from template + 2 VQA

        # 8. Generate answers
        ds = run("datasets", "generate")
        assert isinstance(ds, list)
        assert len(ds) >= 4

        # 9. Export to alpaca
        out = tmp_path / "vqa.json"
        export_result = run(
            "export", "run",
            "-o", str(out),
            "--format", "alpaca",
            "--all",
            "--overwrite",
        )
        assert export_result["count"] >= 4
        assert os.path.exists(out)

        # 10. Prune one image to verify the prune command
        first_img_id = state.images[0]["id"]
        run("files", "prune", "--id", first_img_id)
        assert len(state.images) == 1

        # 11. Spot-check recorded API calls
        recorded = [(r["method"], urlparse(r["path"]).path) for r in state.requests]
        assert ("POST", f"/api/projects/{pid}/model-config") in recorded
        assert ("POST", f"/api/projects/{pid}/images/zip-import") in recorded
        assert ("POST", f"/api/projects/{pid}/questions/templates") in recorded
        assert ("POST", f"/api/projects/{pid}/tasks") in recorded
        assert ("POST", f"/api/projects/{pid}/datasets") in recorded
        assert ("POST", f"/api/projects/{pid}/datasets/export") in recorded
        assert ("DELETE", f"/api/projects/{pid}/images") in recorded

        # Verify the model config posted with type=vision
        post_mc = next(
            r for r in state.requests
            if r["method"] == "POST" and r["path"].endswith("/model-config")
        )
        assert post_mc["body"]["type"] == "vision"

        # Verify a /tasks call kicked off image-question-generation
        task_calls = [
            r for r in state.requests
            if r["method"] == "POST" and r["path"].endswith("/tasks")
        ]
        assert any(
            c["body"].get("taskType") == "image-question-generation"
            for c in task_calls
        )

        print(
            f"\n  Case 1 VQA export: {out} ({os.path.getsize(out):,} bytes, "
            f"{export_result['count']} VQA records)"
        )


# ── 2d. Refine round 2: Case 3 — multi-turn distillation full workflow ──


class TestFullPipelineCase3:
    """Reproduces spec/03-case-studies.md §案例 3 (爱因斯坦讲相对论 多轮对话蒸馏)
    end-to-end: distill auto on a label tree → multi-turn dialogue dataset
    generation → ShareGPT-only export."""

    def test_case_3_multi_turn_distill_workflow(self, stub_server, tmp_path):
        base_url, state = stub_server
        env = {
            "EDS_BASE_URL": base_url,
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
        }

        def run(*args, expect_json=True):
            r = _run(["--json", *args], env_extra=env, check=False)
            assert r.returncode == 0, f"command {args} failed: {r.stderr}"
            if expect_json and r.stdout.strip():
                try:
                    return json.loads(r.stdout)
                except json.JSONDecodeError:
                    return r.stdout
            return r.stdout

        # 1. Bootstrap project + a regular text model
        proj = run("project", "new", "--name", "physics-tutor")
        pid = proj["id"]
        mc = run(
            "model", "set",
            "--provider-id", "openai",
            "--endpoint", "https://api.openai.com/v1",
            "--api-key", "sk-test",
            "--model-id", "gpt-4o-mini",
            "--type", "text",
        )
        assert mc["id"].startswith("mc-")

        # 2. Save a system prompt for the Einstein persona
        sys_prompt = tmp_path / "einstein.md"
        sys_prompt.write_text(
            "你是阿尔伯特·爱因斯坦，正在用初中生能理解的语言讲解相对论。\n"
            "请用通俗的比喻和友好的语气解释。\n"
            "学生提问者: {{student}}",
            encoding="utf-8",
        )
        run(
            "prompts", "set",
            "--type", "answer",
            "--key", "EINSTEIN_PERSONA",
            "--language", "zh-CN",
            "--file", str(sys_prompt),
            "--require-var", "student",
        )

        # 3. Write a label tree (YAML or JSON — JSON works without PyYAML)
        tree_file = tmp_path / "physics-tree.json"
        tree_file.write_text(
            json.dumps({
                "name": "物理学",
                "children": [
                    {"name": "经典力学", "children": [
                        {"name": "牛顿定律"},
                        {"name": "动量守恒"},
                    ]},
                    {"name": "相对论", "children": [
                        {"name": "狭义相对论"},
                        {"name": "广义相对论"},
                    ]},
                ],
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        # 4. Run distill auto with --type single first to verify the tree walk
        summary = run(
            "distill", "auto",
            "--label-tree-file", str(tree_file),
            "--questions-per-leaf", "2",
            "--language", "zh",
        )
        # 4 leaves: 牛顿定律, 动量守恒, 狭义相对论, 广义相对论
        assert summary["leaves_processed"] == 4
        assert summary["questions_called"] == 4
        # Stub returns 2 questions per leaf
        assert len(state.questions) == 8

        # 5. distill step tags / questions sub-commands (debug mode)
        step_tags = run(
            "distill", "step", "tags",
            "--parent-tag", "量子力学",
            "--count", "3",
        )
        assert "tags" in step_tags
        assert len(step_tags["tags"]) == 3

        step_qs = run(
            "distill", "step", "questions",
            "--current-tag", "薛定谔方程",
            "--count", "2",
        )
        assert "questions" in step_qs

        # 6. Generate multi-turn dialogue datasets for the first two distilled
        #    questions via the extended `datasets generate --rounds` flag
        first_two_qids = [q["id"] for q in state.questions[:2]]
        multi = run(
            "datasets", "generate",
            "--question", first_two_qids[0],
            "--question", first_two_qids[1],
            "--rounds", "4",
            "--role-a", "学生",
            "--role-b", "爱因斯坦",
            "--system-prompt-file", str(sys_prompt),
            "--scenario", "中学物理课",
            "--language", "中文",
        )
        assert isinstance(multi, list)
        assert len(multi) == 2
        for conv in multi:
            assert conv["rounds"] == 4
            assert conv["roleA"] == "学生"
            assert conv["roleB"] == "爱因斯坦"
            assert len(conv["messages"]) == 8  # 4 rounds * 2 sides

        # 7. List the conversations via conversations-list
        listed = run("datasets", "conversations-list")
        assert isinstance(listed, list)
        assert len(listed) == 2

        # 8. Export to ShareGPT (the only allowed format for multi-turn)
        out = tmp_path / "physics-multi-turn.json"
        export_result = run(
            "export", "conversations",
            "-o", str(out),
            "--format", "sharegpt",
            "--overwrite",
        )
        assert export_result["format"] == "sharegpt"
        assert export_result["kind"] == "multi-turn"
        assert os.path.exists(out)
        records = json.loads(out.read_text(encoding="utf-8"))
        assert isinstance(records, list)
        assert len(records) == 2
        for rec in records:
            # Server returns ShareGPT-shaped {messages:[...]} on the GET endpoint
            assert "messages" in rec

        # 9. Verify multi-turn export REJECTS alpaca (CLI Choice should reject
        #    non-sharegpt formats — confirm via subprocess return code)
        out_bad = tmp_path / "should-not-exist.json"
        bad = _run(
            ["--json", "export", "conversations",
             "-o", str(out_bad), "--format", "alpaca", "--overwrite"],
            env_extra=env, check=False,
        )
        assert bad.returncode != 0
        assert not out_bad.exists()

        # 10. Spot-check recorded API call sequence
        recorded = [(r["method"], urlparse(r["path"]).path) for r in state.requests]
        assert ("POST", f"/api/projects/{pid}/distill/questions") in recorded
        assert ("POST", f"/api/projects/{pid}/distill/tags") in recorded
        assert ("POST", f"/api/projects/{pid}/dataset-conversations") in recorded
        assert ("GET", f"/api/projects/{pid}/dataset-conversations/export") in recorded

        # Confirm the multi-turn POST body had rounds + roles
        mt_calls = [
            r for r in state.requests
            if r["method"] == "POST" and r["path"].endswith("/dataset-conversations")
        ]
        assert all(c["body"]["rounds"] == 4 for c in mt_calls)
        assert all(c["body"]["roleA"] == "学生" for c in mt_calls)

        print(
            f"\n  Case 3 multi-turn export: {out} "
            f"({os.path.getsize(out):,} bytes, {len(records)} dialogues, "
            f"{state.distill_question_calls.__len__()} distill/questions calls)"
        )


# ── 2e. Refine round 3: eval + blind-test full workflow ─────────────


class TestFullPipelineEvalBlindTest:
    """Reproduces the J1-J5 pipeline end-to-end through the installed CLI:

    create benchmark rows (single + multiple choice + short_answer) →
    sample → export to jsonl → run an eval-task with judge model →
    create a blind-test pitting model A vs B → drive the vote loop with
    auto-vote → confirm scores. Every CLI call is a subprocess invocation.
    """

    def test_eval_and_blind_workflow(self, stub_server, tmp_path):
        base_url, state = stub_server
        env = {
            "EDS_BASE_URL": base_url,
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
        }

        def run(*args):
            r = _run(["--json", *args], env_extra=env, check=False)
            assert r.returncode == 0, f"command {args} failed: {r.stderr}"
            if r.stdout.strip():
                try:
                    return json.loads(r.stdout)
                except json.JSONDecodeError:
                    return r.stdout
            return r.stdout

        # 1. Project + model bootstrap
        proj = run("project", "new", "--name", "eval-suite-demo")
        pid = proj["id"]
        run(
            "model", "set",
            "--provider-id", "openai",
            "--endpoint", "https://api.openai.com/v1",
            "--api-key", "sk-test",
            "--model-id", "gpt-4o-mini",
        )

        # 2. Create three benchmark rows: single_choice, multiple_choice, short_answer
        sc = run(
            "eval", "create",
            "--question", "Which planet is closest to the sun?",
            "--type", "single_choice",
            "--option", "Mercury", "--option", "Venus",
            "--option", "Earth", "--option", "Mars",
            "--correct", "[0]",
            "--tag", "astronomy",
        )
        assert sc["id"].startswith("e-")

        mc = run(
            "eval", "create",
            "--question", "Pick the rocky planets.",
            "--type", "multiple_choice",
            "--option", "Mercury", "--option", "Jupiter",
            "--option", "Earth", "--option", "Saturn",
            "--correct", "[0,2]",
            "--tag", "astronomy",
        )
        assert mc["id"].startswith("e-")

        sa = run(
            "eval", "create",
            "--question", "What is entropy in one sentence?",
            "--type", "short_answer",
            "--correct", "A measure of disorder in a system.",
        )
        assert sa["id"].startswith("e-")

        # 3. Count + list with type filter
        count_result = run("eval", "count")
        assert count_result["data"]["total"] == 3

        # 4. Sample → returns ids → feeds eval-task
        sample = run("eval", "sample", "--limit", "5")
        sample_ids = sample["data"]["ids"]
        assert len(sample_ids) == 3

        # 5. Server-side benchmark export to JSONL
        bench_out = tmp_path / "bench.jsonl"
        bench_export = run(
            "eval", "export",
            "-o", str(bench_out),
            "--format", "jsonl",
        )
        assert bench_export["format"] == "jsonl"
        assert os.path.exists(bench_out)

        # 6. Kick off an eval-task with two test models + a judge
        et = run(
            "eval-task", "run",
            "--model", "gpt-4o-mini:openai",
            "--model", "claude-haiku:anthropic",
            "--eval-id", sample_ids[0],
            "--eval-id", sample_ids[1],
            "--eval-id", sample_ids[2],
            "--judge-model", "gpt-4o:openai",
            "--language", "en",
        )
        assert et["code"] == 0
        assert len(et["data"]) == 2  # one task per test model
        first_task_id = et["data"][0]["id"]

        # 7. Inspect task results
        task_detail = run("eval-task", "get", first_task_id)
        assert task_detail["data"]["task"]["id"] == first_task_id
        assert len(task_detail["data"]["results"]) == 3

        # 8. Interrupt + delete to exercise the management commands
        run("eval-task", "interrupt", first_task_id)
        # Don't actually delete — we want it visible in `list`
        listed_tasks = run("eval-task", "list")
        assert listed_tasks["data"]["total"] >= 2

        # 9. Create a blind-test pitting two models against each other
        bt = run(
            "blind", "run",
            "--model-a", "gpt-4o:openai",
            "--model-b", "claude-opus:anthropic",
            "--eval-id", sample_ids[0],
            "--eval-id", sample_ids[1],
            "--eval-id", sample_ids[2],
            "--language", "en",
        )
        bt_id = bt["data"]["id"]
        assert bt_id.startswith("bt-")

        # 10. Drive the vote loop with the longer-answer auto judge.
        # The stub puts a longer answer for model B, so model B should always win.
        auto = run("blind", "auto-vote", bt_id, "--judge-rule", "longer")
        assert auto["votes_cast"] == 3
        assert auto["final_scores"]["modelB"] >= 2  # at least 2/3 to model B

        # 11. Get final task detail and confirm scores were recorded
        bt_detail = run("blind", "get", bt_id)
        assert bt_detail["data"]["scores"]["modelB"] >= 2

        # 12. Spot-check the recorded API call sequence
        recorded = [(r["method"], urlparse(r["path"]).path) for r in state.requests]
        assert ("POST", f"/api/projects/{pid}/eval-datasets") in recorded
        assert ("POST", f"/api/projects/{pid}/eval-datasets/sample") in recorded
        assert ("POST", f"/api/projects/{pid}/eval-datasets/export") in recorded
        assert ("POST", f"/api/projects/{pid}/eval-tasks") in recorded
        assert ("POST", f"/api/projects/{pid}/blind-test-tasks") in recorded
        assert ("POST", f"/api/projects/{pid}/blind-test-tasks/{bt_id}/vote") in recorded

        # Confirm at least one eval-dataset row was JSON-encoded properly
        single_choice_create = next(
            r for r in state.requests
            if r["method"] == "POST"
            and r["path"].endswith("/eval-datasets")
            and r["body"].get("questionType") == "single_choice"
        )
        assert json.loads(single_choice_create["body"]["correctAnswer"]) == [0]
        assert json.loads(single_choice_create["body"]["options"]) == [
            "Mercury", "Venus", "Earth", "Mars"
        ]

        print(
            f"\n  Eval+Blind workflow: {len(state.eval_datasets)} benchmark rows, "
            f"{len(state.eval_tasks)} eval tasks, {len(state.blind_tasks)} blind tasks, "
            f"{len(state.blind_votes)} votes cast"
        )


# ── 2f. Refine round 3: GA / MGA expansion workflow + export extensions ──


class TestFullPipelineGA:
    """Reproduces the I1-I5 pipeline end-to-end:

    upload → batch GA generate → list pairs → toggle one off → manually
    add a pair → estimate token inflation → run questions/datasets the
    normal way → export with --field-map, --include-chunk, --split.
    Validates that every export client-side option lands in the right file.
    """

    def test_ga_workflow_and_export_extensions(self, stub_server, tmp_path):
        base_url, state = stub_server
        env = {
            "EDS_BASE_URL": base_url,
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
        }

        def run(*args):
            r = _run(["--json", *args], env_extra=env, check=False)
            assert r.returncode == 0, f"command {args} failed: {r.stderr}"
            if r.stdout.strip():
                try:
                    return json.loads(r.stdout)
                except json.JSONDecodeError:
                    return r.stdout
            return r.stdout

        # 1. Bootstrap project + model
        proj = run("project", "new", "--name", "ga-demo")
        pid = proj["id"]
        run(
            "model", "set",
            "--provider-id", "openai",
            "--endpoint", "https://api.openai.com/v1",
            "--api-key", "sk-test",
            "--model-id", "gpt-4o-mini",
        )

        # 2. Upload a source document
        spec = tmp_path / "spec.md"
        spec.write_text("# Topic\n\nSome content.\n", encoding="utf-8")
        upload = run("files", "upload", str(spec))
        file_id = upload["id"]

        # 3. Estimate cost BEFORE running batch GA
        estimate = run(
            "ga", "estimate",
            "--files", "1",
            "--questions", "20",
        )
        assert estimate["pairs_per_file"] == 5
        assert estimate["estimated_max_questions"] == 100
        assert estimate["estimated_token_inflation"] == 3.9

        # 4. Generate GA pairs in batch (overwrite mode by default)
        gen = run(
            "ga", "generate",
            "--file", file_id,
            "--language", "中文",
        )
        assert gen["summary"]["success"] == 5
        assert len(state.ga_pairs[file_id]) == 5

        # 5. List the pairs
        pairs = run("ga", "list", file_id)
        assert isinstance(pairs, list)
        assert len(pairs) == 5
        first_pair_id = pairs[0]["id"]

        # 6. Toggle the first pair off
        toggled = run(
            "ga", "set-active",
            "--file", file_id,
            "--id", first_pair_id,
            "--inactive",
        )
        assert toggled["data"]["isActive"] is False

        # 7. Add a manual GA pair (--append is default)
        manual = run(
            "ga", "add-manual",
            "--file", file_id,
            "--genre-title", "Tutorial",
            "--audience-title", "Beginner",
            "--genre-desc", "step-by-step",
            "--audience-desc", "no prior background",
        )
        assert len(state.ga_pairs[file_id]) == 6  # 5 generated + 1 manual

        # 8. Run the standard chunk → questions → datasets pipeline
        run("chunks", "split", "--file", "spec.md")  # match the upload above
        run("questions", "generate")
        run("datasets", "generate")

        # 9. Test the new export flags: --file-type jsonl + --field-map
        out_jsonl = tmp_path / "renamed.jsonl"
        export_jsonl = run(
            "export", "run",
            "-o", str(out_jsonl),
            "--format", "alpaca",
            "--file-type", "jsonl",
            "--field-map", "instruction=prompt",
            "--field-map", "output=response",
            "--all",
            "--overwrite",
        )
        assert export_jsonl["file_type"] == "jsonl"
        # Validate the file is JSONL with renamed keys
        text = out_jsonl.read_text(encoding="utf-8").strip().split("\n")
        assert len(text) >= 1
        first = json.loads(text[0])
        assert "prompt" in first
        assert "response" in first
        assert "instruction" not in first

        # 10. Test --split with deterministic train/valid/test buckets
        out_split = tmp_path / "split.json"
        export_split = run(
            "export", "run",
            "-o", str(out_split),
            "--format", "alpaca",
            "--split", "0.7,0.15,0.15",
            "--all",
            "--overwrite",
        )
        assert "splits" in export_split
        for name in ("train", "valid", "test"):
            assert os.path.exists(export_split["splits"][name]["output"])
        total = sum(export_split["splits"][n]["count"] for n in ("train", "valid", "test"))
        assert total == export_split["count"]

        # 11. Test --file-type csv
        out_csv = tmp_path / "out.csv"
        export_csv = run(
            "export", "run",
            "-o", str(out_csv),
            "--format", "alpaca",
            "--file-type", "csv",
            "--all",
            "--overwrite",
        )
        assert export_csv["file_type"] == "csv"
        csv_text = out_csv.read_text(encoding="utf-8")
        assert "instruction" in csv_text  # header row present

        # 12. Spot-check the recorded API call sequence
        recorded = [(r["method"], urlparse(r["path"]).path) for r in state.requests]
        assert ("POST", f"/api/projects/{pid}/batch-generateGA") in recorded
        assert ("POST", f"/api/projects/{pid}/batch-add-manual-ga") in recorded
        assert ("GET", f"/api/projects/{pid}/files/{file_id}/ga-pairs") in recorded
        assert ("PATCH", f"/api/projects/{pid}/files/{file_id}/ga-pairs") in recorded

        # Confirm batch-generateGA body had correct shape
        bg = next(
            r for r in state.requests
            if r["method"] == "POST" and r["path"].endswith("/batch-generateGA")
        )
        assert bg["body"]["fileIds"] == [file_id]
        assert bg["body"]["language"] == "中文"
        assert bg["body"]["appendMode"] is False

        print(
            f"\n  GA workflow: {len(state.ga_pairs[file_id])} pairs (5 generated + 1 manual), "
            f"export written as jsonl ({out_jsonl.stat().st_size} bytes), "
            f"split into 3 files ({total} records total)"
        )


# ── 2g. Refine round 4: tags + tasks editing workflow ──────────────


class TestFullPipelineTagsEdit:
    """Reproduces E2-E4 + N1-N3: manually edit a project's domain tree, list
    questions by tag, and exercise the background task system commands."""

    def test_tags_and_tasks_workflow(self, stub_server, tmp_path):
        base_url, state = stub_server
        env = {
            "EDS_BASE_URL": base_url,
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
        }

        def run(*args):
            r = _run(["--json", *args], env_extra=env, check=False)
            assert r.returncode == 0, f"command {args} failed: {r.stderr}"
            if r.stdout.strip():
                try:
                    return json.loads(r.stdout)
                except json.JSONDecodeError:
                    return r.stdout
            return r.stdout

        proj = run("project", "new", "--name", "tags-demo")
        pid = proj["id"]

        # 1. Create a 3-level tag tree manually
        physics = run("tags", "create", "--label", "物理学")
        physics_id = physics["tags"]["id"]

        mech = run("tags", "create", "--label", "经典力学", "--parent", physics_id)
        mech_id = mech["tags"]["id"]

        run("tags", "create", "--label", "牛顿定律", "--parent", mech_id)
        run("tags", "create", "--label", "动量守恒", "--parent", mech_id)

        em = run("tags", "create", "--label", "电磁学", "--parent", physics_id)
        em_id = em["tags"]["id"]

        # 2. List the tree (nested + flat)
        tree = run("tags", "list")
        assert isinstance(tree, list)
        assert any(t.get("label") == "物理学" for t in tree)

        flat = run("tags", "list", "--flat")
        assert "物理学" in flat
        assert "牛顿定律" in flat

        # 3. Rename one tag
        renamed = run("tags", "rename", em_id, "--label", "电动力学")
        assert renamed["tags"]["label"] == "电动力学"

        # 4. Move 电动力学 from physics root → under mech
        moved = run("tags", "move", em_id, "--parent", mech_id)
        assert moved["tags"]["parentId"] == mech_id

        # 5. Lookup questions by tag name (returns empty since we have none)
        by_tag = run("tags", "questions", "牛顿定律")
        assert "questions" in by_tag

        # 6. Delete one tag (cascades through subtree)
        run("tags", "delete", em_id)
        # The tree should still contain physics + mech
        tree_after = run("tags", "list", "--flat")
        assert "电动力学" not in tree_after
        assert "物理学" in tree_after

        # 7. Background task system: create a fake task by direct stub call,
        #    then list / get / cancel via the easyds CLI.
        import requests as _r
        _r.post(
            f"{base_url}/api/projects/{pid}/tasks",
            json={
                "taskType": "answer-generation",
                "totalCount": 100,
                "modelInfo": "{}",
                "language": "zh-CN",
                "note": "test task",
            },
        )
        # Manually push a second task with status=1 (completed) for the wait test.
        _r.post(
            f"{base_url}/api/projects/{pid}/tasks",
            json={
                "taskType": "data-cleaning",
                "totalCount": 50,
                "note": "completed test task",
            },
        )
        state.bg_tasks[1]["status"] = 1  # mark second task as completed

        # 8. List with type filter
        listed = run("task", "list", "--type", "answer-generation")
        assert listed["data"][0]["taskType"] == "answer-generation"

        # 9. Get one task
        first_id = state.bg_tasks[0]["id"]
        detail = run("task", "get", first_id)
        assert detail["data"]["taskType"] == "answer-generation"

        # 10. Cancel the running task
        cancelled = run("task", "cancel", first_id)
        assert cancelled["data"]["status"] == 3  # interrupted

        # 11. Wait for the already-completed task (should return immediately)
        second_id = state.bg_tasks[1]["id"]
        waited = run("task", "wait", second_id, "--poll-interval", "0.1", "--timeout", "5")
        assert waited["status"] == 1  # completed

        # 12. Spot-check the API call sequence
        recorded = [(r["method"], urlparse(r["path"]).path) for r in state.requests]
        assert ("PUT", f"/api/projects/{pid}/tags") in recorded  # save_tag
        assert ("DELETE", f"/api/projects/{pid}/tags") in recorded  # delete_tag
        assert ("POST", f"/api/projects/{pid}/tags") in recorded  # questions by tag
        assert ("GET", f"/api/projects/{pid}/tasks/list") in recorded
        assert ("PATCH", f"/api/projects/{pid}/tasks/{first_id}") in recorded

        print(
            f"\n  Tags+Tasks workflow: {len([n for n in _walk_tags(state.tag_tree)])} "
            f"tags after edits, {len(state.bg_tasks)} background tasks"
        )


# ── 2h. Refine round 4: import → clean → optimize workflow ──────────


class TestFullPipelineImportCleanOptimize:
    """Reproduces M1-M2 + D6 + G4: import seed datasets from JSONL with field
    mapping, run chunks clean against a noisy chunk, then optimize one
    dataset's answer with user advice."""

    def test_import_clean_optimize_workflow(self, stub_server, tmp_path):
        base_url, state = stub_server
        env = {
            "EDS_BASE_URL": base_url,
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
        }

        def run(*args):
            r = _run(["--json", *args], env_extra=env, check=False)
            assert r.returncode == 0, f"command {args} failed: {r.stderr}"
            if r.stdout.strip():
                try:
                    return json.loads(r.stdout)
                except json.JSONDecodeError:
                    return r.stdout
            return r.stdout

        # 1. Project + model bootstrap
        proj = run("project", "new", "--name", "import-clean-demo")
        pid = proj["id"]
        run(
            "model", "set",
            "--provider-id", "openai",
            "--endpoint", "https://api.openai.com/v1",
            "--api-key", "sk-test",
            "--model-id", "gpt-4o-mini",
        )

        # 2. Build a JSONL seed file using DIFFERENT column names than the
        #    server expects, then import with --mapping. Two valid rows + one
        #    invalid row that should be filtered client-side.
        seed = tmp_path / "seed.jsonl"
        seed.write_text(
            '{"instruction": "What is gravity?", "output": "A force that attracts mass."}\n'
            '{"instruction": "Define entropy.", "output": "Disorder in a system."}\n'
            '{"instruction": "Bad row with no output"}\n',
            encoding="utf-8",
        )
        imported = run(
            "datasets", "import",
            str(seed),
            "--mapping", "instruction=question",
            "--mapping", "output=answer",
        )
        assert imported["success"] == 2
        assert len(state.datasets) == 2

        # 3. Test the JSON variant with default field names + a CSV file
        json_seed = tmp_path / "seed.json"
        json_seed.write_text(
            json.dumps([
                {"question": "What is mass?", "answer": "A measure of matter."},
                {"question": "What is energy?", "answer": "Capacity to do work."},
            ]),
            encoding="utf-8",
        )
        run("datasets", "import", str(json_seed))
        assert len(state.datasets) == 4

        csv_seed = tmp_path / "seed.csv"
        csv_seed.write_text(
            "question,answer\n"
            "What is force?,Mass times acceleration.\n",
            encoding="utf-8",
        )
        run("datasets", "import", str(csv_seed))
        assert len(state.datasets) == 5

        # 4. Set up a chunk to clean (we need a chunk in the project — run
        #    the standard upload + split first)
        spec = tmp_path / "spec.md"
        spec.write_text("# Spec\n\nNoisy text with [1] [2] HTML <b>tags</b>.\n", encoding="utf-8")
        run("files", "upload", str(spec))
        run("chunks", "split", "--file", "spec.md")
        assert len(state.chunks) >= 1
        first_chunk_id = state.chunks[0]["id"]

        # 5. Set a custom dataClean prompt + run chunks clean (D6)
        clean_prompt = tmp_path / "clean.md"
        clean_prompt.write_text(
            "Clean the following text ({{textLength}} chars):\n\n{{text}}\n\n"
            "Remove [N] citations and HTML tags.",
            encoding="utf-8",
        )
        cleaned = run(
            "chunks", "clean", first_chunk_id,
            "--prompt-file", str(clean_prompt),
            "--language", "中文",
        )
        assert cleaned["success"] is True
        assert cleaned["chunkId"] == first_chunk_id
        # Confirm the stub recorded the clean call
        assert len(state.chunk_clean_calls) == 1
        assert state.chunk_clean_calls[0]["chunkId"] == first_chunk_id

        # 6. Test chunks edit (PATCH) — overwrite the chunk content directly
        edited = run(
            "chunks", "edit", first_chunk_id,
            "--content", "Manually edited content.",
        )
        assert edited["content"] == "Manually edited content."

        # 7. Test chunks batch-edit — append a header to all chunks
        if len(state.chunks) >= 1:
            batch_result = run(
                "chunks", "batch-edit",
                "--chunk", first_chunk_id,
                "--position", "start",
                "--content", "## Header",
            )
            assert batch_result["updatedCount"] == 1

        # 8. Optimize one of the imported datasets with advice (魔法棒 / G4)
        first_dataset_id = state.datasets[0]["id"]
        optimized = run(
            "datasets", "optimize", first_dataset_id,
            "--advice", "make the answer more concise and add an example",
            "--language", "en",
        )
        assert optimized["success"] is True
        assert "[optimized:" in state.datasets[0]["answer"]
        assert len(state.optimize_calls) == 1
        assert state.optimize_calls[0]["advice"] == "make the answer more concise and add an example"

        # 9. Questions filtering — create a manual question and search
        run("chunks", "split", "--file", "spec.md")  # idempotent in stub
        chunk_id_for_question = state.chunks[0]["id"]
        manual_q = run(
            "questions", "create",
            "--question", "What is the meaning of life?",
            "--chunk", chunk_id_for_question,
            "--label", "philosophy",
        )
        assert manual_q["question"] == "What is the meaning of life?"

        # Edit the question via the CLI's GET-then-PUT helper
        edited_q = run(
            "questions", "edit", manual_q["id"],
            "--question", "What is the ultimate question?",
            "--label", "philosophy/ultimate",
        )
        assert edited_q["question"] == "What is the ultimate question?"

        # List with status filter
        listed = run("questions", "list", "--status", "unanswered", "--all")
        # The result is the raw dict-or-list shape
        if isinstance(listed, dict) and "items" in listed:
            assert any(q.get("question") == "What is the ultimate question?" for q in listed["items"])
        else:
            assert any(q.get("question") == "What is the ultimate question?" for q in listed)

        # Delete the manual question
        run("questions", "delete", manual_q["id"])

        # 10. Spot-check the API call sequence
        recorded = [(r["method"], urlparse(r["path"]).path) for r in state.requests]
        assert ("POST", f"/api/projects/{pid}/datasets/import") in recorded
        assert ("POST", f"/api/projects/{pid}/chunks/{first_chunk_id}/clean") in recorded
        assert ("PATCH", f"/api/projects/{pid}/chunks/{first_chunk_id}") in recorded
        assert ("POST", f"/api/projects/{pid}/chunks/batch-edit") in recorded
        assert ("POST", f"/api/projects/{pid}/datasets/optimize") in recorded
        assert ("PUT", f"/api/projects/{pid}/questions") in recorded
        assert ("DELETE", f"/api/projects/{pid}/questions/{manual_q['id']}") in recorded

        print(
            f"\n  Import+Clean+Optimize workflow: imported {len(state.datasets)} datasets, "
            f"cleaned 1 chunk, optimized 1 answer, "
            f"{len(state.chunk_clean_calls)} clean calls, "
            f"{len(state.optimize_calls)} optimize calls"
        )


# ── 2d. Dataset-eval feedback loop (subprocess, offline) ──────────────


class TestDatasetEvalFeedbackLoop:
    """End-to-end subprocess exercise of the 'datasets eval' feedback loop.

    Simulates the full agent narrative from spec §3.6:
      1. Copy the known-broken case-2 fixture into tmp.
      2. Run 'easyds datasets eval --json' — expect exit=2, fail verdict,
         input_empty_rate=1.0, output_double_encoded=1.0 with attribution
         pointing at 'export' + 'post-process'.
      3. Apply '--fix chunk-join' using a chunks file, verify it writes.
      4. Apply '--fix unwrap-labels'.
      5. Re-run eval — expect exit=0 (or 1 if only warn-level remains).
      6. Confirm eval-history now has entries for both runs.

    No server required — the feedback loop is entirely local.
    """

    def _prep(self, tmp_path):
        fixtures = Path(__file__).parent / "fixtures" / "eval"
        broken = fixtures / "case2-broken-sentiment.json"
        target = tmp_path / "sentiment-alpaca.json"
        target.write_text(broken.read_text(encoding="utf-8"), encoding="utf-8")
        # Matching chunks file
        chunks = [
            {"name": f"reviews-part-{i+1}", "content": f"test review {i+1}"}
            for i in range(8)
        ]
        chunks_file = tmp_path / "chunks.json"
        chunks_file.write_text(json.dumps(chunks, ensure_ascii=False))
        return target, chunks_file

    def test_full_loop_broken_to_fixed(self, tmp_path):
        target, chunks_file = self._prep(tmp_path)
        env = {
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
        }

        # 1. Initial eval — broken, exit=2
        r = _run(
            ["--json", "datasets", "eval", str(target)],
            env_extra=env, check=False,
        )
        assert r.returncode in (0, 2), (
            f"expected exit 0/2 in JSON mode, got {r.returncode}: {r.stderr}"
        )
        report = json.loads(r.stdout)
        assert report["verdict"] == "fail"
        assert report["task_type"] == "classification"
        assert report["exit_code"] == 2

        rules = {c["name"]: c for c in report["checks"]}
        assert rules["input_empty_rate"]["verdict"] == "fail"
        assert rules["input_empty_rate"]["value"] == 1.0
        assert rules["output_double_encoded"]["verdict"] == "fail"

        attr_rules = {a["rule"] for a in report["attribution"]}
        assert "input_empty_rate" in attr_rules
        assert "output_double_encoded" in attr_rules

        # 2. Apply --fix chunk-join (writes file in place, no server)
        r = _run([
            "--json", "datasets", "eval", str(target),
            "--fix", "chunk-join",
            "--chunks-file", str(chunks_file),
        ], env_extra=env, check=False)
        assert r.returncode == 0, r.stderr
        fix_summary = json.loads(r.stdout)
        assert fix_summary["fix"] == "chunk-join"
        assert fix_summary["updated"] == 8

        # Verify the file actually got rewritten
        rewritten = json.loads(target.read_text(encoding="utf-8"))
        assert all(r["input"] != "" for r in rewritten)
        assert rewritten[0]["input"] == "test review 1"

        # 3. Apply --fix unwrap-labels to collapse the '["正面"]' encoding
        r = _run([
            "--json", "datasets", "eval", str(target),
            "--fix", "unwrap-labels",
        ], env_extra=env, check=False)
        assert r.returncode == 0, r.stderr
        rewritten = json.loads(target.read_text(encoding="utf-8"))
        assert all(not r["output"].startswith("[") for r in rewritten)

        # 4. Re-run eval — both hard failures should be gone
        r = _run(
            ["--json", "datasets", "eval", str(target)],
            env_extra=env, check=False,
        )
        report2 = json.loads(r.stdout)
        hard_fails = [c for c in report2["checks"] if c["verdict"] == "fail"]
        assert hard_fails == [], (
            f"still failing after fixes: {hard_fails}"
        )
        assert report2["verdict"] in ("pass", "warn")

    def test_eval_history_persists_across_runs(self, tmp_path):
        target, _ = self._prep(tmp_path)
        # Seed a session with a project so history keys by project id
        session_dir = tmp_path / ".easyds"
        session_dir.mkdir()
        (session_dir / "session.json").write_text(
            json.dumps({"current_project_id": "proj-x"})
        )
        env = {
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
        }
        for _ in range(3):
            r = _run(
                ["--json", "datasets", "eval", str(target)],
                env_extra=env, check=False,
            )
            assert r.returncode in (0, 2)

        r = _run(
            ["--json", "datasets", "eval-history"],
            env_extra=env, check=False,
        )
        assert r.returncode == 0, r.stderr
        hist = json.loads(r.stdout)
        assert isinstance(hist, list)
        assert len(hist) == 3
        for entry in hist:
            assert entry["verdict"] == "fail"
            assert "input_empty_rate" in entry["failing_rules"]

    def test_no_history_flag_opts_out(self, tmp_path):
        target, _ = self._prep(tmp_path)
        session_dir = tmp_path / ".easyds"
        session_dir.mkdir()
        (session_dir / "session.json").write_text(
            json.dumps({"current_project_id": "proj-noh"})
        )
        env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}

        _run(
            ["--json", "datasets", "eval", str(target), "--no-history"],
            env_extra=env, check=False,
        )
        r = _run(
            ["--json", "datasets", "eval-history"],
            env_extra=env, check=False,
        )
        hist = json.loads(r.stdout)
        assert hist == []


# ── 3. Live backend (gated) ───────────────────────────────────────────


@pytest.mark.skipif(
    os.environ.get("EDS_LIVE_TESTS") != "1",
    reason="Live tests require EDS_LIVE_TESTS=1, a running Easy-Dataset server, "
           "and valid LLM API keys. See TEST.md Part 1.",
)
class TestLiveBackend:
    def test_real_server_status(self, tmp_path):
        env = {
            "EDS_BASE_URL": os.environ.get("EDS_BASE_URL", "http://localhost:1717"),
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
        }
        r = _run(["--json", "status"], env_extra=env, check=False)
        assert r.returncode == 0, r.stderr
        payload = json.loads(r.stdout)
        assert payload["server_status"] == "ok"
