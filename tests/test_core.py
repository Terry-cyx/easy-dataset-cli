"""Unit tests for easyds.core.* and utils.* — fully mocked."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest
import responses

from easyds.core import (
    blind_test as blind_mod,
    chunks as chunks_mod,
    datasets as datasets_mod,
    distill as distill_mod,
    eval as eval_mod,
    eval_tasks as eval_tasks_mod,
    export as export_mod,
    files as files_mod,
    ga as ga_mod,
    model as model_mod,
    project as project_mod,
    prompts as prompts_mod,
    questions as questions_mod,
    session as session_mod,
    tags as tags_mod,
    tasks as tasks_mod,
    templates as templates_mod,
)
from easyds.utils.backend import (
    BackendError,
    BackendUnavailable,
    EasyDatasetBackend,
    resolve_base_url,
)


BASE = "http://test.local:1717"

# Sample full model-config dict — every LLM-driven server endpoint requires
# the FULL config object as ``model``, not just the id (the GUI reads this
# from selectedModelInfoAtom in localStorage). Tests pass it directly to
# avoid an extra mocked GET /model-config call.
SAMPLE_MODEL = {
    "id": "mc1",
    "providerId": "openai",
    "providerName": "OpenAI",
    "endpoint": "https://api.openai.com/v1",
    "apiKey": "sk-test",
    "modelId": "gpt-4o-mini",
    "modelName": "gpt-4o-mini",
    "type": "text",
    "temperature": 0.7,
    "maxTokens": 4096,
    "topP": 0.9,
}


@pytest.fixture
def backend():
    return EasyDatasetBackend(base_url=BASE)


@pytest.fixture
def isolated_session(tmp_path, monkeypatch):
    """Redirect ~/.easyds/ into a tmp dir for each test."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # On Windows Path.home() honors USERPROFILE; on POSIX it honors HOME.
    yield tmp_path


# ── resolve_base_url ──────────────────────────────────────────────────


class TestResolveBaseUrl:
    def test_cli_arg_wins(self, monkeypatch):
        monkeypatch.setenv("EDS_BASE_URL", "http://from-env:9999")
        assert resolve_base_url("http://from-cli:1111") == "http://from-cli:1111"

    def test_env_used_when_no_cli(self, monkeypatch):
        monkeypatch.setenv("EDS_BASE_URL", "http://from-env:9999")
        assert resolve_base_url(None) == "http://from-env:9999"

    def test_default_when_neither(self, monkeypatch):
        monkeypatch.delenv("EDS_BASE_URL", raising=False)
        assert resolve_base_url(None) == "http://localhost:1717"

    def test_strips_trailing_slash(self):
        assert resolve_base_url("http://x/").endswith("x")


# ── EasyDatasetBackend ────────────────────────────────────────────────


class TestBackend:
    @responses.activate
    def test_check_health_ok(self, backend):
        responses.add(responses.GET, f"{BASE}/api/projects", json=[], status=200)
        h = backend.check_health()
        assert h["ok"] is True
        assert h["status_code"] == 200

    def test_check_health_unreachable_raises(self):
        # Bind a port and immediately close it; nothing listens there now.
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        be = EasyDatasetBackend(base_url=f"http://127.0.0.1:{port}", timeout=1.0)
        with pytest.raises(BackendUnavailable) as exc:
            be.check_health()
        assert "not reachable" in str(exc.value)
        assert "pnpm dev" in str(exc.value)

    @responses.activate
    def test_request_4xx_raises_backend_error(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/missing",
            json={"error": "not found"},
            status=404,
        )
        with pytest.raises(BackendError) as exc:
            backend.get("/api/projects/missing")
        assert "404" in str(exc.value)

    @responses.activate
    def test_post_multipart_uses_file_field(self, backend, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# hello\n")
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/files",
            json={"id": "file-1", "fileName": "doc.md"},
            status=200,
        )
        with open(f, "rb") as fh:
            r = backend.post_multipart(
                "/api/projects/p1/files",
                files={"file": ("doc.md", fh, "application/octet-stream")},
            )
        assert r["id"] == "file-1"
        assert "multipart/form-data" in responses.calls[0].request.headers["Content-Type"]


# ── session ───────────────────────────────────────────────────────────


class TestSession:
    def test_load_returns_empty_when_missing(self, isolated_session):
        assert session_mod.load_session() == {}

    def test_save_and_load_round_trip(self, isolated_session):
        session_mod.save_session({"current_project_id": "p1", "extra": [1, 2]})
        loaded = session_mod.load_session()
        assert loaded["current_project_id"] == "p1"
        assert loaded["extra"] == [1, 2]

    def test_set_current_project_preserves_other_keys(self, isolated_session):
        session_mod.save_session({"base_url": "http://x", "current_model_config_id": "m1"})
        session_mod.set_current_project("p2", project_name="demo")
        s = session_mod.load_session()
        assert s["base_url"] == "http://x"
        assert s["current_model_config_id"] == "m1"
        assert s["current_project_id"] == "p2"
        assert s["current_project_name"] == "demo"

    def test_resolve_project_id_cli_wins(self, isolated_session, monkeypatch):
        monkeypatch.setenv("EDS_PROJECT_ID", "from-env")
        session_mod.save_session({"current_project_id": "from-session"})
        assert session_mod.resolve_project_id("from-cli") == "from-cli"

    def test_resolve_project_id_env_then_session(self, isolated_session, monkeypatch):
        monkeypatch.setenv("EDS_PROJECT_ID", "from-env")
        assert session_mod.resolve_project_id(None) == "from-env"
        monkeypatch.delenv("EDS_PROJECT_ID")
        session_mod.save_session({"current_project_id": "from-session"})
        assert session_mod.resolve_project_id(None) == "from-session"

    def test_resolve_project_id_raises_when_unset(self, isolated_session, monkeypatch):
        monkeypatch.delenv("EDS_PROJECT_ID", raising=False)
        with pytest.raises(session_mod.NoProjectSelected):
            session_mod.resolve_project_id(None)

    def test_resolve_model_config_id_raises_when_unset(self, isolated_session, monkeypatch):
        monkeypatch.delenv("EDS_MODEL_CONFIG_ID", raising=False)
        with pytest.raises(session_mod.NoModelConfigSelected):
            session_mod.resolve_model_config_id(None)


# ── core/project ──────────────────────────────────────────────────────


class TestProject:
    @responses.activate
    def test_create(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects",
            json={"id": "p1", "name": "demo"},
            status=200,
        )
        r = project_mod.create(backend, name="demo", description="d")
        assert r["id"] == "p1"
        body = json.loads(responses.calls[0].request.body)
        assert body == {"name": "demo", "description": "d"}

    @responses.activate
    def test_list_unwraps_data_key(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects",
            json={"data": [{"id": "p1"}, {"id": "p2"}]},
            status=200,
        )
        r = project_mod.list_all(backend)
        assert [p["id"] for p in r] == ["p1", "p2"]

    @responses.activate
    def test_list_passthrough_for_array_response(self, backend):
        responses.add(
            responses.GET, f"{BASE}/api/projects", json=[{"id": "p1"}], status=200
        )
        assert project_mod.list_all(backend) == [{"id": "p1"}]

    @responses.activate
    def test_update_uses_put(self, backend):
        # Server defines GET/PUT/DELETE on /api/projects/{id}; PATCH 405s.
        responses.add(
            responses.PUT, f"{BASE}/api/projects/p1", json={"id": "p1"}, status=200
        )
        project_mod.update(backend, "p1", name="new")
        assert responses.calls[0].request.method == "PUT"

    @responses.activate
    def test_set_default_model(self, backend):
        # Required for GA generation: server's getActiveModel(projectId) reads
        # project.defaultModelConfigId from the DB. `model use` is local-only
        # without this PUT, which silently breaks /batch-generateGA.
        responses.add(
            responses.PUT,
            f"{BASE}/api/projects/p1",
            json={"id": "p1", "defaultModelConfigId": "mc1"},
            status=200,
        )
        project_mod.set_default_model(backend, "p1", "mc1")
        body = json.loads(responses.calls[0].request.body)
        assert body == {"defaultModelConfigId": "mc1"}

    @responses.activate
    def test_set_default_model_clear(self, backend):
        responses.add(
            responses.PUT, f"{BASE}/api/projects/p1", json={"id": "p1"}, status=200
        )
        project_mod.set_default_model(backend, "p1", None)
        body = json.loads(responses.calls[0].request.body)
        assert body == {"defaultModelConfigId": None}

    @responses.activate
    def test_delete(self, backend):
        responses.add(
            responses.DELETE, f"{BASE}/api/projects/p1", json={"deleted": True}, status=200
        )
        assert project_mod.delete(backend, "p1") == {"deleted": True}


# ── core/model ────────────────────────────────────────────────────────


class TestModel:
    @responses.activate
    def test_set_config_body_shape(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/model-config",
            json={"id": "mc1"},
            status=200,
        )
        r = model_mod.set_config(
            backend,
            "p1",
            provider_id="openai",
            provider_name="OpenAI",
            endpoint="https://api.openai.com/v1",
            api_key="sk-x",
            model_id="gpt-4o-mini",
        )
        assert r["id"] == "mc1"
        body = json.loads(responses.calls[0].request.body)
        assert body["providerId"] == "openai"
        assert body["endpoint"] == "https://api.openai.com/v1"
        assert body["apiKey"] == "sk-x"
        assert body["modelId"] == "gpt-4o-mini"
        assert body["modelName"] == "gpt-4o-mini"
        assert "temperature" in body and "maxTokens" in body

    @responses.activate
    def test_set_config_always_sends_topp(self, backend):
        """Regression: server's ModelConfig schema requires topP (Float, no
        default) and the route handler does NOT inject a default for it
        (only for topK and status). Caller should not have to remember
        --top-p or every model registration 500s. See
        easy-dataset/prisma/schema.prisma + lib/db/model-config.js."""
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/model-config",
            json={"id": "mc1"},
            status=200,
        )
        model_mod.set_config(
            backend,
            "p1",
            provider_id="openai",
            provider_name="OpenAI",
            endpoint="https://api.openai.com/v1",
            api_key="sk-x",
            model_id="gpt-4o-mini",
        )
        body = json.loads(responses.calls[0].request.body)
        assert "topP" in body, (
            "topP must always be sent — server schema requires it and the "
            "route handler does not default it"
        )
        assert isinstance(body["topP"], (int, float))
        assert 0.0 <= body["topP"] <= 1.0


# ── core/files ────────────────────────────────────────────────────────


class TestFiles:
    @responses.activate
    def test_upload_sends_raw_body_with_x_file_name(self, backend, tmp_path):
        """Regression: Easy-Dataset's /api/projects/{id}/files POST is NOT
        multipart. The route reads the filename from the ``x-file-name``
        header (URL-encoded) and the file bytes from
        ``request.arrayBuffer()``. See easy-dataset/app/api/projects/
        [projectId]/files/route.js lines 176-194. The previous multipart
        implementation was rejected by the server with HTTP 400."""
        path = tmp_path / "doc with space.md"
        body = b"# hi\nthis is the body\n"
        path.write_bytes(body)
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/files",
            json={"id": "f1", "fileName": "doc with space.md"},
            status=200,
        )
        r = files_mod.upload(backend, "p1", str(path))
        assert r["id"] == "f1"

        req = responses.calls[0].request
        # NOT multipart — raw bytes only
        ctype = req.headers.get("Content-Type", "")
        assert "multipart/form-data" not in ctype
        # x-file-name must be present and URL-encoded (spaces → %20)
        xfn = req.headers.get("x-file-name") or req.headers.get("X-File-Name")
        assert xfn is not None, "x-file-name header missing"
        assert "%20" in xfn or "+" in xfn, (
            f"filename must be URL-encoded; got {xfn!r}"
        )
        # Body must be the raw file bytes verbatim
        assert req.body == body

    def test_upload_missing_file_raises(self, backend):
        with pytest.raises(FileNotFoundError):
            files_mod.upload(backend, "p1", "/nonexistent/xyz.md")

    @responses.activate
    def test_delete_uses_query_param(self, backend):
        responses.add(
            responses.DELETE,
            f"{BASE}/api/projects/p1/files",
            json={"deleted": True},
            status=200,
        )
        files_mod.delete_file(backend, "p1", "f1")
        assert "fileId=f1" in responses.calls[0].request.url


# ── core/chunks ───────────────────────────────────────────────────────


class TestChunks:
    @responses.activate
    def test_split_body_shape(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/split",
            json={"chunks": []},
            status=200,
        )
        chunks_mod.split(
            backend, "p1",
            files=[
                {"fileName": "a.md", "fileId": "f-a"},
                {"fileName": "b.md", "fileId": "f-b"},
            ],
            model=SAMPLE_MODEL,
        )
        body = json.loads(responses.calls[0].request.body)
        assert body == {
            "fileNames": [
                {"fileName": "a.md", "fileId": "f-a"},
                {"fileName": "b.md", "fileId": "f-b"},
            ],
            "model": SAMPLE_MODEL,
            "domainTreeAction": "rebuild",
        }

    @responses.activate
    def test_list_unwraps_chunks_key(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/split",
            json={"chunks": [{"id": "c1"}]},
            status=200,
        )
        assert chunks_mod.list_chunks(backend, "p1") == [{"id": "c1"}]


# ── core/questions ────────────────────────────────────────────────────


class TestQuestions:
    @responses.activate
    def test_generate_body_shape(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/generate-questions",
            json={"count": 5},
            status=200,
        )
        questions_mod.generate(
            backend, "p1", ["c1", "c2"], model=SAMPLE_MODEL,
            enable_ga_expansion=True, language="zh",
        )
        body = json.loads(responses.calls[0].request.body)
        assert body == {
            "chunkIds": ["c1", "c2"],
            "model": SAMPLE_MODEL,
            "enableGaExpansion": True,
            "language": "zh",
            "sourceType": "chunk",
        }

    @responses.activate
    def test_list_defaults_to_all(self, backend):
        # Server bug workaround: prisma.questions.findMany() requires `take`,
        # so an unparameterized call 500s. The CLI must default to all=true.
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/questions",
            json=[],
            status=200,
        )
        questions_mod.list_questions(backend, "p1")
        assert "all=true" in responses.calls[0].request.url

    @responses.activate
    def test_list_with_page_does_not_inject_all(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/questions",
            json={"items": [], "total": 0},
            status=200,
        )
        questions_mod.list_questions(backend, "p1", page=1, size=20)
        url = responses.calls[0].request.url
        assert "all=true" not in url
        assert "page=1" in url and "size=20" in url


# ── core/datasets ─────────────────────────────────────────────────────


class TestDatasets:
    @responses.activate
    def test_generate_body_shape(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/datasets",
            json={"id": "d1"},
            status=200,
        )
        datasets_mod.generate(backend, "p1", "q1", model=SAMPLE_MODEL)
        body = json.loads(responses.calls[0].request.body)
        assert body == {"questionId": "q1", "model": SAMPLE_MODEL, "language": "en"}

    @responses.activate
    def test_update_uses_put(self, backend):
        responses.add(
            responses.PUT,
            f"{BASE}/api/projects/p1/datasets/d1",
            json={"id": "d1", "confirmed": True},
            status=200,
        )
        datasets_mod.update(backend, "p1", "d1", confirmed=True, score=5)
        assert responses.calls[0].request.method == "PUT"
        body = json.loads(responses.calls[0].request.body)
        assert body == {"confirmed": True, "score": 5}


# ── core/export ───────────────────────────────────────────────────────


class TestExport:
    def test_rejects_unknown_format(self, backend):
        with pytest.raises(ValueError):
            export_mod.run(backend, "p1", output_path="/tmp/x.json", fmt="bogus")

    def test_refuses_overwrite_without_flag(self, backend, tmp_path):
        out = tmp_path / "out.json"
        out.write_text("[]")
        with pytest.raises(FileExistsError):
            export_mod.run(backend, "p1", output_path=str(out), fmt="alpaca")

    @responses.activate
    def test_score_filter_uses_selected_ids(self, backend, tmp_path):
        # Step 1: list_datasets is called with scoreRange filter
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/datasets",
            json={
                "data": [
                    {"id": "d1", "score": 4.5, "question": "Q1?", "answer": "A1"},
                    {"id": "d3", "score": 5.0, "question": "Q3?", "answer": "A3"},
                ]
            },
            status=200,
        )
        # Step 2: export endpoint receives selectedIds
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/datasets/export",
            json=[
                {"instruction": "Q1?", "input": "", "output": "A1"},
                {"instruction": "Q3?", "input": "", "output": "A3"},
            ],
            status=200,
        )
        out = tmp_path / "filtered.json"
        result = export_mod.run(
            backend, "p1",
            output_path=str(out), fmt="alpaca",
            score_gte=4, overwrite=True,
        )
        assert result["count"] == 2
        # Verify the export call carried selectedIds
        export_call = next(
            c for c in responses.calls
            if c.request.url.endswith("/datasets/export")
        )
        body = json.loads(export_call.request.body)
        assert body["selectedIds"] == ["d1", "d3"]
        # And the list call carried scoreRange
        list_call = next(
            c for c in responses.calls
            if "/datasets?" in c.request.url or c.request.url.endswith("/datasets")
            and c.request.method == "GET"
        )
        assert "scoreRange=4-5" in list_call.request.url

    @responses.activate
    def test_writes_valid_json(self, backend, tmp_path):
        records = [
            {"instruction": "Q1?", "input": "", "output": "A1."},
            {"instruction": "Q2?", "input": "", "output": "A2."},
        ]
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/datasets/export",
            json=records,
            status=200,
        )
        out = tmp_path / "out.json"
        result = export_mod.run(
            backend, "p1", output_path=str(out), fmt="alpaca", overwrite=True
        )
        assert result["count"] == 2
        assert result["size"] > 0
        on_disk = json.loads(out.read_text())
        assert on_disk == records


# ── core/prompts ─────────────────────────────────────────────────────


class TestPromptValidation:
    def test_finds_all_placeholders(self):
        found = prompts_mod.validate_template_vars(
            "Q: {{question}}\nText: {{text}}\nLen: {{textLength}}"
        )
        assert found == ["question", "text", "textLength"]

    def test_required_missing_raises(self):
        with pytest.raises(prompts_mod.TemplateValidationError) as exc:
            prompts_mod.validate_template_vars(
                "Only {{text}} here", required=["text", "question"]
            )
        msg = str(exc.value)
        assert "{{question}}" in msg
        assert "FAQ" in msg

    def test_no_placeholders_raises_by_default(self):
        with pytest.raises(prompts_mod.TemplateValidationError):
            prompts_mod.validate_template_vars("just static text, no vars")

    def test_no_placeholders_ok_when_disabled(self):
        # Should not raise
        out = prompts_mod.validate_template_vars(
            "static", require_at_least_one=False
        )
        assert out == []


class TestPromptsAPI:
    @responses.activate
    def test_list_passes_filters(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/custom-prompts",
            json={"customPrompts": [{"promptType": "question", "promptKey": "Q1"}], "templates": []},
            status=200,
        )
        result = prompts_mod.list_prompts(
            backend, "p1", prompt_type="question", language="zh-CN"
        )
        assert result["customPrompts"][0]["promptKey"] == "Q1"
        url = responses.calls[0].request.url
        assert "promptType=question" in url
        assert "language=zh-CN" in url

    @responses.activate
    def test_save_validates_placeholders(self, backend):
        # No HTTP call should happen — validation fails first
        with pytest.raises(prompts_mod.TemplateValidationError):
            prompts_mod.save_prompt(
                backend,
                "p1",
                prompt_type="question",
                prompt_key="QUESTION_PROMPT",
                language="zh-CN",
                content="static, no placeholders",
            )

    @responses.activate
    def test_save_full_body_shape(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/custom-prompts",
            json={"success": True, "result": {"id": "cp1"}},
            status=200,
        )
        prompts_mod.save_prompt(
            backend,
            "p1",
            prompt_type="question",
            prompt_key="QUESTION_PROMPT",
            language="zh-CN",
            content="基于 {{text}} 生成 {{number}} 个问题",
        )
        body = json.loads(responses.calls[0].request.body)
        assert body == {
            "promptType": "question",
            "promptKey": "QUESTION_PROMPT",
            "language": "zh-CN",
            "content": "基于 {{text}} 生成 {{number}} 个问题",
        }

    @responses.activate
    def test_get_returns_none_when_absent(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/custom-prompts",
            json={"customPrompts": [], "templates": []},
            status=200,
        )
        result = prompts_mod.get_prompt(
            backend, "p1",
            prompt_type="question", prompt_key="QUESTION_PROMPT", language="zh-CN",
        )
        assert result is None

    @responses.activate
    def test_delete_uses_query_params(self, backend):
        responses.add(
            responses.DELETE,
            f"{BASE}/api/projects/p1/custom-prompts",
            json={"success": True},
            status=200,
        )
        prompts_mod.delete_prompt(
            backend, "p1",
            prompt_type="question", prompt_key="QUESTION_PROMPT", language="zh-CN",
        )
        url = responses.calls[0].request.url
        assert "promptType=question" in url
        assert "promptKey=QUESTION_PROMPT" in url
        assert "language=zh-CN" in url

    @responses.activate
    def test_batch_save(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/custom-prompts",
            json={"success": True, "results": []},
            status=200,
        )
        prompts_mod.batch_save_prompts(
            backend, "p1",
            prompts=[
                {"promptType": "question", "promptKey": "Q", "language": "en", "content": "..."}
            ],
        )
        body = json.loads(responses.calls[0].request.body)
        assert "prompts" in body and isinstance(body["prompts"], list)


# ── core/project config ──────────────────────────────────────────────


class TestProjectConfig:
    @responses.activate
    def test_set_task_config_merges_and_puts_to_tasks_route(self, backend):
        """Regression: textSplitMinLength / textSplitMaxLength /
        questionGenerationLength etc. live in ``task-config.json``, NOT in the
        Prisma ``Projects`` table. The server route that writes that file is
        ``PUT /api/projects/{id}/tasks`` and it REPLACES the entire file.
        ``GET /api/projects/{id}/tasks`` returns the current contents.

        Therefore ``set_task_config`` must:
          1. GET /tasks to fetch the current config
          2. Merge the caller's overrides into it
          3. PUT /tasks with the **complete** merged dict

        Previous implementation called ``PUT /config`` which only updates
        Projects DB columns and rejected ``textSplitMinLength`` with a 500
        Prisma validation error. See easy-dataset/app/api/projects/
        [projectId]/tasks/route.js."""
        existing = {
            "textSplitMinLength": 1500,
            "textSplitMaxLength": 2000,
            "questionGenerationLength": 240,
            "concurrencyLimit": 5,
            "minerUToken": "PRESERVE-ME",
        }
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/tasks",
            json=existing,
            status=200,
        )
        responses.add(
            responses.PUT,
            f"{BASE}/api/projects/p1/tasks",
            json={"message": "Task configuration updated successfully"},
            status=200,
        )
        project_mod.set_task_config(
            backend, "p1",
            textSplitMinLength=2500, textSplitMaxLength=4000,
        )
        # Two HTTP calls: GET then PUT
        assert len(responses.calls) == 2
        assert responses.calls[0].request.method == "GET"
        assert responses.calls[1].request.method == "PUT"
        body = json.loads(responses.calls[1].request.body)
        # Caller overrides applied
        assert body["textSplitMinLength"] == 2500
        assert body["textSplitMaxLength"] == 4000
        # Untouched fields preserved (no clobbering)
        assert body["questionGenerationLength"] == 240
        assert body["concurrencyLimit"] == 5
        assert body["minerUToken"] == "PRESERVE-ME"


# ── core/chunks: custom split + task config orchestration ───────────


class TestChunksCustomSplit:
    def test_compute_split_points_basic(self):
        content = "AAA---BBB---CCC"
        points = chunks_mod.compute_split_points(content, "---")
        # After each '---' (positions 6 and 12). Last position skipped because
        # it would be at the end of the content.
        assert points == [{"position": 6}, {"position": 12}]

    def test_compute_split_points_empty_separator_raises(self):
        with pytest.raises(ValueError):
            chunks_mod.compute_split_points("abc", "")

    def test_compute_split_points_no_match(self):
        assert chunks_mod.compute_split_points("just some text", "###") == []

    def test_case_2_dash_separator(self):
        content = "comment 1\n---------\ncomment 2\n---------\ncomment 3"
        points = chunks_mod.compute_split_points(content, "---------")
        assert len(points) == 2

    def test_case_4_chapter_separator(self):
        # Three '## 第' occurrences. The first is at position 0, but its
        # boundary lands at position 4 (after the separator) which is INTERIOR
        # to the content, so it produces a split point too — that's fine and
        # matches Easy-Dataset's own behavior of letting the first chunk be
        # empty / discarded server-side.
        content = "## 第一章\nfoo\n## 第二章\nbar\n## 第三章\nbaz"
        points = chunks_mod.compute_split_points(content, "## 第")
        assert len(points) == 3

    @responses.activate
    def test_custom_split_by_separator_full_flow(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/custom-split",
            json={"success": True, "totalChunks": 3},
            status=200,
        )
        result = chunks_mod.custom_split_by_separator(
            backend, "p1",
            file_id="file-1", file_name="reviews.txt",
            content="a\n---\nb\n---\nc",
            separator="---",
        )
        assert result["totalChunks"] == 3
        body = json.loads(responses.calls[0].request.body)
        assert body["fileId"] == "file-1"
        assert body["fileName"] == "reviews.txt"
        assert body["content"] == "a\n---\nb\n---\nc"
        assert isinstance(body["splitPoints"], list)
        assert len(body["splitPoints"]) == 2

    @responses.activate
    def test_custom_split_no_match_raises(self, backend):
        with pytest.raises(ValueError, match="not found"):
            chunks_mod.custom_split_by_separator(
                backend, "p1",
                file_id="file-1", file_name="x.txt",
                content="abc def",
                separator="###",
            )

    @responses.activate
    @responses.activate
    def test_split_sends_full_model_config_object(self, backend):
        """Regression: every LLM-driven endpoint expects ``model`` to be the
        complete model config object (providerId/endpoint/apiKey/modelId/...),
        NOT the model config id string. The server constructs ``new
        LLMClient(model)`` directly from this dict and treats id-only as
        ``providerId=undefined → model=undefined → toLowerCase crash``.
        See easy-dataset/lib/llm/core/index.js:27 (LLMClient constructor)
        and the GUI's selectedModelInfoAtom usage."""
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/split",
            json={"chunks": []},
            status=200,
        )
        chunks_mod.split(
            backend, "p1",
            files=[{"fileName": "doc.md", "fileId": "f-123"}],
            model={
                "id": "mc1", "providerId": "openai",
                "endpoint": "https://api.openai.com/v1",
                "apiKey": "sk-x", "modelId": "gpt-4o-mini",
                "modelName": "gpt-4o-mini", "type": "text",
                "temperature": 0.7, "maxTokens": 4096, "topP": 0.9,
            },
        )
        body = json.loads(responses.calls[0].request.body)
        assert isinstance(body["model"], dict), (
            "model must be the full config object, not just the id"
        )
        assert body["model"]["providerId"] == "openai"
        assert body["model"]["modelId"] == "gpt-4o-mini"
        assert body["model"]["apiKey"] == "sk-x"

    @responses.activate
    def test_split_sends_file_objects_not_strings(self, backend):
        """Regression: /split's POST handler iterates fileNames and passes
        each entry to splitProjectFile(projectId, file), where the second
        argument is destructured as ``{fileName, fileId}``. So the wire
        format requires an array of objects, not strings. Sending strings
        produces a 500 with 'path argument must be of type string. Received
        undefined'. See easy-dataset/lib/file/text-splitter.js:170 and
        app/api/projects/[projectId]/split/route.js:29."""
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/split",
            json={"chunks": [], "totalChunks": 0},
            status=200,
        )
        chunks_mod.split(
            backend, "p1",
            files=[{"fileName": "doc.md", "fileId": "f-123"}],
            model=SAMPLE_MODEL,
        )
        body = json.loads(responses.calls[0].request.body)
        assert isinstance(body["fileNames"], list)
        assert len(body["fileNames"]) == 1
        first = body["fileNames"][0]
        assert isinstance(first, dict), (
            "fileNames must be a list of objects (server destructures "
            "{fileName, fileId} from each entry)"
        )
        assert first["fileName"] == "doc.md"
        assert first["fileId"] == "f-123"

    @responses.activate
    def test_split_rejects_string_filenames(self, backend):
        """Loud error if a caller still passes bare strings."""
        with pytest.raises((TypeError, ValueError)):
            chunks_mod.split(
                backend, "p1",
                files=["doc.md"],  # type: ignore[list-item]
                model=SAMPLE_MODEL,
            )

    @responses.activate
    def test_split_with_text_split_overrides_puts_task_config_first(self, backend):
        # set_task_config first does GET /tasks then PUT /tasks
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/tasks",
            json={"questionGenerationLength": 240, "concurrencyLimit": 5},
            status=200,
        )
        responses.add(
            responses.PUT,
            f"{BASE}/api/projects/p1/tasks",
            json={"message": "Task configuration updated successfully"},
            status=200,
        )
        # Then the actual split
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/split",
            json={"chunks": [], "totalChunks": 0},
            status=200,
        )
        chunks_mod.split(
            backend, "p1",
            files=[{"fileName": "doc.md", "fileId": "f-123"}],
            model=SAMPLE_MODEL,
            text_split_min=800, text_split_max=1200,
        )
        # Three calls, in order: GET /tasks, PUT /tasks, POST /split
        assert responses.calls[0].request.method == "GET"
        assert responses.calls[0].request.url.endswith("/tasks")
        assert responses.calls[1].request.method == "PUT"
        assert responses.calls[1].request.url.endswith("/tasks")
        cfg_body = json.loads(responses.calls[1].request.body)
        assert cfg_body["textSplitMinLength"] == 800
        assert cfg_body["textSplitMaxLength"] == 1200
        # Unrelated fields preserved
        assert cfg_body["questionGenerationLength"] == 240
        assert cfg_body["concurrencyLimit"] == 5

        assert responses.calls[2].request.method == "POST"
        assert responses.calls[2].request.url.endswith("/split")
        split_body = json.loads(responses.calls[2].request.body)
        assert split_body["fileNames"] == [{"fileName": "doc.md", "fileId": "f-123"}]
        assert split_body["model"] == SAMPLE_MODEL


# ── core/datasets: filters + evaluation ──────────────────────────────


class TestDatasetsFilters:
    @responses.activate
    def test_score_range_query_param(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/datasets",
            json={"data": []},
            status=200,
        )
        datasets_mod.list_datasets(backend, "p1", score_gte=4, score_lte=5)
        url = responses.calls[0].request.url
        assert "scoreRange=4-5" in url

    @responses.activate
    def test_score_gte_only(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/datasets",
            json={"data": []},
            status=200,
        )
        datasets_mod.list_datasets(backend, "p1", score_gte=3.5)
        url = responses.calls[0].request.url
        # When only score_gte is given, lte defaults to 5
        assert "scoreRange=3.5-5" in url

    @responses.activate
    def test_confirmed_maps_to_status(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/datasets",
            json={"data": []},
            status=200,
        )
        datasets_mod.list_datasets(backend, "p1", confirmed=True)
        assert "status=confirmed" in responses.calls[0].request.url

    @responses.activate
    def test_text_filters(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/datasets",
            json={"data": []},
            status=200,
        )
        datasets_mod.list_datasets(
            backend, "p1",
            custom_tag="Eval", note_keyword="todo", chunk_name="ch1",
        )
        url = responses.calls[0].request.url
        assert "customTag=Eval" in url
        assert "noteKeyword=todo" in url
        assert "chunkName=ch1" in url


class TestDatasetsEvaluate:
    @responses.activate
    def test_evaluate_single(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/datasets/d1/evaluate",
            json={"success": True, "data": {"score": 4.5, "evaluation": "good"}},
            status=200,
        )
        result = datasets_mod.evaluate(
            backend, "p1", "d1",
            model={"id": "mc1", "modelId": "gpt", "endpoint": "x", "apiKey": "k"},
            language="en",
        )
        assert result["data"]["score"] == 4.5
        body = json.loads(responses.calls[0].request.body)
        assert body["model"]["modelId"] == "gpt"
        assert body["language"] == "en"

    @responses.activate
    def test_batch_evaluate_returns_task_id(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/datasets/batch-evaluate",
            json={"success": True, "data": {"taskId": "task-7"}},
            status=200,
        )
        result = datasets_mod.batch_evaluate(
            backend, "p1",
            model={"id": "mc1", "modelId": "gpt", "endpoint": "x", "apiKey": "k"},
        )
        assert result["data"]["taskId"] == "task-7"


# ──────────────────────────────────────────────────────────────────────
# Round 2: question templates
# ──────────────────────────────────────────────────────────────────────


class TestTemplates:
    def test_normalize_answer_type_aliases(self):
        assert templates_mod.normalize_answer_type("json-schema") == "custom_format"
        assert templates_mod.normalize_answer_type("json") == "custom_format"
        assert templates_mod.normalize_answer_type("text") == "text"
        assert templates_mod.normalize_answer_type("label") == "label"

    def test_parse_label_set(self):
        assert templates_mod.parse_label_set("正面,负面,中性") == ["正面", "负面", "中性"]
        assert templates_mod.parse_label_set("a,  b ,c") == ["a", "b", "c"]
        assert templates_mod.parse_label_set("only") == ["only"]

    def test_load_schema_from_file_round_trip(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text('{"type":"object","properties":{"x":{"type":"string"}}}', encoding="utf-8")
        out = templates_mod.load_schema_from_file(str(p))
        assert json.loads(out) == {"type": "object", "properties": {"x": {"type": "string"}}}

    def test_load_schema_from_file_invalid_json(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text("not json at all", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            templates_mod.load_schema_from_file(str(p))

    @responses.activate
    def test_create_template_label(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/questions/templates",
            json={"id": "tpl1", "answerType": "label"},
            status=200,
        )
        result = templates_mod.create_template(
            backend, "p1",
            question="情感倾向？", source_type="text",
            answer_type="label", labels=["正面", "负面", "中性"],
        )
        assert result["id"] == "tpl1"
        body = json.loads(responses.calls[0].request.body)
        assert body["question"] == "情感倾向？"
        assert body["sourceType"] == "text"
        assert body["answerType"] == "label"
        assert body["labels"] == ["正面", "负面", "中性"]
        assert body["autoGenerate"] is False

    @responses.activate
    def test_create_template_json_schema_alias(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/questions/templates",
            json={"id": "tpl2"},
            status=200,
        )
        templates_mod.create_template(
            backend, "p1",
            question="车型识别", source_type="image",
            answer_type="json-schema", custom_format='{"type":"object"}',
            auto_generate=True,
        )
        body = json.loads(responses.calls[0].request.body)
        assert body["sourceType"] == "image"
        assert body["answerType"] == "custom_format"
        assert body["customFormat"] == '{"type":"object"}'
        assert body["autoGenerate"] is True

    def test_create_template_label_requires_labels(self, backend):
        with pytest.raises(ValueError, match="non-empty labels"):
            templates_mod.create_template(
                backend, "p1",
                question="?", source_type="text",
                answer_type="label",
            )

    def test_create_template_custom_requires_format(self, backend):
        with pytest.raises(ValueError, match="custom_format"):
            templates_mod.create_template(
                backend, "p1",
                question="?", source_type="image",
                answer_type="custom_format",
            )

    def test_create_template_rejects_bad_source(self, backend):
        with pytest.raises(ValueError, match="source_type"):
            templates_mod.create_template(
                backend, "p1",
                question="?", source_type="audio",
                answer_type="text",
            )

    @responses.activate
    def test_list_templates_with_filter(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/questions/templates",
            json={"templates": [{"id": "t1"}, {"id": "t2"}]},
            status=200,
        )
        out = templates_mod.list_templates(backend, "p1", source_type="image")
        assert len(out) == 2
        assert "sourceType=image" in responses.calls[0].request.url

    @responses.activate
    def test_update_template_normalizes_kwargs(self, backend):
        responses.add(
            responses.PUT,
            f"{BASE}/api/projects/p1/questions/templates/t1",
            json={"id": "t1"},
            status=200,
        )
        templates_mod.update_template(
            backend, "p1", "t1",
            answer_type="json-schema", auto_generate=True, source_type="text",
        )
        body = json.loads(responses.calls[0].request.body)
        assert body["answerType"] == "custom_format"
        assert body["autoGenerate"] is True
        assert body["sourceType"] == "text"

    @responses.activate
    def test_delete_template(self, backend):
        responses.add(
            responses.DELETE,
            f"{BASE}/api/projects/p1/questions/templates/t1",
            json={"deleted": True},
            status=200,
        )
        templates_mod.delete_template(backend, "p1", "t1")
        assert responses.calls[0].request.method == "DELETE"


# ──────────────────────────────────────────────────────────────────────
# Round 2: image-source files
# ──────────────────────────────────────────────────────────────────────


class TestFilesImages:
    def test_zip_directory_packs_images_only(self, tmp_path):
        (tmp_path / "a.png").write_bytes(b"\x89PNG\x0a")
        (tmp_path / "b.JPG").write_bytes(b"\xff\xd8")
        (tmp_path / "ignore.txt").write_text("nope", encoding="utf-8")
        zb, included = files_mod._zip_directory(str(tmp_path))
        assert b"PK" in zb[:4] or zb[:2] == b"PK"
        assert sorted(included) == sorted(["a.png", "b.JPG"])
        assert "ignore.txt" not in included

    def test_zip_directory_no_images_raises(self, tmp_path):
        (tmp_path / "x.txt").write_text("hi", encoding="utf-8")
        with pytest.raises(FileNotFoundError, match="No image files"):
            files_mod._zip_directory(str(tmp_path))

    def test_zip_directory_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.png").write_bytes(b"\x89PNG")
        _, included = files_mod._zip_directory(str(tmp_path))
        assert any("deep.png" in n for n in included)

    @responses.activate
    def test_import_image_directory(self, backend, tmp_path):
        (tmp_path / "car1.png").write_bytes(b"\x89PNG")
        (tmp_path / "car2.png").write_bytes(b"\x89PNG")
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/images/zip-import",
            json={"success": True},
            status=200,
        )
        result = files_mod.import_image_directory(backend, "p1", str(tmp_path))
        assert result["imported_count"] == 2
        assert sorted(result["imported_files"]) == ["car1.png", "car2.png"]
        # Verify multipart upload happened
        assert len(responses.calls) == 1

    @responses.activate
    def test_import_pdf_as_images(self, backend, tmp_path):
        pdf = tmp_path / "deck.pdf"
        pdf.write_bytes(b"%PDF-1.4\n...")
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/images/pdf-convert",
            json={"success": True, "pages": 5},
            status=200,
        )
        result = files_mod.import_pdf_as_images(backend, "p1", str(pdf))
        assert result["pages"] == 5

    def test_import_pdf_rejects_non_pdf(self, backend, tmp_path):
        bad = tmp_path / "notpdf.png"
        bad.write_bytes(b"x")
        with pytest.raises(ValueError, match="only .pdf"):
            files_mod.import_pdf_as_images(backend, "p1", str(bad))

    @responses.activate
    def test_list_images(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/images",
            json={"images": [{"id": "i1"}, {"id": "i2"}]},
            status=200,
        )
        result = files_mod.list_images(backend, "p1")
        assert [i["id"] for i in result] == ["i1", "i2"]

    @responses.activate
    def test_delete_image(self, backend):
        responses.add(
            responses.DELETE,
            f"{BASE}/api/projects/p1/images",
            json={"deleted": True},
            status=200,
        )
        files_mod.delete_image(backend, "p1", "i9")
        assert "imageId=i9" in responses.calls[0].request.url


# ──────────────────────────────────────────────────────────────────────
# Round 2: model type=vision
# ──────────────────────────────────────────────────────────────────────


class TestModelType:
    @responses.activate
    def test_set_config_vision(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/model-config",
            json={"id": "mc-vision", "type": "vision"},
            status=200,
        )
        result = model_mod.set_config(
            backend, "p1",
            provider_id="openai", provider_name="OpenAI",
            endpoint="https://api.openai.com/v1",
            api_key="sk-test", model_id="gpt-4o", model_type="vision",
        )
        assert result["type"] == "vision"
        body = json.loads(responses.calls[0].request.body)
        assert body["type"] == "vision"

    def test_set_config_rejects_bad_type(self, backend):
        with pytest.raises(ValueError, match="model_type"):
            model_mod.set_config(
                backend, "p1",
                provider_id="x", provider_name="X",
                endpoint="x", api_key="x", model_id="x",
                model_type="audio",
            )

    def test_find_config_by_type(self):
        configs = [
            {"id": "a", "type": "text"},
            {"id": "b", "type": "vision"},
            {"id": "c", "type": "text"},
        ]
        assert model_mod.find_config_by_type(configs, "vision")["id"] == "b"
        assert model_mod.find_config_by_type(configs, "text")["id"] == "a"
        assert model_mod.find_config_by_type(configs, "audio") is None


# ──────────────────────────────────────────────────────────────────────
# Round 2: image-source question generation
# ──────────────────────────────────────────────────────────────────────


class TestQuestionsImageSource:
    @responses.activate
    def test_generate_image_body_shape(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/generate-questions",
            json={"success": True},
            status=200,
        )
        vision_model = {**SAMPLE_MODEL, "id": "mc-vision", "type": "vision"}
        questions_mod.generate(
            backend, "p1", [], model=vision_model,
            source="image", image_ids=["img1", "img2"], language="zh",
        )
        body = json.loads(responses.calls[0].request.body)
        assert body["sourceType"] == "image"
        assert body["imageIds"] == ["img1", "img2"]
        assert "chunkIds" not in body
        assert body["model"] == vision_model

    @responses.activate
    def test_generate_chunk_body_shape(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/generate-questions",
            json={"success": True},
            status=200,
        )
        questions_mod.generate(backend, "p1", ["c1", "c2"], model=SAMPLE_MODEL, source="chunk")
        body = json.loads(responses.calls[0].request.body)
        assert body["sourceType"] == "chunk"
        assert body["chunkIds"] == ["c1", "c2"]
        assert "imageIds" not in body

    def test_generate_rejects_bad_source(self, backend):
        with pytest.raises(ValueError, match="source must be"):
            questions_mod.generate(backend, "p1", [], model=SAMPLE_MODEL, source="audio")


# ──────────────────────────────────────────────────────────────────────
# Round 2: multi-turn datasets
# ──────────────────────────────────────────────────────────────────────


class TestDatasetsMultiTurn:
    @responses.activate
    def test_generate_multi_turn(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/dataset-conversations",
            json={"id": "conv1", "rounds": 4},
            status=200,
        )
        result = datasets_mod.generate_multi_turn(
            backend, "p1",
            question_id="q1",
            model={"id": "mc1", "modelId": "gpt"},
            system_prompt="You are Einstein.",
            scenario="middle school physics",
            rounds=4, role_a="student", role_b="Einstein",
            language="中文",
        )
        assert result["id"] == "conv1"
        body = json.loads(responses.calls[0].request.body)
        assert body["questionId"] == "q1"
        assert body["systemPrompt"] == "You are Einstein."
        assert body["scenario"] == "middle school physics"
        assert body["rounds"] == 4
        assert body["roleA"] == "student"
        assert body["roleB"] == "Einstein"
        assert body["model"]["modelId"] == "gpt"

    @responses.activate
    def test_list_conversations_with_filters(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/dataset-conversations",
            json={"data": [{"id": "c1"}]},
            status=200,
        )
        result = datasets_mod.list_conversations(
            backend, "p1", role_a="user", role_b="assistant", keyword="physics"
        )
        assert len(result) == 1
        url = responses.calls[0].request.url
        assert "roleA=user" in url
        assert "roleB=assistant" in url
        assert "keyword=physics" in url


# ──────────────────────────────────────────────────────────────────────
# Round 2: export conversations + multi-turn format guard
# ──────────────────────────────────────────────────────────────────────


class TestExportConversations:
    def test_validate_multi_turn_format_rejects_alpaca(self):
        with pytest.raises(ValueError, match="sharegpt"):
            export_mod.validate_multi_turn_format("alpaca")

    def test_validate_multi_turn_format_accepts_sharegpt(self):
        export_mod.validate_multi_turn_format("sharegpt")  # no raise

    @responses.activate
    def test_export_conversations_writes_file(self, backend, tmp_path):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/dataset-conversations/export",
            json={"data": [{"conversations": [{"from": "user", "value": "hi"}]}]},
            status=200,
        )
        out = tmp_path / "conv.json"
        result = export_mod.export_conversations(
            backend, "p1", output_path=str(out), fmt="sharegpt",
        )
        assert os.path.exists(out)
        assert result["format"] == "sharegpt"
        assert result["kind"] == "multi-turn"
        body = json.loads(responses.calls[0].request.body)
        assert body["format"] == "sharegpt"

    @responses.activate
    def test_export_conversations_rejects_alpaca(self, backend, tmp_path):
        out = tmp_path / "conv.json"
        with pytest.raises(ValueError, match="sharegpt"):
            export_mod.export_conversations(
                backend, "p1", output_path=str(out), fmt="alpaca",
            )

    @responses.activate
    def test_export_conversations_no_overwrite(self, backend, tmp_path):
        out = tmp_path / "conv.json"
        out.write_text("existing", encoding="utf-8")
        with pytest.raises(FileExistsError):
            export_mod.export_conversations(
                backend, "p1", output_path=str(out), fmt="sharegpt",
            )


# ──────────────────────────────────────────────────────────────────────
# Round 2: distillation
# ──────────────────────────────────────────────────────────────────────


class TestDistill:
    @responses.activate
    def test_generate_tags(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/distill/tags",
            json={"tags": [{"label": "经典力学"}, {"label": "电磁学"}]},
            status=200,
        )
        result = distill_mod.generate_tags(
            backend, "p1",
            parent_tag="物理学", tag_path="物理学",
            count=5, model={"id": "mc1"}, language="zh",
        )
        assert len(result["tags"]) == 2
        body = json.loads(responses.calls[0].request.body)
        assert body["parentTag"] == "物理学"
        assert body["count"] == 5

    @responses.activate
    def test_generate_questions(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/distill/questions",
            json={"questions": ["Q1", "Q2", "Q3"]},
            status=200,
        )
        result = distill_mod.generate_questions(
            backend, "p1",
            tag_path="物理学/经典力学/牛顿定律",
            current_tag="牛顿定律",
            count=3, model={"id": "mc1"},
        )
        assert len(result["questions"]) == 3
        body = json.loads(responses.calls[0].request.body)
        assert body["currentTag"] == "牛顿定律"
        assert body["tagPath"].endswith("牛顿定律")

    def test_walk_tree_yields_paths(self):
        tree = {
            "name": "物理学",
            "children": [
                {"name": "经典力学", "children": [
                    {"name": "牛顿定律"},
                    {"name": "动量守恒"},
                ]},
                {"name": "电磁学"},
            ],
        }
        nodes = list(distill_mod._walk_tree(tree))
        paths = [n[0] for n in nodes]
        assert "物理学" in paths
        assert "物理学/经典力学" in paths
        assert "物理学/经典力学/牛顿定律" in paths
        assert "物理学/电磁学" in paths
        # Leaves
        leaves = [n[0] for n in nodes if n[3]]
        assert "物理学/经典力学/牛顿定律" in leaves
        assert "物理学/经典力学/动量守恒" in leaves
        assert "物理学/电磁学" in leaves
        assert "物理学" not in leaves
        assert "物理学/经典力学" not in leaves

    @responses.activate
    def test_run_auto_calls_questions_at_each_leaf(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/distill/questions",
            json={"questions": ["q?"]},
            status=200,
        )
        tree = {
            "name": "物理学",
            "children": [
                {"name": "经典力学", "children": [
                    {"name": "牛顿定律"},
                    {"name": "动量守恒"},
                ]},
                {"name": "电磁学"},
            ],
        }
        summary = distill_mod.run_auto(
            backend, "p1",
            label_tree=tree, model={"id": "mc1"},
            questions_per_leaf=2,
        )
        # Three leaves: 牛顿定律, 动量守恒, 电磁学
        assert summary["leaves_processed"] == 3
        assert summary["questions_called"] == 3
        assert len(responses.calls) == 3

    @responses.activate
    def test_run_auto_expand(self, backend):
        # depth=1: one /distill/tags then one /distill/questions per child
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/distill/tags",
            json={"tags": [{"label": "牛顿"}, {"label": "动量"}]},
            status=200,
        )
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/distill/questions",
            json={"questions": ["q?"]},
            status=200,
        )
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/distill/questions",
            json={"questions": ["q?"]},
            status=200,
        )
        summary = distill_mod.run_auto_expand(
            backend, "p1",
            root_topic="物理学", model={"id": "mc1"},
            levels=1, tags_per_level=2, questions_per_leaf=1,
        )
        assert summary["tags_called"] == 1
        assert summary["questions_called"] == 2
        assert summary["leaves_processed"] == 2


# ──────────────────────────────────────────────────────────────────────
# Round 3: eval-datasets benchmark
# ──────────────────────────────────────────────────────────────────────


class TestEval:
    def test_encode_choice_field_list(self):
        out = eval_mod._encode_choice_field(["A", "B", "C"])
        assert json.loads(out) == ["A", "B", "C"]

    def test_encode_choice_field_passthrough_string(self):
        assert eval_mod._encode_choice_field('["x"]') == '["x"]'

    def test_encode_choice_field_none(self):
        assert eval_mod._encode_choice_field(None) is None

    def test_decode_row_unwraps_json_strings(self):
        row = {"id": "e1", "options": '["a","b"]', "correctAnswer": "[0]"}
        decoded = eval_mod._decode_row(dict(row))
        assert decoded["options"] == ["a", "b"]
        assert decoded["correctAnswer"] == [0]

    def test_decode_row_preserves_unparseable(self):
        row = {"options": "free text", "correctAnswer": "answer"}
        decoded = eval_mod._decode_row(dict(row))
        assert decoded["options"] == "free text"
        assert decoded["correctAnswer"] == "answer"

    @responses.activate
    def test_list_decodes_items(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/eval-datasets",
            json={
                "items": [
                    {"id": "e1", "options": '["a","b"]', "correctAnswer": "[0]"},
                    {"id": "e2", "options": None, "correctAnswer": "free text"},
                ],
                "total": 2,
            },
            status=200,
        )
        result = eval_mod.list_eval_datasets(backend, "p1", question_type="single_choice")
        assert result["items"][0]["options"] == ["a", "b"]
        assert result["items"][0]["correctAnswer"] == [0]
        assert result["items"][1]["correctAnswer"] == "free text"
        assert "questionType=single_choice" in responses.calls[0].request.url

    @responses.activate
    def test_create_choice_question_encodes_fields(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/eval-datasets",
            json={"id": "e3"},
            status=200,
        )
        eval_mod.create_eval_dataset(
            backend, "p1",
            question="Which planet is closest to the sun?",
            question_type="single_choice",
            options=["Mercury", "Venus", "Earth", "Mars"],
            correct_answer=[0],
            tags=["astronomy"],
        )
        body = json.loads(responses.calls[0].request.body)
        assert body["questionType"] == "single_choice"
        assert json.loads(body["options"]) == ["Mercury", "Venus", "Earth", "Mars"]
        assert json.loads(body["correctAnswer"]) == [0]
        assert body["tags"] == "astronomy"

    @responses.activate
    def test_create_short_answer_does_not_encode(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/eval-datasets",
            json={"id": "e4"},
            status=200,
        )
        eval_mod.create_eval_dataset(
            backend, "p1",
            question="Define entropy.",
            question_type="short_answer",
            correct_answer="Disorder of a system.",
        )
        body = json.loads(responses.calls[0].request.body)
        assert body["questionType"] == "short_answer"
        assert body["correctAnswer"] == "Disorder of a system."
        assert "options" not in body

    def test_create_choice_requires_options(self, backend):
        with pytest.raises(ValueError, match="requires --options"):
            eval_mod.create_eval_dataset(
                backend, "p1",
                question="?",
                question_type="multiple_choice",
                correct_answer=[0],
            )

    def test_create_rejects_bad_type(self, backend):
        with pytest.raises(ValueError, match="question_type"):
            eval_mod.create_eval_dataset(
                backend, "p1",
                question="?", question_type="essay",
                correct_answer="x",
            )

    @responses.activate
    def test_sample_passes_filters(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/eval-datasets/sample",
            json={"code": 0, "data": {"total": 100, "selectedCount": 10, "ids": ["e1"]*10}},
            status=200,
        )
        eval_mod.sample(
            backend, "p1",
            question_type="single_choice", tags=["astronomy"],
            limit=10, strategy="random",
        )
        body = json.loads(responses.calls[0].request.body)
        assert body["questionType"] == "single_choice"
        assert body["tags"] == ["astronomy"]
        assert body["limit"] == 10

    @responses.activate
    def test_count_returns_breakdown(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/eval-datasets/count",
            json={"code": 0, "data": {"total": 50, "byType": {"single_choice": 30, "short_answer": 20}}},
            status=200,
        )
        result = eval_mod.count(backend, "p1")
        assert result["data"]["total"] == 50

    @responses.activate
    def test_export_writes_file(self, backend, tmp_path):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/eval-datasets/export",
            body=b'{"items":[]}',
            status=200,
            content_type="application/octet-stream",
        )
        out = tmp_path / "bench.jsonl"
        result = eval_mod.export(
            backend, "p1", output_path=str(out), fmt="jsonl",
        )
        assert os.path.exists(out)
        assert result["format"] == "jsonl"
        assert result["kind"] == "eval-dataset"

    def test_export_rejects_bad_format(self, backend, tmp_path):
        out = tmp_path / "x.xlsx"
        with pytest.raises(ValueError, match="format"):
            eval_mod.export(backend, "p1", output_path=str(out), fmt="xlsx")

    @responses.activate
    def test_import_file_multipart(self, backend, tmp_path):
        f = tmp_path / "bench.json"
        f.write_text('[{"question":"q","correctAnswer":"a"}]', encoding="utf-8")
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/eval-datasets/import",
            json={"code": 0, "data": {"total": 1}},
            status=200,
        )
        result = eval_mod.import_file(
            backend, "p1",
            file_path=str(f), question_type="short_answer", tags=["seed"],
        )
        assert result["data"]["total"] == 1

    def test_import_rejects_bad_type(self, backend, tmp_path):
        f = tmp_path / "x.json"
        f.write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError, match="question_type"):
            eval_mod.import_file(
                backend, "p1", file_path=str(f), question_type="essay",
            )

    @responses.activate
    def test_delete_many_uses_json_body(self, backend):
        responses.add(
            responses.DELETE,
            f"{BASE}/api/projects/p1/eval-datasets",
            json={"success": True, "deleted": 3},
            status=200,
        )
        eval_mod.delete_many(backend, "p1", ["e1", "e2", "e3"])
        body = json.loads(responses.calls[0].request.body)
        assert body == {"ids": ["e1", "e2", "e3"]}

    @responses.activate
    def test_copy_from_dataset(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/datasets/d1/copy-to-eval",
            json={"success": True, "evalDataset": {"id": "e9"}},
            status=200,
        )
        result = eval_mod.copy_from_dataset(backend, "p1", "d1")
        assert result["evalDataset"]["id"] == "e9"

    @responses.activate
    def test_generate_variant_body_shape(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/datasets/generate-eval-variant",
            json={"success": True, "data": []},
            status=200,
        )
        eval_mod.generate_variant(
            backend, "p1",
            dataset_id="d1", model={"id": "mc1", "modelId": "gpt"},
            question_type="single_choice", count=5, language="en",
        )
        body = json.loads(responses.calls[0].request.body)
        assert body["datasetId"] == "d1"
        assert body["count"] == 5
        assert body["questionType"] == "single_choice"
        assert body["model"]["modelId"] == "gpt"


# ──────────────────────────────────────────────────────────────────────
# Round 3: eval-tasks
# ──────────────────────────────────────────────────────────────────────


class TestEvalTasks:
    @responses.activate
    def test_create_task_body_shape(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/eval-tasks",
            json={"code": 0, "data": [{"id": "t1"}]},
            status=200,
        )
        eval_tasks_mod.create_task(
            backend, "p1",
            models=[{"modelId": "gpt-4o", "providerId": "openai"}],
            eval_dataset_ids=["e1", "e2"],
            judge_model_id="gpt-4o-mini",
            judge_provider_id="openai",
            language="en",
            custom_score_anchors={"5": "perfect", "0": "wrong"},
        )
        body = json.loads(responses.calls[0].request.body)
        assert body["models"][0]["modelId"] == "gpt-4o"
        assert body["evalDatasetIds"] == ["e1", "e2"]
        assert body["judgeModelId"] == "gpt-4o-mini"
        assert body["language"] == "en"
        # customScoreAnchors must be JSON-encoded string
        assert isinstance(body["customScoreAnchors"], str)
        assert json.loads(body["customScoreAnchors"])["5"] == "perfect"

    def test_create_task_rejects_empty_models(self, backend):
        with pytest.raises(ValueError, match="models"):
            eval_tasks_mod.create_task(
                backend, "p1", models=[], eval_dataset_ids=["e1"],
            )

    def test_create_task_rejects_empty_eval_ids(self, backend):
        with pytest.raises(ValueError, match="eval_dataset_ids"):
            eval_tasks_mod.create_task(
                backend, "p1",
                models=[{"modelId": "x", "providerId": "y"}],
                eval_dataset_ids=[],
            )

    @responses.activate
    def test_get_task_passes_filters(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/eval-tasks/t1",
            json={"code": 0, "data": {"task": {"id": "t1"}, "results": []}},
            status=200,
        )
        eval_tasks_mod.get_task(
            backend, "p1", "t1",
            page=2, page_size=10, type_filter="single_choice", is_correct=True,
        )
        url = responses.calls[0].request.url
        assert "page=2" in url
        assert "isCorrect=true" in url
        assert "type=single_choice" in url

    @responses.activate
    def test_interrupt(self, backend):
        responses.add(
            responses.PUT,
            f"{BASE}/api/projects/p1/eval-tasks/t1",
            json={"code": 0, "message": "interrupted"},
            status=200,
        )
        eval_tasks_mod.interrupt_task(backend, "p1", "t1")
        body = json.loads(responses.calls[0].request.body)
        assert body == {"action": "interrupt"}


# ──────────────────────────────────────────────────────────────────────
# Round 3: blind-test tasks
# ──────────────────────────────────────────────────────────────────────


class TestBlindTest:
    @responses.activate
    def test_create_task_body_shape(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/blind-test-tasks",
            json={"code": 0, "data": {"id": "bt1"}},
            status=200,
        )
        blind_mod.create_task(
            backend, "p1",
            model_a={"modelId": "gpt-4o", "providerId": "openai"},
            model_b={"modelId": "claude-opus", "providerId": "anthropic"},
            eval_dataset_ids=["e1", "e2"],
            language="en",
        )
        body = json.loads(responses.calls[0].request.body)
        assert body["modelA"]["modelId"] == "gpt-4o"
        assert body["modelB"]["providerId"] == "anthropic"
        assert body["evalDatasetIds"] == ["e1", "e2"]

    def test_create_rejects_empty_eval_ids(self, backend):
        with pytest.raises(ValueError, match="eval_dataset_ids"):
            blind_mod.create_task(
                backend, "p1",
                model_a={"modelId": "a", "providerId": "x"},
                model_b={"modelId": "b", "providerId": "y"},
                eval_dataset_ids=[],
            )

    @responses.activate
    def test_vote_body_includes_swap_and_answers(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/blind-test-tasks/bt1/vote",
            json={"code": 0, "data": {"success": True, "isCompleted": False}},
            status=200,
        )
        blind_mod.vote(
            backend, "p1", "bt1",
            vote_value="left", question_id="q1",
            is_swapped=True, left_answer="A1", right_answer="B1",
        )
        body = json.loads(responses.calls[0].request.body)
        assert body == {
            "vote": "left",
            "questionId": "q1",
            "isSwapped": True,
            "leftAnswer": "A1",
            "rightAnswer": "B1",
        }

    def test_vote_rejects_bad_value(self, backend):
        with pytest.raises(ValueError, match="vote must be"):
            blind_mod.vote(
                backend, "p1", "bt1",
                vote_value="maybe", question_id="q1",
                is_swapped=False, left_answer="", right_answer="",
            )

    @responses.activate
    def test_run_manual_loop_walks_to_completion(self, backend):
        # /current returns one question, then completed
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/blind-test-tasks/bt1/current",
            json={"questionId": "q1", "leftAnswer": "A", "rightAnswer": "BB", "isSwapped": False},
            status=200,
        )
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/blind-test-tasks/bt1/vote",
            json={"code": 0, "data": {"isCompleted": True, "scores": {"modelA": 0, "modelB": 1}}},
            status=200,
        )
        votes = []

        def _judge(payload):
            votes.append(payload["questionId"])
            return "right" if len(payload["rightAnswer"]) > len(payload["leftAnswer"]) else "left"

        summary = blind_mod.run_manual_loop(
            backend, "p1", "bt1", vote_callback=_judge,
        )
        assert summary["votes_cast"] == 1
        assert summary["by_vote"]["right"] == 1
        assert summary["final_scores"]["modelB"] == 1
        assert votes == ["q1"]


# ──────────────────────────────────────────────────────────────────────
# Round 3: Genre-Audience pairs
# ──────────────────────────────────────────────────────────────────────


class TestGA:
    @responses.activate
    def test_batch_generate_body_shape(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/batch-generateGA",
            json={"success": True, "summary": {"total": 5}},
            status=200,
        )
        ga_mod.batch_generate(
            backend, "p1",
            file_ids=["f1", "f2"], model_config_id="mc1",
            language="English", append_mode=True,
        )
        body = json.loads(responses.calls[0].request.body)
        assert body["fileIds"] == ["f1", "f2"]
        assert body["modelConfigId"] == "mc1"
        assert body["language"] == "English"
        assert body["appendMode"] is True

    def test_batch_generate_rejects_empty_files(self, backend):
        with pytest.raises(ValueError, match="file_ids"):
            ga_mod.batch_generate(
                backend, "p1", file_ids=[], model_config_id="mc1",
            )

    @responses.activate
    def test_add_manual_body_shape(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/batch-add-manual-ga",
            json={"success": True},
            status=200,
        )
        ga_mod.add_manual(
            backend, "p1",
            file_ids=["f1"],
            genre_title="技术教程",
            audience_title="初学者",
            genre_desc="step-by-step style",
            audience_desc="no prior background",
        )
        body = json.loads(responses.calls[0].request.body)
        assert body["fileIds"] == ["f1"]
        assert body["gaPair"]["genreTitle"] == "技术教程"
        assert body["gaPair"]["audienceTitle"] == "初学者"
        assert body["appendMode"] is True  # default

    def test_add_manual_requires_titles(self, backend):
        with pytest.raises(ValueError, match="genre_title and audience_title"):
            ga_mod.add_manual(
                backend, "p1",
                file_ids=["f1"], genre_title="", audience_title="",
            )

    @responses.activate
    def test_list_pairs_unwraps_data(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/files/f1/ga-pairs",
            json={"success": True, "data": [{"id": "g1"}, {"id": "g2"}]},
            status=200,
        )
        result = ga_mod.list_pairs(backend, "p1", "f1")
        assert [g["id"] for g in result] == ["g1", "g2"]

    @responses.activate
    def test_set_active_uses_patch(self, backend):
        responses.add(
            responses.PATCH,
            f"{BASE}/api/projects/p1/files/f1/ga-pairs",
            json={"success": True},
            status=200,
        )
        ga_mod.set_active(
            backend, "p1", "f1", ga_pair_id="g3", is_active=False,
        )
        body = json.loads(responses.calls[0].request.body)
        assert body == {"gaPairId": "g3", "isActive": False}

    def test_estimate_inflation_arithmetic(self):
        result = ga_mod.estimate_inflation(
            file_count=4, base_question_count=20,
        )
        assert result["files"] == 4
        assert result["pairs_per_file"] == 5
        assert result["max_pairs_total"] == 20
        assert result["estimated_max_questions"] == 100
        assert result["estimated_token_inflation"] == 3.9
        assert "warning" in result

    def test_estimate_inflation_custom_factor(self):
        result = ga_mod.estimate_inflation(
            file_count=1, base_question_count=10, inflation_factor=2.5,
        )
        assert result["estimated_token_inflation"] == 2.5

    def test_estimate_inflation_rejects_negatives(self):
        with pytest.raises(ValueError):
            ga_mod.estimate_inflation(file_count=-1, base_question_count=10)


# ──────────────────────────────────────────────────────────────────────
# Round 3: client-side export extensions
# ──────────────────────────────────────────────────────────────────────


class TestExportFormats:
    def test_parse_field_map_simple(self):
        result = export_mod.parse_field_map(["question=instruction", "answer=output"])
        assert result == {"question": "instruction", "answer": "output"}

    def test_parse_field_map_strips_whitespace(self):
        result = export_mod.parse_field_map([" q = inst ", "a=out"])
        assert result == {"q": "inst", "a": "out"}

    def test_parse_field_map_rejects_missing_eq(self):
        with pytest.raises(ValueError, match="src=dst"):
            export_mod.parse_field_map(["question"])

    def test_parse_field_map_rejects_empty_target(self):
        with pytest.raises(ValueError, match="non-empty"):
            export_mod.parse_field_map(["question="])

    def test_apply_field_map_renames(self):
        records = [{"question": "q1", "answer": "a1", "score": 5}]
        result = export_mod.apply_field_map(
            records, {"question": "instruction", "answer": "output"}
        )
        assert result == [{"instruction": "q1", "output": "a1", "score": 5}]

    def test_apply_field_map_passthrough_when_empty(self):
        records = [{"q": "x"}]
        assert export_mod.apply_field_map(records, {}) == records

    def test_parse_split_ratio_fractions(self):
        assert export_mod.parse_split_ratio("0.7,0.15,0.15") == (0.7, 0.15, 0.15)

    def test_parse_split_ratio_percentages(self):
        result = export_mod.parse_split_ratio("70,15,15")
        assert result[0] == pytest.approx(0.7)
        assert result[1] == pytest.approx(0.15)

    def test_parse_split_ratio_slash_separator(self):
        assert export_mod.parse_split_ratio("0.6/0.2/0.2") == (0.6, 0.2, 0.2)

    def test_parse_split_ratio_rejects_bad_count(self):
        with pytest.raises(ValueError, match="3 comma-separated"):
            export_mod.parse_split_ratio("0.5,0.5")

    def test_parse_split_ratio_rejects_bad_sum(self):
        with pytest.raises(ValueError, match="sum"):
            export_mod.parse_split_ratio("0.3,0.3,0.3")

    def test_parse_split_ratio_rejects_negative(self):
        with pytest.raises(ValueError, match="≥ 0"):
            export_mod.parse_split_ratio("-0.1,0.6,0.5")

    def test_deterministic_split_is_stable(self):
        records = [{"id": str(i), "v": i} for i in range(100)]
        a = export_mod.deterministic_split(records, train=0.7, valid=0.15, test=0.15)
        b = export_mod.deterministic_split(list(reversed(records)), train=0.7, valid=0.15, test=0.15)
        # Same id → same bucket regardless of input order
        a_train_ids = {r["id"] for r in a["train"]}
        b_train_ids = {r["id"] for r in b["train"]}
        assert a_train_ids == b_train_ids
        # Buckets cover everything
        total = len(a["train"]) + len(a["valid"]) + len(a["test"])
        assert total == 100

    def test_deterministic_split_empty(self):
        result = export_mod.deterministic_split([], train=0.7, valid=0.15, test=0.15)
        assert result == {"train": [], "valid": [], "test": []}

    def test_serialize_json(self):
        data = export_mod.serialize_records([{"a": 1}, {"a": 2}], file_type="json")
        assert json.loads(data) == [{"a": 1}, {"a": 2}]

    def test_serialize_jsonl(self):
        data = export_mod.serialize_records(
            [{"a": 1}, {"a": 2}], file_type="jsonl"
        )
        lines = data.decode("utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"a": 1}

    def test_serialize_csv_collects_union_of_keys(self):
        data = export_mod.serialize_records(
            [{"a": 1, "b": 2}, {"a": 3, "c": 4}], file_type="csv",
        )
        text = data.decode("utf-8")
        # Header should include all three keys
        first_line = text.split("\r\n")[0] if "\r\n" in text else text.split("\n")[0]
        for h in ("a", "b", "c"):
            assert h in first_line

    def test_serialize_csv_json_encodes_nested(self):
        data = export_mod.serialize_records(
            [{"q": "x", "tags": ["a", "b"]}], file_type="csv",
        )
        text = data.decode("utf-8")
        # csv.DictWriter doubles internal " when quoting; the encoded JSON
        # array therefore appears as ""a"", ""b"" inside a quoted cell.
        assert '""a""' in text and '""b""' in text

    def test_serialize_csv_empty(self):
        assert export_mod.serialize_records([], file_type="csv") == b""

    def test_serialize_rejects_xlsx(self):
        with pytest.raises(ValueError, match="file_type"):
            export_mod.serialize_records([{"a": 1}], file_type="xlsx")

    # ── client-side format conversion (Alpaca / ShareGPT / multilingual) ──
    # The Easy-Dataset server's /datasets/export route returns RAW dataset
    # rows (just queries the DB). Format conversion (instruction/input/output
    # for Alpaca, messages array for ShareGPT, etc.) is done client-side
    # in the GUI's useDatasetExport.formatDataBatch — and therefore the CLI
    # must do the same. The previous CLI just wrote raw rows to disk and
    # called it "alpaca" output, which broke every consumer.

    def test_format_records_alpaca(self):
        rows = [
            {"id": "d1", "question": "Q1?", "answer": "A1.", "cot": "thinking 1"},
            {"id": "d2", "question": "Q2?", "answer": "A2.", "cot": ""},
        ]
        out = export_mod.format_records(rows, fmt="alpaca")
        assert len(out) == 2
        assert out[0] == {"instruction": "Q1?", "input": "", "output": "A1.", "system": ""}
        assert out[1] == {"instruction": "Q2?", "input": "", "output": "A2.", "system": ""}

    def test_format_records_alpaca_with_cot(self):
        rows = [{"question": "Q?", "answer": "A.", "cot": "step1\nstep2"}]
        out = export_mod.format_records(rows, fmt="alpaca", include_cot=True)
        assert out[0]["instruction"] == "Q?"
        assert out[0]["output"] == "<think>step1\nstep2</think>\nA."

    def test_format_records_alpaca_system_prompt(self):
        rows = [{"question": "Q?", "answer": "A."}]
        out = export_mod.format_records(rows, fmt="alpaca", system_prompt="You are helpful.")
        assert out[0]["system"] == "You are helpful."

    def test_format_records_sharegpt(self):
        rows = [{"question": "Q?", "answer": "A.", "cot": "x"}]
        out = export_mod.format_records(rows, fmt="sharegpt")
        assert "messages" in out[0]
        msgs = out[0]["messages"]
        assert msgs == [
            {"role": "user", "content": "Q?"},
            {"role": "assistant", "content": "A."},
        ]

    def test_format_records_sharegpt_with_system_and_cot(self):
        rows = [{"question": "Q?", "answer": "A.", "cot": "thinking"}]
        out = export_mod.format_records(
            rows, fmt="sharegpt", system_prompt="sys", include_cot=True,
        )
        msgs = out[0]["messages"]
        assert msgs[0] == {"role": "system", "content": "sys"}
        assert msgs[1] == {"role": "user", "content": "Q?"}
        assert msgs[2]["role"] == "assistant"
        assert msgs[2]["content"] == "<think>thinking</think>\nA."

    def test_format_records_multilingual_thinking(self):
        rows = [{"question": "Q?", "answer": "A.", "cot": "step"}]
        out = export_mod.format_records(
            rows, fmt="multilingual-thinking", include_cot=True,
            reasoning_language="Chinese",
        )
        rec = out[0]
        assert rec["user"] == "Q?"
        assert rec["final"] == "A."
        assert rec["analysis"] == "step"
        assert rec["reasoning_language"] == "Chinese"
        assert isinstance(rec["messages"], list)
        assert rec["messages"][2]["thinking"] == "step"

    def test_format_records_unknown_fmt_passthrough_raises(self):
        with pytest.raises(ValueError):
            export_mod.format_records([{"question": "x", "answer": "y"}], fmt="csv")

    def test_format_records_skips_rows_without_question_or_answer(self):
        rows = [
            {"question": "Q1?", "answer": "A1."},
            {"question": "", "answer": "A2."},     # empty question — skip
            {"question": "Q3?", "answer": ""},     # empty answer — skip
            {"question": "Q4?", "answer": "A4."},
        ]
        out = export_mod.format_records(rows, fmt="alpaca")
        assert [r["instruction"] for r in out] == ["Q1?", "Q4?"]

    @responses.activate
    def test_run_emits_alpaca_format_not_raw_rows(self, backend, tmp_path):
        """Regression: previously ``export run --format alpaca`` wrote the
        raw dataset row dict (id/projectId/questionId/...) to disk instead
        of the canonical Alpaca tuple. ``run`` must call ``format_records``
        before serialization."""
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/datasets/export",
            json=[
                {"id": "d1", "question": "Q1?", "answer": "A1.",
                 "cot": "x", "score": 4.5},
            ],
            status=200,
        )
        out = tmp_path / "alpaca.json"
        export_mod.run(
            backend, "p1",
            output_path=str(out), fmt="alpaca",
            file_type="json", confirmed_only=False, overwrite=True,
        )
        data = json.loads(out.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 1
        assert set(data[0].keys()) == {"instruction", "input", "output", "system"}
        assert data[0]["instruction"] == "Q1?"
        assert data[0]["output"] == "A1."
        # Raw row keys must NOT leak through
        assert "id" not in data[0]
        assert "questionId" not in data[0]
        assert "score" not in data[0]

    @responses.activate
    def test_run_with_field_map_and_jsonl(self, backend, tmp_path):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/datasets/export",
            json=[
                {"id": "d1", "question": "q1", "answer": "a1"},
                {"id": "d2", "question": "q2", "answer": "a2"},
            ],
            status=200,
        )
        out = tmp_path / "out.jsonl"
        result = export_mod.run(
            backend, "p1",
            output_path=str(out), fmt="alpaca",
            file_type="jsonl",
            field_map={"question": "instruction", "answer": "output"},
            confirmed_only=False,
        )
        assert result["file_type"] == "jsonl"
        assert result["count"] == 2
        # Validate the file is JSONL with the renamed keys
        text = out.read_text(encoding="utf-8").strip().split("\n")
        first = json.loads(text[0])
        assert "instruction" in first
        assert "output" in first
        assert "question" not in first

    @responses.activate
    def test_run_with_split_writes_three_files(self, backend, tmp_path):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/datasets/export",
            json=[{"id": str(i), "question": f"q{i}", "answer": f"a{i}"} for i in range(20)],
            status=200,
        )
        out = tmp_path / "split.json"
        result = export_mod.run(
            backend, "p1",
            output_path=str(out), fmt="alpaca",
            split=(0.7, 0.15, 0.15),
            confirmed_only=False,
        )
        assert "splits" in result
        for name in ("train", "valid", "test"):
            assert os.path.exists(result["splits"][name]["output"])
        total = sum(result["splits"][n]["count"] for n in ("train", "valid", "test"))
        assert total == 20

    @responses.activate
    def test_run_with_include_image_path_unwraps_other(self, backend, tmp_path):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/datasets/export",
            json=[
                {"id": "d1", "question": "what?", "answer": "car",
                 "other": '{"imagePath":"/abs/cars/img1.png"}'},
            ],
            status=200,
        )
        out = tmp_path / "vqa.json"
        export_mod.run(
            backend, "p1",
            output_path=str(out), fmt="alpaca",
            include_image_path=True,
            confirmed_only=False,
        )
        records = json.loads(out.read_text(encoding="utf-8"))
        assert records[0]["imagePath"] == "/abs/cars/img1.png"

    @responses.activate
    def test_run_with_include_chunk_preserves_chunk_fields(self, backend, tmp_path):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/datasets/export",
            json=[
                {"id": "d1", "question": "?", "answer": "x",
                 "chunkContent": "source paragraph", "chunkName": "ch1"},
            ],
            status=200,
        )
        out = tmp_path / "withchunk.json"
        export_mod.run(
            backend, "p1",
            output_path=str(out), fmt="alpaca",
            include_chunk=True,
            confirmed_only=False,
        )
        records = json.loads(out.read_text(encoding="utf-8"))
        assert records[0]["chunkContent"] == "source paragraph"
        assert records[0]["chunkName"] == "ch1"

    def test_run_rejects_bad_file_type(self, backend, tmp_path):
        out = tmp_path / "x.xlsx"
        with pytest.raises(ValueError, match="file_type"):
            export_mod.run(
                backend, "p1",
                output_path=str(out), fmt="alpaca", file_type="xlsx",
            )

    def test_run_split_no_overwrite_guard(self, backend, tmp_path):
        out = tmp_path / "out.json"
        # Pre-create the train file so the guard fires.
        (tmp_path / "out-train.json").write_text("existing", encoding="utf-8")
        with pytest.raises(FileExistsError):
            export_mod.run(
                backend, "p1",
                output_path=str(out), fmt="alpaca",
                split=(0.7, 0.15, 0.15),
            )


# ──────────────────────────────────────────────────────────────────────
# Round 4: domain tree tags
# ──────────────────────────────────────────────────────────────────────


class TestTags:
    @responses.activate
    def test_list_unwraps_tags_key(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/tags",
            json={"tags": [{"id": "t1", "label": "root", "children": []}]},
            status=200,
        )
        result = tags_mod.list_tags(backend, "p1")
        assert len(result) == 1
        assert result[0]["label"] == "root"

    @responses.activate
    def test_save_tag_create_omits_id(self, backend):
        responses.add(
            responses.PUT,
            f"{BASE}/api/projects/p1/tags",
            json={"tags": {"id": "t-new", "label": "physics", "parentId": None}},
            status=200,
        )
        tags_mod.save_tag(backend, "p1", label="physics")
        body = json.loads(responses.calls[0].request.body)
        assert body == {"tags": {"label": "physics", "parentId": None}}

    @responses.activate
    def test_save_tag_update_includes_id(self, backend):
        responses.add(
            responses.PUT,
            f"{BASE}/api/projects/p1/tags",
            json={"tags": {"id": "t1", "label": "renamed", "parentId": "t0"}},
            status=200,
        )
        tags_mod.save_tag(backend, "p1", label="renamed", tag_id="t1", parent_id="t0")
        body = json.loads(responses.calls[0].request.body)
        assert body["tags"]["id"] == "t1"
        assert body["tags"]["label"] == "renamed"
        assert body["tags"]["parentId"] == "t0"

    def test_save_tag_rejects_empty_label(self, backend):
        with pytest.raises(ValueError, match="non-empty"):
            tags_mod.save_tag(backend, "p1", label="   ")

    @responses.activate
    def test_delete_tag_uses_query_param(self, backend):
        responses.add(
            responses.DELETE,
            f"{BASE}/api/projects/p1/tags",
            json={"success": True, "message": "deleted"},
            status=200,
        )
        tags_mod.delete_tag(backend, "p1", "t1")
        assert "id=t1" in responses.calls[0].request.url

    @responses.activate
    def test_get_questions_by_tag_uses_post(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/tags",
            json={"questions": []},
            status=200,
        )
        tags_mod.get_questions_by_tag(backend, "p1", "physics")
        body = json.loads(responses.calls[0].request.body)
        assert body == {"tagName": "physics"}

    def test_walk_tree_depth_first(self):
        tree = [
            {"id": "1", "label": "a", "children": [
                {"id": "2", "label": "a.1"},
                {"id": "3", "label": "a.2", "children": [
                    {"id": "4", "label": "a.2.1"},
                ]},
            ]},
            {"id": "5", "label": "b"},
        ]
        labels = [n["label"] for n in tags_mod.walk_tree(tree)]
        assert labels == ["a", "a.1", "a.2", "a.2.1", "b"]

    def test_find_tag_by_label_first_match(self):
        tree = [
            {"id": "1", "label": "a", "children": [
                {"id": "2", "label": "target"},
            ]},
        ]
        result = tags_mod.find_tag(tree, label="target")
        assert result["id"] == "2"

    def test_find_tag_by_id(self):
        tree = [{"id": "1", "label": "root", "children": [
            {"id": "deep", "label": "x"},
        ]}]
        result = tags_mod.find_tag(tree, tag_id="deep")
        assert result["label"] == "x"

    def test_find_tag_returns_none_when_missing(self):
        result = tags_mod.find_tag([{"id": "1", "label": "x"}], label="nope")
        assert result is None

    def test_find_tag_requires_search_arg(self):
        with pytest.raises(ValueError, match="label or tag_id"):
            tags_mod.find_tag([])

    def test_collect_labels_flattens(self):
        tree = [{"id": "1", "label": "root", "children": [
            {"id": "2", "label": "child"},
        ]}]
        assert tags_mod.collect_labels(tree) == ["root", "child"]

    def test_walk_tree_handles_child_alias(self):
        # Server uses "child" in some response shapes; walker accepts both.
        tree = [{"id": "1", "label": "root", "child": [
            {"id": "2", "label": "leaf"},
        ]}]
        labels = [n["label"] for n in tags_mod.walk_tree(tree)]
        assert labels == ["root", "leaf"]


# ──────────────────────────────────────────────────────────────────────
# Round 4: background task system
# ──────────────────────────────────────────────────────────────────────


class TestTasks:
    def test_status_constants(self):
        assert tasks_mod.STATUS_PROCESSING == 0
        assert tasks_mod.STATUS_COMPLETED == 1
        assert tasks_mod.STATUS_FAILED == 2
        assert tasks_mod.STATUS_INTERRUPTED == 3
        assert tasks_mod.TERMINAL_STATUSES == {1, 2, 3}

    def test_status_label_known(self):
        assert tasks_mod.status_label(0) == "processing"
        assert tasks_mod.status_label(1) == "completed"
        assert tasks_mod.status_label(2) == "failed"
        assert tasks_mod.status_label(3) == "interrupted"

    def test_status_label_unknown(self):
        assert tasks_mod.status_label(99) == "status-99"
        assert tasks_mod.status_label(None) == "unknown"

    @responses.activate
    def test_list_tasks_passes_filters(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/tasks/list",
            json={"code": 0, "data": [], "total": 0, "page": 0, "limit": 10},
            status=200,
        )
        tasks_mod.list_tasks(
            backend, "p1",
            task_type="answer-generation", status=0, page=2, limit=10,
        )
        url = responses.calls[0].request.url
        assert "taskType=answer-generation" in url
        assert "status=0" in url
        assert "page=2" in url
        assert "limit=10" in url

    @responses.activate
    def test_get_task(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/tasks/t1",
            json={"code": 0, "data": {"id": "t1", "status": 1}},
            status=200,
        )
        result = tasks_mod.get_task(backend, "p1", "t1")
        assert result["data"]["status"] == 1

    @responses.activate
    def test_cancel_uses_patch_with_status_3(self, backend):
        responses.add(
            responses.PATCH,
            f"{BASE}/api/projects/p1/tasks/t1",
            json={"code": 0, "data": {"id": "t1", "status": 3}},
            status=200,
        )
        tasks_mod.cancel_task(backend, "p1", "t1")
        body = json.loads(responses.calls[0].request.body)
        assert body == {"status": 3}

    @responses.activate
    def test_update_task_arbitrary_fields(self, backend):
        responses.add(
            responses.PATCH,
            f"{BASE}/api/projects/p1/tasks/t1",
            json={"code": 0, "data": {}},
            status=200,
        )
        tasks_mod.update_task(
            backend, "p1", "t1",
            completedCount=42, totalCount=100, note="halfway",
        )
        body = json.loads(responses.calls[0].request.body)
        assert body == {"completedCount": 42, "totalCount": 100, "note": "halfway"}

    @responses.activate
    def test_wait_for_returns_when_terminal(self, backend):
        # First call: still processing. Second: completed.
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/tasks/t1",
            json={"code": 0, "data": {"id": "t1", "status": 0, "completedCount": 5}},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/tasks/t1",
            json={"code": 0, "data": {"id": "t1", "status": 1, "completedCount": 10}},
            status=200,
        )

        sleeps = []
        result = tasks_mod.wait_for(
            backend, "p1", "t1",
            poll_interval=0.01, timeout=10,
            sleep_func=sleeps.append,
            now_func=lambda: 0,  # constant time so we never time out
        )
        assert result["status"] == 1
        assert sleeps == [0.01]  # one sleep between the two GETs

    def test_wait_for_times_out(self, backend):
        @responses.activate
        def _run():
            # Always return processing status.
            responses.add(
                responses.GET,
                f"{BASE}/api/projects/p1/tasks/t1",
                json={"code": 0, "data": {"id": "t1", "status": 0}},
                status=200,
            )
            # Add several copies so polling doesn't run out of mock responses.
            for _ in range(10):
                responses.add(
                    responses.GET,
                    f"{BASE}/api/projects/p1/tasks/t1",
                    json={"code": 0, "data": {"id": "t1", "status": 0}},
                    status=200,
                )

            # Fake clock that advances 5s per now() call so we deadline-out fast.
            t = [0.0]

            def fake_now():
                t[0] += 5.0
                return t[0]

            with pytest.raises(TimeoutError, match="did not finish"):
                tasks_mod.wait_for(
                    backend, "p1", "t1",
                    poll_interval=0.01, timeout=3.0,
                    sleep_func=lambda _: None,
                    now_func=fake_now,
                )
        _run()

    def test_task_types_includes_known(self):
        for t in (
            "question-generation",
            "answer-generation",
            "data-cleaning",
            "data-distillation",
            "model-evaluation",
        ):
            assert t in tasks_mod.TASK_TYPES


# ──────────────────────────────────────────────────────────────────────
# Round 4: per-chunk CRUD + clean + batch edit
# ──────────────────────────────────────────────────────────────────────


class TestChunksCrud:
    @responses.activate
    def test_get_chunk(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/chunks/c1",
            json={"id": "c1", "content": "hello", "size": 5},
            status=200,
        )
        result = chunks_mod.get_chunk(backend, "p1", "c1")
        assert result["content"] == "hello"

    @responses.activate
    def test_update_chunk_uses_patch(self, backend):
        responses.add(
            responses.PATCH,
            f"{BASE}/api/projects/p1/chunks/c1",
            json={"id": "c1", "content": "new"},
            status=200,
        )
        chunks_mod.update_chunk(backend, "p1", "c1", content="new")
        body = json.loads(responses.calls[0].request.body)
        assert body == {"content": "new"}

    def test_update_chunk_rejects_non_string(self, backend):
        with pytest.raises(ValueError, match="content"):
            chunks_mod.update_chunk(backend, "p1", "c1", content=42)

    @responses.activate
    def test_delete_chunk(self, backend):
        responses.add(
            responses.DELETE,
            f"{BASE}/api/projects/p1/chunks/c1",
            json={"message": "deleted"},
            status=200,
        )
        chunks_mod.delete_chunk(backend, "p1", "c1")
        assert responses.calls[0].request.method == "DELETE"

    @responses.activate
    def test_clean_chunk_body_shape(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/chunks/c1/clean",
            json={"chunkId": "c1", "originalLength": 100, "cleanedLength": 80, "success": True},
            status=200,
        )
        chunks_mod.clean_chunk(
            backend, "p1", "c1",
            model={"id": "mc1", "modelId": "gpt"}, language="English",
        )
        body = json.loads(responses.calls[0].request.body)
        assert body == {"model": {"id": "mc1", "modelId": "gpt"}, "language": "English"}

    @responses.activate
    def test_batch_edit_chunks_body_shape(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/chunks/batch-edit",
            json={"success": True, "updatedCount": 2},
            status=200,
        )
        chunks_mod.batch_edit_chunks(
            backend, "p1",
            chunk_ids=["c1", "c2"], position="start", content="HEADER",
        )
        body = json.loads(responses.calls[0].request.body)
        assert body == {"chunkIds": ["c1", "c2"], "position": "start", "content": "HEADER"}

    def test_batch_edit_rejects_bad_position(self, backend):
        with pytest.raises(ValueError, match="position"):
            chunks_mod.batch_edit_chunks(
                backend, "p1", chunk_ids=["c1"], position="middle", content="x",
            )

    def test_batch_edit_rejects_empty_chunk_ids(self, backend):
        with pytest.raises(ValueError, match="non-empty"):
            chunks_mod.batch_edit_chunks(
                backend, "p1", chunk_ids=[], position="start", content="x",
            )

    @responses.activate
    def test_batch_content_lookup(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/chunks/batch-content",
            json={"chunk-1": "content A", "chunk-2": "content B"},
            status=200,
        )
        chunks_mod.batch_content(backend, "p1", chunk_names=["chunk-1", "chunk-2"])
        body = json.loads(responses.calls[0].request.body)
        assert body == {"chunkNames": ["chunk-1", "chunk-2"]}


# ──────────────────────────────────────────────────────────────────────
# Round 4: questions CRUD + filtering
# ──────────────────────────────────────────────────────────────────────


class TestQuestionsCrud:
    @responses.activate
    def test_list_passes_filters(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/questions",
            json={"items": [], "total": 0, "page": 1, "size": 50},
            status=200,
        )
        questions_mod.list_questions(
            backend, "p1",
            status="answered",
            chunk_name="chunk-1",
            source_type="text",
            input_keyword="energy",
            search_match_mode="match",
            page=1,
            size=50,
        )
        url = responses.calls[0].request.url
        assert "status=answered" in url
        assert "chunkName=chunk-1" in url
        assert "sourceType=text" in url
        assert "input=energy" in url
        assert "searchMatchMode=match" in url

    @responses.activate
    def test_list_unwraps_items_key(self, backend):
        responses.add(
            responses.GET,
            f"{BASE}/api/projects/p1/questions",
            json={"items": [{"id": "q1"}], "total": 1},
            status=200,
        )
        result = questions_mod.list_questions(backend, "p1", page=1, size=10)
        assert isinstance(result, dict)
        assert len(result["items"]) == 1

    def test_list_rejects_bad_status(self, backend):
        with pytest.raises(ValueError, match="status"):
            questions_mod.list_questions(backend, "p1", status="pending")

    def test_list_rejects_bad_source_type(self, backend):
        with pytest.raises(ValueError, match="source_type"):
            questions_mod.list_questions(backend, "p1", source_type="audio")

    def test_list_rejects_bad_match_mode(self, backend):
        with pytest.raises(ValueError, match="search_match_mode"):
            questions_mod.list_questions(backend, "p1", search_match_mode="exact")

    @responses.activate
    def test_create_question_chunk_source(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/questions",
            json={"id": "q-new", "question": "what?"},
            status=200,
        )
        questions_mod.create_question(
            backend, "p1",
            question="What is gravity?",
            chunk_id="c1",
            label="physics/forces",
        )
        body = json.loads(responses.calls[0].request.body)
        assert body["question"] == "What is gravity?"
        assert body["chunkId"] == "c1"
        assert body["label"] == "physics/forces"
        assert body["projectId"] == "p1"

    @responses.activate
    def test_create_question_image_source(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/questions",
            json={"id": "q-img"},
            status=200,
        )
        questions_mod.create_question(
            backend, "p1",
            question="What car is this?",
            image_id="img-1",
        )
        body = json.loads(responses.calls[0].request.body)
        assert body["imageId"] == "img-1"
        assert "chunkId" not in body

    def test_create_question_rejects_empty(self, backend):
        with pytest.raises(ValueError, match="non-empty"):
            questions_mod.create_question(backend, "p1", question="")

    @responses.activate
    def test_update_question_puts_full_object(self, backend):
        responses.add(
            responses.PUT,
            f"{BASE}/api/projects/p1/questions",
            json={"id": "q1", "question": "edited"},
            status=200,
        )
        questions_mod.update_question(
            backend, "p1",
            {"id": "q1", "question": "edited", "label": "x"},
        )
        body = json.loads(responses.calls[0].request.body)
        assert body == {"id": "q1", "question": "edited", "label": "x"}

    def test_update_question_requires_id(self, backend):
        with pytest.raises(ValueError, match="containing 'id'"):
            questions_mod.update_question(backend, "p1", {"question": "no id"})

    @responses.activate
    def test_delete_question(self, backend):
        responses.add(
            responses.DELETE,
            f"{BASE}/api/projects/p1/questions/q1",
            json={"success": True},
            status=200,
        )
        questions_mod.delete_question(backend, "p1", "q1")
        assert responses.calls[0].request.method == "DELETE"


# ──────────────────────────────────────────────────────────────────────
# Round 4: datasets import + optimize + load_records_from_file
# ──────────────────────────────────────────────────────────────────────


class TestDatasetsImportOptimize:
    def test_load_json_array(self, tmp_path):
        f = tmp_path / "in.json"
        f.write_text(
            json.dumps([
                {"question": "q1", "answer": "a1"},
                {"question": "q2", "answer": "a2"},
            ]),
            encoding="utf-8",
        )
        records = datasets_mod.load_records_from_file(str(f))
        assert len(records) == 2
        assert records[0]["question"] == "q1"

    def test_load_jsonl(self, tmp_path):
        f = tmp_path / "in.jsonl"
        f.write_text(
            '{"question":"q1","answer":"a1"}\n'
            '{"question":"q2","answer":"a2"}\n',
            encoding="utf-8",
        )
        records = datasets_mod.load_records_from_file(str(f))
        assert len(records) == 2

    def test_load_csv(self, tmp_path):
        f = tmp_path / "in.csv"
        f.write_text(
            "question,answer\n"
            "What is X?,X is a thing\n"
            "What is Y?,Y is another thing\n",
            encoding="utf-8",
        )
        records = datasets_mod.load_records_from_file(str(f))
        assert len(records) == 2
        assert records[0]["answer"] == "X is a thing"

    def test_load_with_mapping_renames_columns(self, tmp_path):
        f = tmp_path / "in.json"
        f.write_text(
            json.dumps([
                {"instruction": "q1", "output": "a1"},
                {"instruction": "q2", "output": "a2"},
            ]),
            encoding="utf-8",
        )
        records = datasets_mod.load_records_from_file(
            str(f), mapping={"instruction": "question", "output": "answer"},
        )
        assert len(records) == 2
        assert records[0]["question"] == "q1"
        assert records[0]["answer"] == "a1"
        assert "instruction" not in records[0]

    def test_load_filters_records_missing_required_fields(self, tmp_path):
        f = tmp_path / "in.json"
        f.write_text(
            json.dumps([
                {"question": "q1", "answer": "a1"},
                {"question": "q2"},                       # no answer
                {"answer": "a3"},                         # no question
                {"question": "q4", "answer": ""},         # empty answer
            ]),
            encoding="utf-8",
        )
        records = datasets_mod.load_records_from_file(str(f))
        assert len(records) == 1
        assert records[0]["question"] == "q1"

    def test_load_rejects_unsupported_extension(self, tmp_path):
        f = tmp_path / "in.xlsx"
        f.write_bytes(b"PK")
        with pytest.raises(ValueError, match="Unsupported file type"):
            datasets_mod.load_records_from_file(str(f))

    def test_load_rejects_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            datasets_mod.load_records_from_file(str(tmp_path / "nope.json"))

    def test_load_rejects_non_array_json(self, tmp_path):
        f = tmp_path / "in.json"
        f.write_text(json.dumps({"question": "q1", "answer": "a1"}), encoding="utf-8")
        with pytest.raises(ValueError, match="JSON array"):
            datasets_mod.load_records_from_file(str(f))

    def test_load_jsonl_reports_bad_line(self, tmp_path):
        f = tmp_path / "in.jsonl"
        f.write_text(
            '{"question":"q1","answer":"a1"}\n'
            'not json\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="not valid JSON"):
            datasets_mod.load_records_from_file(str(f))

    @responses.activate
    def test_import_records_posts_inline_json(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/datasets/import",
            json={"success": 2, "total": 2, "failed": 0, "skipped": 0, "errors": []},
            status=200,
        )
        result = datasets_mod.import_records(
            backend, "p1",
            records=[
                {"question": "q1", "answer": "a1"},
                {"question": "q2", "answer": "a2"},
            ],
        )
        body = json.loads(responses.calls[0].request.body)
        assert "datasets" in body
        assert len(body["datasets"]) == 2
        assert result["success"] == 2

    def test_import_records_rejects_empty(self, backend):
        with pytest.raises(ValueError, match="empty"):
            datasets_mod.import_records(backend, "p1", records=[])

    def test_import_records_rejects_non_list(self, backend):
        with pytest.raises(ValueError, match="list"):
            datasets_mod.import_records(backend, "p1", records={"a": 1})

    @responses.activate
    def test_optimize_body_shape(self, backend):
        responses.add(
            responses.POST,
            f"{BASE}/api/projects/p1/datasets/optimize",
            json={"success": True, "dataset": {"id": "d1", "answer": "improved"}},
            status=200,
        )
        datasets_mod.optimize(
            backend, "p1", "d1",
            advice="be more concise",
            model={"id": "mc1", "modelId": "gpt"},
            language="en",
        )
        body = json.loads(responses.calls[0].request.body)
        assert body["datasetId"] == "d1"
        assert body["advice"] == "be more concise"
        assert body["language"] == "en"
        assert body["model"]["modelId"] == "gpt"

    def test_optimize_rejects_empty_advice(self, backend):
        with pytest.raises(ValueError, match="non-empty"):
            datasets_mod.optimize(
                backend, "p1", "d1",
                advice="   ", model={"id": "mc1"},
            )
