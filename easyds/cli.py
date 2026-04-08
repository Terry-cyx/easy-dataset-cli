"""easyds — Click CLI + REPL for Easy-Dataset.

Global flags
------------
--base-url URL    Override Easy-Dataset server URL (default $EDS_BASE_URL or http://localhost:1717)
--project ID      Override active project id
--json            Machine-readable JSON output (no banners, no colors)

Default behavior with no subcommand: drops into the ReplSkin REPL.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import traceback
from typing import Any

import click

from easyds import __version__
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
)
from easyds.utils.repl_skin import ReplSkin


# ── Global state plumbed through Click's context ──────────────────────


class AppCtx:
    def __init__(self, base_url: str | None, project_arg: str | None, json_mode: bool):
        self.json_mode = json_mode
        self.project_arg = project_arg
        self.backend = EasyDatasetBackend(base_url=base_url)
        self.skin = ReplSkin("easyds", version=__version__)

    def project_id(self) -> str:
        return session_mod.resolve_project_id(self.project_arg)

    def emit(self, payload: Any, *, human_label: str | None = None):
        """Print a result either as JSON or human-friendly text."""
        if self.json_mode:
            # ensure_ascii=True so non-ASCII chars become \uXXXX escapes —
            # stdout encoding (GBK on Chinese Windows) cannot crash on chars
            # like 'ə' (\u0259). Agents that pipe stdout into json.loads
            # round-trip the original text losslessly.
            click.echo(json.dumps(payload, ensure_ascii=True, indent=2, default=str))
            return
        if human_label:
            self.skin.success(human_label)
        if isinstance(payload, dict):
            for k, v in payload.items():
                self.skin.status(str(k), str(v))
        elif isinstance(payload, list):
            if not payload:
                self.skin.hint("(empty)")
                return
            if isinstance(payload[0], dict):
                # Build a small table from common keys
                first = payload[0]
                headers = [k for k in ("id", "name", "status", "fileName") if k in first]
                if not headers:
                    headers = list(first.keys())[:4]
                rows = [[str(item.get(h, "")) for h in headers] for item in payload]
                self.skin.table(headers, rows)
            else:
                for item in payload:
                    click.echo(f"  - {item}")
        else:
            click.echo(str(payload))


def _handle_errors(fn):
    """Decorator: convert backend exceptions into clean CLI errors."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        ctx: click.Context = click.get_current_context()
        app: AppCtx = ctx.obj
        try:
            return fn(*args, **kwargs)
        except BackendUnavailable as e:
            if app.json_mode:
                click.echo(
                    json.dumps({"error": "BackendUnavailable", "message": str(e)}),
                    err=True,
                )
            else:
                app.skin.error("Easy-Dataset server is not reachable.")
                click.echo(str(e), err=True)
            sys.exit(2)
        except BackendError as e:
            if app.json_mode:
                click.echo(
                    json.dumps({"error": "BackendError", "message": str(e)}), err=True
                )
            else:
                app.skin.error(str(e))
            sys.exit(3)
        except (session_mod.NoProjectSelected, session_mod.NoModelConfigSelected) as e:
            if app.json_mode:
                click.echo(
                    json.dumps({"error": type(e).__name__, "message": str(e)}), err=True
                )
            else:
                app.skin.error(str(e))
            sys.exit(4)
        except Exception as e:
            if app.json_mode:
                click.echo(
                    json.dumps({"error": type(e).__name__, "message": str(e)}), err=True
                )
            else:
                app.skin.error(f"{type(e).__name__}: {e}")
                if os.environ.get("EDS_TRACEBACK"):
                    traceback.print_exc()
            sys.exit(1)

    return wrapper


# ── Main group ─────────────────────────────────────────────────────────


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--base-url", default=None, help="Easy-Dataset server URL.")
@click.option("--project", "project_arg", default=None, help="Active project id.")
@click.option("--json", "json_mode", is_flag=True, help="Machine-readable JSON output.")
@click.version_option(version=__version__, prog_name="easyds")
@click.pass_context
def cli(ctx: click.Context, base_url: str | None, project_arg: str | None, json_mode: bool):
    """Stateful CLI for Easy-Dataset (https://github.com/ConardLi/easy-dataset)."""
    ctx.obj = AppCtx(base_url=base_url, project_arg=project_arg, json_mode=json_mode)
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl_cmd)


# ── status ─────────────────────────────────────────────────────────────


@cli.command("status")
@click.pass_obj
@_handle_errors
def status_cmd(app: AppCtx):
    """Check Easy-Dataset server reachability and current session."""
    health = app.backend.check_health()
    sess = session_mod.load_session()
    payload = {
        "base_url": health["base_url"],
        "server_status": "ok" if health["ok"] else f"http {health['status_code']}",
        "current_project_id": sess.get("current_project_id"),
        "current_project_name": sess.get("current_project_name"),
        "current_model_config_id": sess.get("current_model_config_id"),
    }
    if app.json_mode:
        click.echo(json.dumps(payload, indent=2))
    else:
        app.skin.section("Easy-Dataset")
        for k, v in payload.items():
            app.skin.status(k, str(v) if v is not None else "(unset)")


# ── project group ──────────────────────────────────────────────────────


@cli.group("project")
def project_grp():
    """Manage Easy-Dataset projects."""


@project_grp.command("new")
@click.option("--name", required=True)
@click.option("--description", default="")
@click.pass_obj
@_handle_errors
def project_new(app: AppCtx, name: str, description: str):
    """Create a new project and remember it as the current one."""
    result = project_mod.create(app.backend, name=name, description=description)
    pid = result.get("id") or (result.get("data") or {}).get("id")
    if pid:
        session_mod.set_current_project(pid, project_name=name)
    app.emit(result, human_label=f"Created project {pid}")


@project_grp.command("list")
@click.pass_obj
@_handle_errors
def project_list(app: AppCtx):
    """List all projects."""
    app.emit(project_mod.list_all(app.backend))


@project_grp.command("info")
@click.pass_obj
@_handle_errors
def project_info(app: AppCtx):
    """Show detail of the current project."""
    app.emit(project_mod.get(app.backend, app.project_id()))


@project_grp.command("use")
@click.argument("project_id")
@click.pass_obj
@_handle_errors
def project_use(app: AppCtx, project_id: str):
    """Set the active project for subsequent commands."""
    info = project_mod.get(app.backend, project_id)
    name = info.get("name") if isinstance(info, dict) else None
    session_mod.set_current_project(project_id, project_name=name)
    app.emit({"current_project_id": project_id, "name": name}, human_label="Active project set")


@project_grp.command("delete")
@click.argument("project_id")
@click.pass_obj
@_handle_errors
def project_delete(app: AppCtx, project_id: str):
    """Delete a project from the server."""
    result = project_mod.delete(app.backend, project_id)
    app.emit(result or {"deleted": project_id}, human_label="Deleted")


# ── model group ────────────────────────────────────────────────────────


@cli.group("model")
def model_grp():
    """Manage per-project LLM model configurations."""


@model_grp.command("set")
@click.option("--provider-id", required=True, help="Provider id, e.g. 'openai'.")
@click.option("--provider-name", default=None, help="Display name (defaults to provider-id).")
@click.option("--endpoint", required=True)
@click.option("--api-key", required=True)
@click.option("--model-id", required=True, help="Model identifier sent to the provider.")
@click.option("--model-name", default=None)
@click.option(
    "--type", "model_type",
    type=click.Choice(list(model_mod.VALID_MODEL_TYPES)),
    default="text",
    show_default=True,
    help="Model type: 'text' for LLMs, 'vision' for VLMs (used by image workflows).",
)
@click.option("--temperature", default=0.7, type=float)
@click.option("--max-tokens", default=4096, type=int)
@click.option("--top-p", default=0.9, type=float, show_default=True,
              help="Server schema requires topP; default 0.9 matches Easy-Dataset.")
@click.pass_obj
@_handle_errors
def model_set(
    app: AppCtx,
    provider_id: str,
    provider_name: str | None,
    endpoint: str,
    api_key: str,
    model_id: str,
    model_name: str | None,
    model_type: str,
    temperature: float,
    max_tokens: int,
    top_p: float,
):
    """Register an LLM (text) or VLM (vision) model config for the current project."""
    pid = app.project_id()
    result = model_mod.set_config(
        app.backend,
        pid,
        provider_id=provider_id,
        provider_name=provider_name or provider_id,
        endpoint=endpoint,
        api_key=api_key,
        model_id=model_id,
        model_name=model_name,
        model_type=model_type,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
    )
    config_id = result.get("id") if isinstance(result, dict) else None
    if config_id:
        session_mod.set_current_model_config(config_id)
    app.emit(result, human_label="Model config saved")


@model_grp.command("list")
@click.pass_obj
@_handle_errors
def model_list(app: AppCtx):
    """List model configs for the current project."""
    app.emit(model_mod.list_configs(app.backend, app.project_id()))


@model_grp.command("use")
@click.argument("model_config_id")
@click.option(
    "--server/--no-server",
    default=True,
    help="Also PUT defaultModelConfigId on the server. Required for "
    "GA-generation and any other endpoint that calls getActiveModel(projectId) "
    "without a request-body model. Default on.",
)
@click.pass_obj
@_handle_errors
def model_use(app: AppCtx, model_config_id: str, server: bool):
    """Set the active model config id (locally + on the server)."""
    session_mod.set_current_model_config(model_config_id)
    payload: dict[str, Any] = {"current_model_config_id": model_config_id}
    if server:
        try:
            project_mod.set_default_model(
                app.backend, app.project_id(), model_config_id
            )
            payload["server_default_model_config_id"] = model_config_id
        except Exception as e:  # noqa: BLE001
            payload["server_update_error"] = str(e)
    app.emit(payload, human_label="Active model set")


# ── files group ────────────────────────────────────────────────────────


@cli.group("files")
def files_grp():
    """Upload, list, and delete source documents."""


@files_grp.command("upload")
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False, resolve_path=True))
@click.pass_obj
@_handle_errors
def files_upload(app: AppCtx, file_path: str):
    """Upload one document to the current project."""
    app.emit(
        files_mod.upload(app.backend, app.project_id(), file_path),
        human_label=f"Uploaded {os.path.basename(file_path)}",
    )


@files_grp.command("list")
@click.pass_obj
@_handle_errors
def files_list(app: AppCtx):
    """List uploaded files."""
    app.emit(files_mod.list_files(app.backend, app.project_id()))


@files_grp.command("delete")
@click.argument("file_id")
@click.pass_obj
@_handle_errors
def files_delete(app: AppCtx, file_id: str):
    """Delete a file (cascades to its chunks and questions)."""
    result = files_mod.delete_file(app.backend, app.project_id(), file_id)
    app.emit(result or {"deleted": file_id}, human_label="Deleted")


@files_grp.command("import")
@click.option("--type", "source_type",
              type=click.Choice(["image"]),
              required=True,
              help="Source type. Currently only 'image' is supported.")
@click.option("--dir", "directory", type=click.Path(exists=True, file_okay=False),
              default=None,
              help="Local directory of images. Recursively zipped and uploaded "
                   "via /images/zip-import.")
@click.option("--from-pdf", "pdf_path", type=click.Path(exists=True, dir_okay=False),
              default=None,
              help="Local PDF. Server converts pages to images via /images/pdf-convert.")
@click.pass_obj
@_handle_errors
def files_import(
    app: AppCtx,
    source_type: str,
    directory: str | None,
    pdf_path: str | None,
):
    """Bulk-import images: a whole directory (案例 1) or a PDF as page images (案例 5)."""
    if (directory is None) == (pdf_path is None):
        raise click.UsageError("provide exactly one of --dir or --from-pdf")
    pid = app.project_id()
    if directory:
        result = files_mod.import_image_directory(app.backend, pid, directory)
        app.emit(result, human_label=f"Imported images from {directory}")
    else:
        result = files_mod.import_pdf_as_images(app.backend, pid, pdf_path)
        app.emit(result, human_label=f"Converted {os.path.basename(pdf_path)} to page images")


@files_grp.command("list-images")
@click.pass_obj
@_handle_errors
def files_list_images(app: AppCtx):
    """List images imported into the current project."""
    app.emit(files_mod.list_images(app.backend, app.project_id()))


@files_grp.command("prune")
@click.option("--id", "image_ids", multiple=True, required=True,
              help="Image id to delete (repeatable).")
@click.pass_obj
@_handle_errors
def files_prune(app: AppCtx, image_ids: tuple[str, ...]):
    """Delete one or more images by id (案例 5 noise pruning)."""
    pid = app.project_id()
    deleted = []
    for img_id in image_ids:
        files_mod.delete_image(app.backend, pid, img_id)
        deleted.append(img_id)
    app.emit({"deleted": deleted}, human_label=f"Pruned {len(deleted)} image(s)")


# ── chunks group ───────────────────────────────────────────────────────


@cli.group("chunks")
def chunks_grp():
    """Split files into chunks and inspect the chunk store."""


@chunks_grp.command("split")
@click.option("--file", "file_names", multiple=True, required=True, help="File name(s) to split.")
@click.option("--model-config", "model_config_id", default=None)
@click.option(
    "--strategy",
    type=click.Choice(list(chunks_mod.VALID_STRATEGIES)),
    default="document",
    show_default=True,
    help="Chunking strategy. Server reads chunk-size params from project task config; "
         "use --text-split-min/--text-split-max to override.",
)
@click.option("--text-split-min", type=int, default=None,
              help="Override project task config textSplitMinLength (default 1500).")
@click.option("--text-split-max", type=int, default=None,
              help="Override project task config textSplitMaxLength (default 2000).")
@click.option("--separator", default=None,
              help="Custom separator string. Routes through /custom-split. "
                   "Requires --content-file (the local copy of the uploaded file).")
@click.option("--content-file", type=click.Path(exists=True, dir_okay=False),
              default=None,
              help="Local path to the file's content (only used with --separator).")
@click.option("--language", default=None)
@click.pass_obj
@_handle_errors
def chunks_split(
    app: AppCtx,
    file_names: tuple[str, ...],
    model_config_id: str | None,
    strategy: str,
    text_split_min: int | None,
    text_split_max: int | None,
    separator: str | None,
    content_file: str | None,
    language: str | None,
):
    """Run chunking + domain-tree generation on uploaded files.

    Default flow uses /api/projects/{id}/split (LLM-driven document strategy).
    When --separator is given, the CLI computes split positions from the
    local content file and routes through /custom-split instead — this is
    how 案例 2 ('---------') and 案例 4 ('## 第') are reproduced.
    """
    pid = app.project_id()

    # Separator-based custom split
    if separator is not None:
        if not content_file:
            raise click.UsageError(
                "--separator requires --content-file (the local copy of the "
                "file's content). Easy-Dataset has no public file-content GET "
                "endpoint, so the CLI must read the file locally to compute "
                "split positions."
            )
        if len(file_names) != 1:
            raise click.UsageError(
                "--separator works on exactly one file at a time."
            )
        target_name = file_names[0]
        # Look up fileId from the server's file list
        listed = files_mod.list_files(app.backend, pid)
        match = next(
            (f for f in listed if isinstance(f, dict) and f.get("fileName") == target_name),
            None,
        )
        if not match:
            raise click.UsageError(
                f"file {target_name!r} not found in project {pid}. "
                f"Run 'easyds files list' to see what's uploaded."
            )
        with open(content_file, "r", encoding="utf-8") as fh:
            content = fh.read()
        result = chunks_mod.custom_split_by_separator(
            app.backend,
            pid,
            file_id=match["id"],
            file_name=target_name,
            content=content,
            separator=separator,
        )
        app.emit(result, human_label=f"Custom split by {separator!r} complete")
        return

    # Default LLM-driven split — must resolve file names → {fileName, fileId}
    # objects (server destructures both from each entry)
    mid = session_mod.resolve_model_config_id(model_config_id)
    file_objects = chunks_mod.resolve_file_objects(app.backend, pid, list(file_names))
    result = chunks_mod.split(
        app.backend,
        pid,
        files=file_objects,
        model_config_id=mid,
        text_split_min=text_split_min,
        text_split_max=text_split_max,
        language=language,
    )
    app.emit(result, human_label=f"Split complete (strategy={strategy})")


@chunks_grp.command("list")
@click.pass_obj
@_handle_errors
def chunks_list(app: AppCtx):
    """List all chunks in the current project."""
    app.emit(chunks_mod.list_chunks(app.backend, app.project_id()))


@chunks_grp.command("get")
@click.argument("chunk_id")
@click.pass_obj
@_handle_errors
def chunks_get(app: AppCtx, chunk_id: str):
    """Show one chunk's full content."""
    app.emit(chunks_mod.get_chunk(app.backend, app.project_id(), chunk_id))


@chunks_grp.command("edit")
@click.argument("chunk_id")
@click.option("--content", "inline_content", default=None,
              help="Inline replacement content.")
@click.option("--file", "content_file", type=click.Path(exists=True, dir_okay=False),
              default=None, help="Read replacement content from a local file.")
@click.pass_obj
@_handle_errors
def chunks_edit(
    app: AppCtx, chunk_id: str, inline_content: str | None, content_file: str | None
):
    """Overwrite a chunk's content via PATCH."""
    if (inline_content is None) == (content_file is None):
        raise click.UsageError("provide exactly one of --content or --file")
    if content_file:
        with open(content_file, "r", encoding="utf-8") as fh:
            content = fh.read()
    else:
        content = inline_content or ""
    app.emit(
        chunks_mod.update_chunk(
            app.backend, app.project_id(), chunk_id, content=content,
        ),
        human_label=f"Chunk {chunk_id} updated",
    )


@chunks_grp.command("delete")
@click.argument("chunk_id")
@click.pass_obj
@_handle_errors
def chunks_delete(app: AppCtx, chunk_id: str):
    """Delete one chunk."""
    result = chunks_mod.delete_chunk(app.backend, app.project_id(), chunk_id)
    app.emit(result or {"deleted": chunk_id}, human_label="Deleted")


@chunks_grp.command("clean")
@click.argument("chunk_id")
@click.option("--model-config", "model_config_id", default=None)
@click.option("--language", default="中文", show_default=True)
@click.option("--prompt-file", type=click.Path(exists=True, dir_okay=False),
              default=None,
              help="Save this file as the project's dataClean prompt before "
                   "running. The endpoint reads the project-level prompt; "
                   "there is no inline prompt parameter.")
@click.option("--prompt-language", default="zh-CN", show_default=True,
              help="Language for the saved prompt (only with --prompt-file).")
@click.pass_obj
@_handle_errors
def chunks_clean(
    app: AppCtx,
    chunk_id: str,
    model_config_id: str | None,
    language: str,
    prompt_file: str | None,
    prompt_language: str,
):
    """Run the data-cleaning prompt against one chunk (案例 4 D6)."""
    pid = app.project_id()
    if prompt_file:
        with open(prompt_file, "r", encoding="utf-8") as fh:
            content = fh.read()
        prompts_mod.save_prompt(
            app.backend, pid,
            prompt_type="dataClean",
            prompt_key="DATA_CLEAN_PROMPT" if prompt_language == "zh-CN" else "DATA_CLEAN_PROMPT_EN",
            language=prompt_language,
            content=content,
        )
    mid = session_mod.resolve_model_config_id(model_config_id)
    configs = model_mod.list_configs(app.backend, pid)
    model_obj = next((c for c in configs if isinstance(c, dict) and c.get("id") == mid), None)
    if not model_obj:
        raise click.UsageError(
            f"current model config {mid!r} not found in project."
        )
    result = chunks_mod.clean_chunk(
        app.backend, pid, chunk_id, model=model_obj, language=language,
    )
    app.emit(result, human_label=f"Chunk {chunk_id} cleaned")


@chunks_grp.command("batch-edit")
@click.option("--chunk", "chunk_ids", multiple=True, required=True,
              help="Chunk id (repeatable).")
@click.option("--position", type=click.Choice(list(chunks_mod.VALID_BATCH_POSITIONS)),
              required=True, help="Where to insert: start (prepend) or end (append).")
@click.option("--content", "inline_content", default=None)
@click.option("--file", "content_file", type=click.Path(exists=True, dir_okay=False),
              default=None)
@click.pass_obj
@_handle_errors
def chunks_batch_edit(
    app: AppCtx,
    chunk_ids: tuple[str, ...],
    position: str,
    inline_content: str | None,
    content_file: str | None,
):
    """Prepend or append text to many chunks at once."""
    if (inline_content is None) == (content_file is None):
        raise click.UsageError("provide exactly one of --content or --file")
    if content_file:
        with open(content_file, "r", encoding="utf-8") as fh:
            content = fh.read()
    else:
        content = inline_content or ""
    app.emit(
        chunks_mod.batch_edit_chunks(
            app.backend, app.project_id(),
            chunk_ids=list(chunk_ids), position=position, content=content,
        ),
        human_label=f"Edited {len(chunk_ids)} chunk(s) ({position})",
    )


# ── questions group ────────────────────────────────────────────────────


@cli.group("questions")
def questions_grp():
    """Generate and inspect questions."""


@questions_grp.command("generate")
@click.option("--chunk", "chunk_ids", multiple=True, help="Chunk id (repeatable). Empty = all chunks.")
@click.option("--image", "image_ids", multiple=True, help="Image id (repeatable, only with --source image). Empty = all images.")
@click.option("--source", type=click.Choice(list(questions_mod.VALID_SOURCES)),
              default="chunk", show_default=True,
              help="Source type. 'image' auto-selects the project's vision model.")
@click.option("--model-config", "model_config_id", default=None)
@click.option("--ga", "enable_ga", is_flag=True, help="Enable Genre/Audience expansion.")
@click.option("--language", default="en")
@click.pass_obj
@_handle_errors
def questions_generate(
    app: AppCtx,
    chunk_ids: tuple[str, ...],
    image_ids: tuple[str, ...],
    source: str,
    model_config_id: str | None,
    enable_ga: bool,
    language: str,
):
    """Generate questions from chunks (text) or images (VQA, 案例 1)."""
    pid = app.project_id()

    if source == "image":
        # Auto-select a vision-type model unless --model-config was explicit.
        if model_config_id is None:
            configs = model_mod.list_configs(app.backend, pid)
            vision = model_mod.find_config_by_type(configs, "vision")
            if not vision:
                raise click.UsageError(
                    "no vision-type model config registered. "
                    "Run 'easyds model set --type vision ...' first."
                )
            mid = vision.get("id")
        else:
            mid = session_mod.resolve_model_config_id(model_config_id)

        if not image_ids:
            imgs = files_mod.list_images(app.backend, pid)
            image_ids = tuple(
                i["id"] for i in imgs if isinstance(i, dict) and "id" in i
            )
        if not image_ids:
            raise click.UsageError(
                "no images in project. Run 'easyds files import --type image --dir ...'."
            )
        result = questions_mod.generate(
            app.backend, pid, [], mid,
            enable_ga_expansion=enable_ga,
            language=language,
            source="image",
            image_ids=list(image_ids),
        )
        app.emit(result, human_label=f"Generated VQA questions for {len(image_ids)} image(s)")
        return

    mid = session_mod.resolve_model_config_id(model_config_id)
    if not chunk_ids:
        all_chunks = chunks_mod.list_chunks(app.backend, pid)
        chunk_ids = tuple(c["id"] for c in all_chunks if isinstance(c, dict) and "id" in c)
    app.emit(
        questions_mod.generate(
            app.backend, pid, list(chunk_ids), mid,
            enable_ga_expansion=enable_ga, language=language, source="chunk",
        ),
        human_label=f"Generated questions for {len(chunk_ids)} chunk(s)",
    )


@questions_grp.command("list")
@click.option("--status",
              type=click.Choice(list(questions_mod.VALID_STATUS_FILTERS)),
              default=None,
              help="Filter by answered status.")
@click.option("--source-type", "source_type",
              type=click.Choice(list(questions_mod.VALID_SOURCE_FILTERS)),
              default=None)
@click.option("--chunk-name", "chunk_name", default=None)
@click.option("--input", "input_keyword", default=None,
              help="Search keyword in question text.")
@click.option("--match-mode", "search_match_mode",
              type=click.Choice(list(questions_mod.VALID_MATCH_MODES)),
              default=None)
@click.option("--page", type=int, default=None)
@click.option("--size", type=int, default=None)
@click.option("--all", "all_records", is_flag=True,
              help="Return every question (no pagination).")
@click.pass_obj
@_handle_errors
def questions_list(
    app: AppCtx,
    status: str | None,
    source_type: str | None,
    chunk_name: str | None,
    input_keyword: str | None,
    search_match_mode: str | None,
    page: int | None,
    size: int | None,
    all_records: bool,
):
    """List questions with rich filters (status / chunk / source-type / search)."""
    app.emit(questions_mod.list_questions(
        app.backend, app.project_id(),
        status=status, source_type=source_type,
        chunk_name=chunk_name, input_keyword=input_keyword,
        search_match_mode=search_match_mode,
        page=page, size=size, all_records=all_records,
    ))


@questions_grp.command("create")
@click.option("--question", required=True)
@click.option("--chunk", "chunk_id", default=None)
@click.option("--image", "image_id", default=None)
@click.option("--label", default=None, help="Domain-tag classification.")
@click.pass_obj
@_handle_errors
def questions_create(
    app: AppCtx,
    question: str,
    chunk_id: str | None,
    image_id: str | None,
    label: str | None,
):
    """Manually create one question (text or image source)."""
    if not chunk_id and not image_id:
        raise click.UsageError("provide either --chunk or --image as the source")
    result = questions_mod.create_question(
        app.backend, app.project_id(),
        question=question, chunk_id=chunk_id, image_id=image_id, label=label,
    )
    app.emit(result, human_label="Question created")


@questions_grp.command("edit")
@click.argument("question_id")
@click.option("--question", "new_question", default=None)
@click.option("--label", default=None)
@click.pass_obj
@_handle_errors
def questions_edit(
    app: AppCtx,
    question_id: str,
    new_question: str | None,
    label: str | None,
):
    """Update one question's text and/or label.

    The server's PUT endpoint expects the entire question object, so we first
    GET the row from the list (using selectedAll false), patch the fields the
    user gave, then PUT it back.
    """
    if new_question is None and label is None:
        raise click.UsageError("provide --question and/or --label")
    pid = app.project_id()
    # Find the row by id within the unpaginated list — questions endpoint
    # has no GET-by-id route.
    result = questions_mod.list_questions(app.backend, pid, all_records=True)
    items = result.get("items", result) if isinstance(result, dict) else result
    target = next((q for q in items if isinstance(q, dict) and q.get("id") == question_id), None)
    if not target:
        raise click.UsageError(f"question {question_id!r} not found in project")
    if new_question is not None:
        target["question"] = new_question
    if label is not None:
        target["label"] = label
    app.emit(
        questions_mod.update_question(app.backend, pid, target),
        human_label="Question updated",
    )


@questions_grp.command("delete")
@click.argument("question_id")
@click.pass_obj
@_handle_errors
def questions_delete(app: AppCtx, question_id: str):
    """Delete one question."""
    result = questions_mod.delete_question(app.backend, app.project_id(), question_id)
    app.emit(result or {"deleted": question_id}, human_label="Deleted")


# ── questions template subgroup ───────────────────────────────────────


@questions_grp.group("template")
def questions_template_grp():
    """Manage reusable question templates (案例 1 / 案例 2)."""


@questions_template_grp.command("create")
@click.option("--question", required=True, help="The prompt question to apply to every source.")
@click.option("--source-type", type=click.Choice(list(templates_mod.VALID_SOURCE_TYPES)),
              required=True, help="image (VQA) or text (chunk) source.")
@click.option(
    "--type", "answer_type",
    type=click.Choice(["text", "label", "json-schema", "custom_format"]),
    required=True,
    help="Answer shape: text (free-form), label (discrete labels), json-schema/custom_format (structured).",
)
@click.option("--label-set", default=None,
              help="Comma-separated label list for --type label, e.g. '正面,负面,中性'.")
@click.option("--schema-file", type=click.Path(exists=True, dir_okay=False),
              default=None,
              help="Local JSON Schema file for --type json-schema.")
@click.option("--custom-format", default=None,
              help="Inline custom format spec (alternative to --schema-file).")
@click.option("--description", default="")
@click.option("--auto-generate", is_flag=True,
              help="Immediately materialize a Question per matching source after creation.")
@click.pass_obj
@_handle_errors
def template_create(
    app: AppCtx,
    question: str,
    source_type: str,
    answer_type: str,
    label_set: str | None,
    schema_file: str | None,
    custom_format: str | None,
    description: str,
    auto_generate: bool,
):
    """Create a question template that can be applied across many sources."""
    answer_type = templates_mod.normalize_answer_type(answer_type)
    labels = templates_mod.parse_label_set(label_set) if label_set else None

    if answer_type == "custom_format":
        if schema_file and custom_format:
            raise click.UsageError("provide only one of --schema-file or --custom-format")
        if schema_file:
            custom_format = templates_mod.load_schema_from_file(schema_file)
        if not custom_format:
            raise click.UsageError(
                "--type json-schema requires --schema-file or --custom-format"
            )

    result = templates_mod.create_template(
        app.backend,
        app.project_id(),
        question=question,
        source_type=source_type,
        answer_type=answer_type,
        description=description,
        labels=labels,
        custom_format=custom_format,
        auto_generate=auto_generate,
    )
    app.emit(result, human_label="Template created")


@questions_template_grp.command("list")
@click.option("--source-type", type=click.Choice(list(templates_mod.VALID_SOURCE_TYPES)),
              default=None)
@click.option("--search", default=None)
@click.pass_obj
@_handle_errors
def template_list(app: AppCtx, source_type: str | None, search: str | None):
    """List question templates for the current project."""
    app.emit(
        templates_mod.list_templates(
            app.backend, app.project_id(),
            source_type=source_type, search=search,
        )
    )


@questions_template_grp.command("get")
@click.argument("template_id")
@click.pass_obj
@_handle_errors
def template_get(app: AppCtx, template_id: str):
    """Show one template by id."""
    app.emit(templates_mod.get_template(app.backend, app.project_id(), template_id))


@questions_template_grp.command("delete")
@click.argument("template_id")
@click.pass_obj
@_handle_errors
def template_delete(app: AppCtx, template_id: str):
    """Delete a template."""
    result = templates_mod.delete_template(app.backend, app.project_id(), template_id)
    app.emit(result or {"deleted": template_id}, human_label="Template deleted")


@questions_template_grp.command("apply")
@click.argument("template_id")
@click.option("--all", "apply_all", is_flag=True, default=True,
              help="Apply to every matching source (default).")
@click.pass_obj
@_handle_errors
def template_apply(app: AppCtx, template_id: str, apply_all: bool):
    """Re-trigger materialization of a template across all matching sources.

    Equivalent to setting autoGenerate=True on update_template.
    """
    result = templates_mod.update_template(
        app.backend, app.project_id(), template_id, autoGenerate=True,
    )
    app.emit(result, human_label="Template re-applied")


# ── datasets group ─────────────────────────────────────────────────────


@cli.group("datasets")
def datasets_grp():
    """Generate, list, and confirm answer datasets."""


@datasets_grp.command("generate")
@click.option("--question", "question_ids", multiple=True, help="Question id (repeatable). Empty = all unanswered.")
@click.option("--model-config", "model_config_id", default=None)
@click.option("--language", default="en")
@click.option("--rounds", type=int, default=None,
              help="Generate a multi-turn dialogue dataset with N rounds (案例 3). "
                   "Routes to /dataset-conversations.")
@click.option("--role-a", default="用户", show_default=True,
              help="Multi-turn role A (only with --rounds).")
@click.option("--role-b", default="助手", show_default=True,
              help="Multi-turn role B (only with --rounds).")
@click.option("--system-prompt-file", type=click.Path(exists=True, dir_okay=False),
              default=None, help="Read multi-turn system prompt from a file.")
@click.option("--system-prompt", "system_prompt_inline", default=None,
              help="Inline multi-turn system prompt (alternative to --system-prompt-file).")
@click.option("--scenario", default="", help="Multi-turn scenario hint.")
@click.pass_obj
@_handle_errors
def datasets_generate(
    app: AppCtx,
    question_ids: tuple[str, ...],
    model_config_id: str | None,
    language: str,
    rounds: int | None,
    role_a: str,
    role_b: str,
    system_prompt_file: str | None,
    system_prompt_inline: str | None,
    scenario: str,
):
    """Generate {answer, cot} for one or many questions.

    With ``--rounds N`` switches to multi-turn dialogue mode and routes to
    /api/projects/{id}/dataset-conversations (case 3 — physics tutor).
    """
    pid = app.project_id()
    mid = session_mod.resolve_model_config_id(model_config_id)

    # ── multi-turn branch ──────────────────────────────────────────
    if rounds is not None:
        if not question_ids:
            raise click.UsageError(
                "--rounds requires at least one --question id "
                "(multi-turn datasets are generated per question)."
            )
        if system_prompt_file and system_prompt_inline:
            raise click.UsageError(
                "provide only one of --system-prompt-file or --system-prompt"
            )
        if system_prompt_file:
            with open(system_prompt_file, "r", encoding="utf-8") as fh:
                system_prompt = fh.read()
        else:
            system_prompt = system_prompt_inline or ""

        # Resolve full model config object the conversations endpoint expects
        configs = model_mod.list_configs(app.backend, pid)
        model_obj = next((c for c in configs if isinstance(c, dict) and c.get("id") == mid), None)
        if not model_obj:
            raise click.UsageError(
                f"current model config {mid!r} not found in project. "
                f"Run 'easyds model list' or 'easyds model use <id>'."
            )

        results = []
        for qid in question_ids:
            results.append(
                datasets_mod.generate_multi_turn(
                    app.backend, pid,
                    question_id=qid,
                    model=model_obj,
                    system_prompt=system_prompt,
                    scenario=scenario,
                    rounds=rounds,
                    role_a=role_a,
                    role_b=role_b,
                    language=language,
                )
            )
        app.emit(
            results,
            human_label=f"Generated {len(results)} multi-turn dialogue dataset(s) ({rounds} rounds each)",
        )
        return

    # ── single-turn branch (original behavior) ─────────────────────
    if not question_ids:
        qs = questions_mod.list_questions(app.backend, pid)
        question_ids = tuple(
            q["id"] for q in qs if isinstance(q, dict) and "id" in q and not q.get("answered")
        )
    results = []
    for qid in question_ids:
        results.append(datasets_mod.generate(app.backend, pid, qid, mid, language=language))
    app.emit(results, human_label=f"Generated {len(results)} dataset record(s)")


@datasets_grp.command("conversations-list")
@click.option("--role-a", default=None)
@click.option("--role-b", default=None)
@click.option("--keyword", default=None)
@click.option("--page", type=int, default=1, show_default=True)
@click.option("--page-size", type=int, default=50, show_default=True)
@click.pass_obj
@_handle_errors
def datasets_conversations_list(
    app: AppCtx,
    role_a: str | None,
    role_b: str | None,
    keyword: str | None,
    page: int,
    page_size: int,
):
    """List multi-turn dialogue datasets in the current project."""
    app.emit(
        datasets_mod.list_conversations(
            app.backend, app.project_id(),
            role_a=role_a, role_b=role_b, keyword=keyword,
            page=page, page_size=page_size,
        )
    )


@datasets_grp.command("list")
@click.option("--confirmed/--all", "confirmed_only", default=False)
@click.option("--score-gte", type=float, default=None,
              help="Only include datasets with score >= N (0–5).")
@click.option("--score-lte", type=float, default=None,
              help="Only include datasets with score <= N (0–5).")
@click.option("--tag", "custom_tag", default=None, help="Filter by custom tag.")
@click.option("--note", "note_keyword", default=None, help="Filter by note keyword.")
@click.option("--chunk", "chunk_name", default=None, help="Filter by source chunk name.")
@click.option("--page", type=int, default=1, show_default=True)
@click.option("--page-size", type=int, default=50, show_default=True)
@click.pass_obj
@_handle_errors
def datasets_list(
    app: AppCtx,
    confirmed_only: bool,
    score_gte: float | None,
    score_lte: float | None,
    custom_tag: str | None,
    note_keyword: str | None,
    chunk_name: str | None,
    page: int,
    page_size: int,
):
    """List datasets with rich filtering (score, tag, note, chunk)."""
    app.emit(
        datasets_mod.list_datasets(
            app.backend,
            app.project_id(),
            confirmed=True if confirmed_only else None,
            score_gte=score_gte,
            score_lte=score_lte,
            custom_tag=custom_tag,
            note_keyword=note_keyword,
            chunk_name=chunk_name,
            page=page,
            page_size=page_size,
        )
    )


@datasets_grp.command("evaluate")
@click.option("--dataset", "dataset_id", default=None,
              help="Evaluate a single dataset by id. Omit to run a batch task.")
@click.option("--prompt-file", type=click.Path(exists=True, dir_okay=False),
              default=None,
              help="Local file with the custom evaluation prompt. The CLI "
                   "uploads it via custom-prompts before invoking evaluation.")
@click.option("--language", default="zh-CN", show_default=True)
@click.option("--no-validate", is_flag=True,
              help="Skip {{var}} placeholder validation on the prompt file.")
@click.pass_obj
@_handle_errors
def datasets_evaluate(
    app: AppCtx,
    dataset_id: str | None,
    prompt_file: str | None,
    language: str,
    no_validate: bool,
):
    """Run quality evaluation on a single dataset, or kick off a batch job.

    Server-side scoring is multi-dimensional (问题质量 / 答案质量 / 文本相关性 /
    整体一致性, 0-5 with 0.5 step) per Easy-Dataset's datasetEvaluation prompt.

    Pass --prompt-file to override the default datasetEvaluation prompt for
    this project before evaluation runs. The override is persisted via the
    custom-prompts API and stays in effect for future evaluations until you
    'easyds prompts reset' it.
    """
    pid = app.project_id()

    if prompt_file:
        with open(prompt_file, "r", encoding="utf-8") as fh:
            content = fh.read()
        prompts_mod.save_prompt(
            app.backend,
            pid,
            prompt_type="datasetEvaluation",
            prompt_key="DATASET_EVALUATION_PROMPT" if language == "zh-CN" else "DATASET_EVALUATION_PROMPT_EN",
            language=language,
            content=content,
            validate=not no_validate,
        )

    # Build the model config object the API expects.
    configs = model_mod.list_configs(app.backend, pid)
    mid = session_mod.resolve_model_config_id(None)
    model_obj = next((c for c in configs if isinstance(c, dict) and c.get("id") == mid), None)
    if not model_obj:
        raise click.UsageError(
            f"current model config {mid!r} not found in project. "
            f"Run 'easyds model list' or 'easyds model use <id>'."
        )

    if dataset_id:
        result = datasets_mod.evaluate(
            app.backend, pid, dataset_id, model=model_obj, language=language
        )
        app.emit(result, human_label=f"Evaluated dataset {dataset_id}")
    else:
        result = datasets_mod.batch_evaluate(
            app.backend, pid, model=model_obj, language=language
        )
        app.emit(result, human_label="Batch evaluation task created")


@datasets_grp.command("confirm")
@click.argument("dataset_id")
@click.pass_obj
@_handle_errors
def datasets_confirm(app: AppCtx, dataset_id: str):
    """Mark a dataset record as confirmed."""
    app.emit(
        datasets_mod.update(app.backend, app.project_id(), dataset_id, confirmed=True),
        human_label="Confirmed",
    )


@datasets_grp.command("import")
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False, resolve_path=True))
@click.option("--mapping", "mapping_specs", multiple=True,
              help="Rename a source column on import, e.g. "
                   "--mapping instruction=question --mapping output=answer. Repeatable.")
@click.pass_obj
@_handle_errors
def datasets_import(
    app: AppCtx,
    file_path: str,
    mapping_specs: tuple[str, ...],
):
    """Import pre-existing datasets from a JSON / JSONL / CSV file.

    The file is parsed client-side, optionally remapped via --mapping, then
    POSTed as inline JSON to the server's /datasets/import route. Records
    missing question or answer (after mapping) are filtered out and counted
    in the response under 'skipped'.
    """
    mapping = export_mod.parse_field_map(mapping_specs) if mapping_specs else None
    records = datasets_mod.load_records_from_file(file_path, mapping=mapping)
    if not records:
        raise click.UsageError(
            f"{file_path} produced 0 valid records (need question + answer "
            f"after mapping). Aborting."
        )
    result = datasets_mod.import_records(
        app.backend, app.project_id(), records=records,
    )
    app.emit(
        result,
        human_label=f"Imported {len(records)} record(s) from {os.path.basename(file_path)}",
    )


@datasets_grp.command("optimize")
@click.argument("dataset_id")
@click.option("--advice", required=True,
              help="User instruction for the LLM (e.g. 'be more concise', "
                   "'add a worked example'). Becomes part of the optimize prompt.")
@click.option("--language", default="zh-CN", show_default=True)
@click.option("--model-config", "model_config_id", default=None)
@click.pass_obj
@_handle_errors
def datasets_optimize(
    app: AppCtx,
    dataset_id: str,
    advice: str,
    language: str,
    model_config_id: str | None,
):
    """Re-generate one dataset's answer + CoT with user advice (魔法棒 / G4)."""
    pid = app.project_id()
    mid = session_mod.resolve_model_config_id(model_config_id)
    configs = model_mod.list_configs(app.backend, pid)
    model_obj = next((c for c in configs if isinstance(c, dict) and c.get("id") == mid), None)
    if not model_obj:
        raise click.UsageError(
            f"current model config {mid!r} not found in project."
        )
    result = datasets_mod.optimize(
        app.backend, pid, dataset_id,
        advice=advice, model=model_obj, language=language,
    )
    app.emit(result, human_label=f"Optimized dataset {dataset_id}")


# ── export group ───────────────────────────────────────────────────────


@cli.group("export")
def export_grp():
    """Export datasets to disk."""


@export_grp.command("run")
@click.option("-o", "--output", "output_path", required=True, type=click.Path())
@click.option("--format", "fmt", default="alpaca", type=click.Choice(sorted(export_mod.VALID_FORMATS)))
@click.option("--all/--confirmed-only", "include_all", default=False)
@click.option("--overwrite", is_flag=True)
@click.option("--score-gte", type=float, default=None,
              help="Only export datasets with score >= N (0–5).")
@click.option("--score-lte", type=float, default=None,
              help="Only export datasets with score <= N (0–5).")
@click.option("--file-type", type=click.Choice(list(export_mod.VALID_FILE_TYPES)),
              default="json", show_default=True,
              help="Output file format. Server always returns JSON; csv/jsonl "
                   "are serialized client-side from the same record list.")
@click.option("--field-map", "field_map_specs", multiple=True,
              help="Rename a column on the way out, e.g. "
                   "--field-map question=instruction --field-map answer=output. Repeatable.")
@click.option("--include-chunk", is_flag=True,
              help="Embed source chunkContent + chunkName in every record.")
@click.option("--include-image-path", is_flag=True,
              help="Unwrap imagePath from the dataset's `other` JSON column "
                   "and surface it as a top-level field (案例 1 / 5).")
@click.option("--include-cot", is_flag=True,
              help="Embed the model's chain-of-thought as <think>{cot}</think> "
                   "before each answer (matches the GUI's includeCOT toggle).")
@click.option("--system-prompt", default="",
              help="Alpaca `system` field / ShareGPT system message. Empty by default.")
@click.option("--reasoning-language", default="English", show_default=True,
              help="Only used by --format multilingual-thinking.")
@click.option("--split", "split_spec", default=None,
              help="Train/valid/test ratio, e.g. '0.7,0.15,0.15' or '70,15,15'. "
                   "Writes three files: <output>-train.<ext>, -valid.<ext>, -test.<ext>. "
                   "Split is deterministic by record id (re-running gives the same buckets).")
@click.pass_obj
@_handle_errors
def export_run(
    app: AppCtx,
    output_path: str,
    fmt: str,
    include_all: bool,
    overwrite: bool,
    score_gte: float | None,
    score_lte: float | None,
    file_type: str,
    field_map_specs: tuple[str, ...],
    include_chunk: bool,
    include_image_path: bool,
    include_cot: bool,
    system_prompt: str,
    reasoning_language: str,
    split_spec: str | None,
):
    """Export the current project's datasets to a file.

    All client-side post-processing happens in this order: enrich
    (--include-chunk / --include-image-path) → rename (--field-map) → split
    → serialize to --file-type. The server's export route only ever returns
    JSON; csv / jsonl / split / mapping are CLI features.
    """
    field_map = export_mod.parse_field_map(field_map_specs) if field_map_specs else None
    split = export_mod.parse_split_ratio(split_spec) if split_spec else None
    result = export_mod.run(
        app.backend,
        app.project_id(),
        output_path=output_path,
        fmt=fmt,
        confirmed_only=not include_all,
        overwrite=overwrite,
        score_gte=score_gte,
        score_lte=score_lte,
        file_type=file_type,
        field_map=field_map,
        include_chunk=include_chunk,
        include_image_path=include_image_path,
        include_cot=include_cot,
        system_prompt=system_prompt,
        reasoning_language=reasoning_language,
        split=split,
    )
    app.emit(result, human_label="Export written")


@export_grp.command("conversations")
@click.option("-o", "--output", "output_path", required=True, type=click.Path())
@click.option("--format", "fmt", default="sharegpt",
              type=click.Choice(sorted(export_mod.MULTI_TURN_FORMATS)),
              show_default=True,
              help="Multi-turn datasets ONLY support ShareGPT (spec/04 §L9).")
@click.option("--overwrite", is_flag=True)
@click.pass_obj
@_handle_errors
def export_conversations_cmd(
    app: AppCtx,
    output_path: str,
    fmt: str,
    overwrite: bool,
):
    """Export multi-turn dialogue datasets to ShareGPT JSON (案例 3)."""
    result = export_mod.export_conversations(
        app.backend,
        app.project_id(),
        output_path=output_path,
        fmt=fmt,
        overwrite=overwrite,
    )
    app.emit(result, human_label="Conversations exported")


# ── distill group ──────────────────────────────────────────────────────


@cli.group("distill")
def distill_grp():
    """Zero-shot dataset distillation (no source documents required)."""


def _load_label_tree(path: str) -> dict[str, Any]:
    """Load a YAML or JSON label tree from disk.

    Falls back to JSON when PyYAML isn't installed — keeps the dependency set
    minimal. The expected shape is ``{"name": str, "children": [ ... ]}``.
    """
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        return json.loads(text)


@distill_grp.command("auto")
@click.option("--label-tree-file", type=click.Path(exists=True, dir_okay=False),
              default=None, help="YAML or JSON label tree file.")
@click.option("--root-topic", default=None,
              help="Root topic when no tree file is given. Server will expand "
                   "the tree via /distill/tags up to --levels.")
@click.option("--levels", type=int, default=2, show_default=True,
              help="Tree expansion depth (only with --root-topic).")
@click.option("--tags-per-level", type=int, default=10, show_default=True)
@click.option("--questions-per-leaf", type=int, default=5, show_default=True)
@click.option("--language", default="zh", show_default=True)
@click.option("--rounds", type=int, default=None,
              help="If --type multi, number of dialogue rounds per question.")
@click.option("--type", "distill_type",
              type=click.Choice(["single", "multi"]), default="single",
              show_default=True,
              help="single = answer per question; multi = multi-turn dialogue per question.")
@click.option("--role-a", default="用户", show_default=True)
@click.option("--role-b", default="助手", show_default=True)
@click.option("--system-prompt-file", type=click.Path(exists=True, dir_okay=False),
              default=None,
              help="Multi-turn system prompt file (only with --type multi).")
@click.option("--model-config", "model_config_id", default=None)
@click.pass_obj
@_handle_errors
def distill_auto(
    app: AppCtx,
    label_tree_file: str | None,
    root_topic: str | None,
    levels: int,
    tags_per_level: int,
    questions_per_leaf: int,
    language: str,
    rounds: int | None,
    distill_type: str,
    role_a: str,
    role_b: str,
    system_prompt_file: str | None,
    model_config_id: str | None,
):
    """One-shot zero-shot distillation (案例 3 — physics multi-turn corpus).

    Walks a user-supplied label tree (or expands one from --root-topic) and
    chains /distill/tags + /distill/questions, then optionally generates
    multi-turn dialogues per question via --type multi.
    """
    if (label_tree_file is None) == (root_topic is None):
        raise click.UsageError("provide exactly one of --label-tree-file or --root-topic")
    if distill_type == "multi" and rounds is None:
        raise click.UsageError("--type multi requires --rounds")

    pid = app.project_id()
    mid = session_mod.resolve_model_config_id(model_config_id)

    # The distill endpoints take a full model config object.
    configs = model_mod.list_configs(app.backend, pid)
    model_obj = next((c for c in configs if isinstance(c, dict) and c.get("id") == mid), None)
    if not model_obj:
        raise click.UsageError(
            f"current model config {mid!r} not found in project. "
            f"Run 'easyds model use <id>'."
        )

    if label_tree_file:
        tree = _load_label_tree(label_tree_file)
        summary = distill_mod.run_auto(
            app.backend, pid,
            label_tree=tree,
            model=model_obj,
            questions_per_leaf=questions_per_leaf,
            language=language,
        )
    else:
        summary = distill_mod.run_auto_expand(
            app.backend, pid,
            root_topic=root_topic,
            model=model_obj,
            levels=levels,
            tags_per_level=tags_per_level,
            questions_per_leaf=questions_per_leaf,
            language=language,
        )

    # Optional multi-turn pass: turn every distilled question into a dialogue.
    if distill_type == "multi":
        if system_prompt_file:
            with open(system_prompt_file, "r", encoding="utf-8") as fh:
                system_prompt = fh.read()
        else:
            system_prompt = ""

        # Re-list questions and convert each one into a multi-turn dialogue.
        all_questions = questions_mod.list_questions(app.backend, pid)
        qids = [q["id"] for q in all_questions if isinstance(q, dict) and "id" in q]
        multi_results = []
        for qid in qids:
            multi_results.append(
                datasets_mod.generate_multi_turn(
                    app.backend, pid,
                    question_id=qid,
                    model=model_obj,
                    system_prompt=system_prompt,
                    rounds=rounds,
                    role_a=role_a,
                    role_b=role_b,
                    language=language,
                )
            )
        summary["multi_turn_generated"] = len(multi_results)

    app.emit(summary, human_label="Distillation complete")


@distill_grp.group("step")
def distill_step_grp():
    """Step-by-step distillation (debug-friendly)."""


@distill_step_grp.command("tags")
@click.option("--parent-tag", required=True)
@click.option("--tag-path", default=None,
              help="Defaults to --parent-tag if omitted.")
@click.option("--parent-tag-id", default=None)
@click.option("--count", type=int, default=10, show_default=True)
@click.option("--language", default="zh", show_default=True)
@click.option("--model-config", "model_config_id", default=None)
@click.pass_obj
@_handle_errors
def distill_step_tags(
    app: AppCtx,
    parent_tag: str,
    tag_path: str | None,
    parent_tag_id: str | None,
    count: int,
    language: str,
    model_config_id: str | None,
):
    """Single call to /distill/tags."""
    pid = app.project_id()
    mid = session_mod.resolve_model_config_id(model_config_id)
    configs = model_mod.list_configs(app.backend, pid)
    model_obj = next((c for c in configs if isinstance(c, dict) and c.get("id") == mid), None)
    if not model_obj:
        raise click.UsageError(f"current model config {mid!r} not found in project.")
    result = distill_mod.generate_tags(
        app.backend, pid,
        parent_tag=parent_tag,
        tag_path=tag_path or parent_tag,
        parent_tag_id=parent_tag_id,
        count=count,
        model=model_obj,
        language=language,
    )
    app.emit(result, human_label=f"Generated child tags for {parent_tag!r}")


@distill_step_grp.command("questions")
@click.option("--current-tag", required=True)
@click.option("--tag-path", default=None,
              help="Defaults to --current-tag if omitted.")
@click.option("--tag-id", default=None)
@click.option("--count", type=int, default=5, show_default=True)
@click.option("--language", default="zh", show_default=True)
@click.option("--model-config", "model_config_id", default=None)
@click.pass_obj
@_handle_errors
def distill_step_questions(
    app: AppCtx,
    current_tag: str,
    tag_path: str | None,
    tag_id: str | None,
    count: int,
    language: str,
    model_config_id: str | None,
):
    """Single call to /distill/questions."""
    pid = app.project_id()
    mid = session_mod.resolve_model_config_id(model_config_id)
    configs = model_mod.list_configs(app.backend, pid)
    model_obj = next((c for c in configs if isinstance(c, dict) and c.get("id") == mid), None)
    if not model_obj:
        raise click.UsageError(f"current model config {mid!r} not found in project.")
    result = distill_mod.generate_questions(
        app.backend, pid,
        tag_path=tag_path or current_tag,
        current_tag=current_tag,
        tag_id=tag_id,
        count=count,
        model=model_obj,
        language=language,
    )
    app.emit(result, human_label=f"Generated questions for tag {current_tag!r}")


# ── prompts group ──────────────────────────────────────────────────────


@cli.group("prompts")
def prompts_grp():
    """Manage project-level prompt overrides via custom-prompts API."""


@prompts_grp.command("list")
@click.option("--type", "prompt_type", default=None,
              help=f"Filter by promptType (e.g. {', '.join(prompts_mod.KNOWN_PROMPT_TYPES[:5])}…).")
@click.option("--language", default=None,
              help=f"Filter by language ({'|'.join(prompts_mod.KNOWN_LANGUAGES)}).")
@click.pass_obj
@_handle_errors
def prompts_list(app: AppCtx, prompt_type: str | None, language: str | None):
    """List custom prompts for the current project (with optional filters)."""
    result = prompts_mod.list_prompts(
        app.backend, app.project_id(), prompt_type=prompt_type, language=language
    )
    app.emit(result.get("customPrompts", []))


@prompts_grp.command("get")
@click.option("--type", "prompt_type", required=True)
@click.option("--key", "prompt_key", required=True)
@click.option("--language", required=True,
              type=click.Choice(list(prompts_mod.KNOWN_LANGUAGES)))
@click.pass_obj
@_handle_errors
def prompts_get(app: AppCtx, prompt_type: str, prompt_key: str, language: str):
    """Fetch a single custom prompt by (type, key, language)."""
    p = prompts_mod.get_prompt(
        app.backend,
        app.project_id(),
        prompt_type=prompt_type,
        prompt_key=prompt_key,
        language=language,
    )
    if p is None:
        if app.json_mode:
            click.echo(json.dumps(None))
        else:
            app.skin.warning("(no override set — server default in effect)")
        return
    app.emit(p)


@prompts_grp.command("set")
@click.option("--type", "prompt_type", required=True,
              help="promptType, e.g. question / answer / dataClean / datasetEvaluation.")
@click.option("--key", "prompt_key", required=True,
              help="promptKey, e.g. QUESTION_PROMPT, QUESTION_PROMPT_EN.")
@click.option("--language", required=True,
              type=click.Choice(list(prompts_mod.KNOWN_LANGUAGES)))
@click.option("--content", "inline_content", default=None,
              help="Inline prompt body (use --file for longer content).")
@click.option("--file", "content_file", type=click.Path(exists=True, dir_okay=False),
              default=None, help="Read prompt body from a local file.")
@click.option("--require-var", "require_vars", multiple=True,
              help="Require this {{var}} placeholder (repeatable).")
@click.option("--no-validate", is_flag=True,
              help="Skip {{var}} placeholder validation. Use with care.")
@click.pass_obj
@_handle_errors
def prompts_set(
    app: AppCtx,
    prompt_type: str,
    prompt_key: str,
    language: str,
    inline_content: str | None,
    content_file: str | None,
    require_vars: tuple[str, ...],
    no_validate: bool,
):
    """Save a custom prompt override for the current project.

    Validates that the prompt body contains at least one {{var}} placeholder
    by default; pass --no-validate to skip. Use --require-var to mandate
    specific variables (repeatable).
    """
    if (inline_content is None) == (content_file is None):
        raise click.UsageError("provide exactly one of --content or --file")
    if content_file:
        with open(content_file, "r", encoding="utf-8") as fh:
            content = fh.read()
    else:
        content = inline_content or ""

    try:
        result = prompts_mod.save_prompt(
            app.backend,
            app.project_id(),
            prompt_type=prompt_type,
            prompt_key=prompt_key,
            language=language,
            content=content,
            validate=not no_validate,
            required_vars=list(require_vars) if require_vars else None,
        )
    except prompts_mod.TemplateValidationError as e:
        # surface as a clean CLI error, not a stack trace
        if app.json_mode:
            click.echo(
                json.dumps({"error": "TemplateValidationError", "message": str(e)}),
                err=True,
            )
        else:
            app.skin.error(str(e))
        sys.exit(5)
    app.emit(result, human_label=f"Saved {prompt_type}/{prompt_key}/{language}")


@prompts_grp.command("reset")
@click.option("--type", "prompt_type", required=True)
@click.option("--key", "prompt_key", required=True)
@click.option("--language", required=True,
              type=click.Choice(list(prompts_mod.KNOWN_LANGUAGES)))
@click.pass_obj
@_handle_errors
def prompts_reset(app: AppCtx, prompt_type: str, prompt_key: str, language: str):
    """Delete a custom prompt override (revert to server default)."""
    result = prompts_mod.delete_prompt(
        app.backend,
        app.project_id(),
        prompt_type=prompt_type,
        prompt_key=prompt_key,
        language=language,
    )
    app.emit(
        result or {"deleted": True, "promptType": prompt_type, "promptKey": prompt_key},
        human_label="Reset to server default",
    )


# ── eval (benchmark) group ────────────────────────────────────────────


@cli.group("eval")
def eval_grp():
    """Manage the evaluation-dataset benchmark (eval-datasets table)."""


@eval_grp.command("list")
@click.option("--type", "question_type",
              type=click.Choice(list(eval_mod.VALID_QUESTION_TYPES)),
              default=None,
              help="Filter by a single question type.")
@click.option("--keyword", default=None)
@click.option("--chunk", "chunk_id", default=None)
@click.option("--tag", "tags", multiple=True, help="Filter by tag (repeatable).")
@click.option("--page", type=int, default=1, show_default=True)
@click.option("--page-size", type=int, default=50, show_default=True)
@click.option("--include-stats", is_flag=True)
@click.pass_obj
@_handle_errors
def eval_list(
    app: AppCtx,
    question_type: str | None,
    keyword: str | None,
    chunk_id: str | None,
    tags: tuple[str, ...],
    page: int,
    page_size: int,
    include_stats: bool,
):
    """List eval-dataset rows with rich filters."""
    result = eval_mod.list_eval_datasets(
        app.backend, app.project_id(),
        question_type=question_type, keyword=keyword,
        chunk_id=chunk_id, tags=list(tags) if tags else None,
        page=page, page_size=page_size, include_stats=include_stats,
    )
    app.emit(result)


@eval_grp.command("get")
@click.argument("eval_id")
@click.pass_obj
@_handle_errors
def eval_get(app: AppCtx, eval_id: str):
    """Show one eval-dataset row by id."""
    app.emit(eval_mod.get_eval_dataset(app.backend, app.project_id(), eval_id))


@eval_grp.command("count")
@click.option("--type", "question_type",
              type=click.Choice(list(eval_mod.VALID_QUESTION_TYPES)),
              default=None)
@click.option("--keyword", default=None)
@click.option("--tag", "tags", multiple=True)
@click.pass_obj
@_handle_errors
def eval_count(
    app: AppCtx,
    question_type: str | None,
    keyword: str | None,
    tags: tuple[str, ...],
):
    """Show total + per-type breakdown without paging through rows."""
    app.emit(eval_mod.count(
        app.backend, app.project_id(),
        question_type=question_type, keyword=keyword,
        tags=list(tags) if tags else None,
    ))


@eval_grp.command("create")
@click.option("--question", required=True)
@click.option("--type", "question_type",
              type=click.Choice(list(eval_mod.VALID_QUESTION_TYPES)),
              default="short_answer", show_default=True)
@click.option("--option", "options", multiple=True,
              help="One option per --option (required for choice types).")
@click.option("--correct", "correct_answer", required=True,
              help="Correct answer. For choice types, the option text or "
                   "JSON-encoded list (e.g. '[0,2]' for multiple_choice).")
@click.option("--tag", "tag_list", multiple=True)
@click.option("--note", default="")
@click.option("--chunk", "chunk_id", default=None)
@click.pass_obj
@_handle_errors
def eval_create(
    app: AppCtx,
    question: str,
    question_type: str,
    options: tuple[str, ...],
    correct_answer: str,
    tag_list: tuple[str, ...],
    note: str,
    chunk_id: str | None,
):
    """Create one benchmark row."""
    # Try to parse correct_answer as JSON for multiple_choice; fall back to string.
    parsed_correct: Any = correct_answer
    if question_type in eval_mod.CHOICE_TYPES:
        try:
            parsed_correct = json.loads(correct_answer)
        except (json.JSONDecodeError, ValueError):
            parsed_correct = correct_answer
    result = eval_mod.create_eval_dataset(
        app.backend, app.project_id(),
        question=question,
        correct_answer=parsed_correct,
        question_type=question_type,
        options=list(options) if options else None,
        tags=list(tag_list) if tag_list else None,
        note=note,
        chunk_id=chunk_id,
    )
    app.emit(result, human_label="Eval row created")


@eval_grp.command("delete")
@click.option("--id", "eval_ids", multiple=True, required=True,
              help="Eval-dataset id to delete (repeatable).")
@click.pass_obj
@_handle_errors
def eval_delete(app: AppCtx, eval_ids: tuple[str, ...]):
    """Delete one or more eval-dataset rows."""
    pid = app.project_id()
    if len(eval_ids) == 1:
        result = eval_mod.delete_eval_dataset(app.backend, pid, eval_ids[0])
    else:
        result = eval_mod.delete_many(app.backend, pid, list(eval_ids))
    app.emit(result or {"deleted": list(eval_ids)}, human_label="Deleted")


@eval_grp.command("sample")
@click.option("--type", "question_type",
              type=click.Choice(list(eval_mod.VALID_QUESTION_TYPES)),
              default=None)
@click.option("--tag", "tags", multiple=True)
@click.option("--limit", type=int, default=50, show_default=True)
@click.option("--strategy", default="random", show_default=True)
@click.pass_obj
@_handle_errors
def eval_sample(
    app: AppCtx,
    question_type: str | None,
    tags: tuple[str, ...],
    limit: int,
    strategy: str,
):
    """Sample a subset of eval-dataset ids (used to seed an eval task)."""
    result = eval_mod.sample(
        app.backend, app.project_id(),
        question_type=question_type, tags=list(tags) if tags else None,
        limit=limit, strategy=strategy,
    )
    app.emit(result)


@eval_grp.command("export")
@click.option("-o", "--output", "output_path", required=True, type=click.Path())
@click.option("--format", "fmt",
              type=click.Choice(list(eval_mod.VALID_EXPORT_FORMATS)),
              default="json", show_default=True,
              help="Server-side serialization (json / jsonl / csv).")
@click.option("--type", "question_types", multiple=True,
              help="Filter by question type (repeatable).")
@click.option("--tag", "tags", multiple=True)
@click.option("--keyword", default=None)
@click.option("--overwrite", is_flag=True)
@click.pass_obj
@_handle_errors
def eval_export(
    app: AppCtx,
    output_path: str,
    fmt: str,
    question_types: tuple[str, ...],
    tags: tuple[str, ...],
    keyword: str | None,
    overwrite: bool,
):
    """Export the benchmark to disk via the server's streaming export route."""
    result = eval_mod.export(
        app.backend, app.project_id(),
        output_path=output_path, fmt=fmt,
        question_types=list(question_types) if question_types else None,
        tags=list(tags) if tags else None,
        keyword=keyword, overwrite=overwrite,
    )
    app.emit(result, human_label="Eval benchmark exported")


@eval_grp.command("import")
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False, resolve_path=True))
@click.option("--type", "question_type",
              type=click.Choice(list(eval_mod.VALID_QUESTION_TYPES)),
              required=True,
              help="Question type to assign to every imported row.")
@click.option("--tag", "tags", multiple=True)
@click.pass_obj
@_handle_errors
def eval_import(
    app: AppCtx,
    file_path: str,
    question_type: str,
    tags: tuple[str, ...],
):
    """Import a benchmark file (json/jsonl/csv) into eval-datasets."""
    result = eval_mod.import_file(
        app.backend, app.project_id(),
        file_path=file_path, question_type=question_type,
        tags=list(tags) if tags else None,
    )
    app.emit(result, human_label=f"Imported from {os.path.basename(file_path)}")


@eval_grp.command("copy-from-dataset")
@click.argument("dataset_id")
@click.pass_obj
@_handle_errors
def eval_copy_from_dataset(app: AppCtx, dataset_id: str):
    """Promote one SFT dataset row into an eval-dataset row."""
    result = eval_mod.copy_from_dataset(app.backend, app.project_id(), dataset_id)
    app.emit(result, human_label="Copied to eval")


@eval_grp.command("variant")
@click.option("--dataset", "dataset_id", required=True)
@click.option("--type", "question_type",
              type=click.Choice(list(eval_mod.VALID_QUESTION_TYPES)),
              default="single_choice", show_default=True)
@click.option("--count", type=int, default=3, show_default=True)
@click.option("--language", default="zh-CN")
@click.option("--model-config", "model_config_id", default=None)
@click.pass_obj
@_handle_errors
def eval_variant(
    app: AppCtx,
    dataset_id: str,
    question_type: str,
    count: int,
    language: str,
    model_config_id: str | None,
):
    """Generate N evaluation-style variants from one SFT dataset row."""
    pid = app.project_id()
    mid = session_mod.resolve_model_config_id(model_config_id)
    configs = model_mod.list_configs(app.backend, pid)
    model_obj = next((c for c in configs if isinstance(c, dict) and c.get("id") == mid), None)
    if not model_obj:
        raise click.UsageError(f"current model config {mid!r} not found in project.")
    result = eval_mod.generate_variant(
        app.backend, pid,
        dataset_id=dataset_id, model=model_obj,
        question_type=question_type, count=count, language=language,
    )
    app.emit(result, human_label=f"Generated {count} eval variant(s)")


# ── eval-task group ───────────────────────────────────────────────────


@cli.group("eval-task")
def eval_task_grp():
    """Run automated evaluation tasks (multiple test models + judge model)."""


@eval_task_grp.command("run")
@click.option("--model", "models", multiple=True, required=True,
              help="Test model spec 'modelId:providerId' (repeatable).")
@click.option("--eval-id", "eval_ids", multiple=True,
              help="Eval-dataset id to score (repeatable).")
@click.option("--sample-limit", type=int, default=None,
              help="If --eval-id is omitted, sample N rows from the benchmark.")
@click.option("--sample-type", default=None,
              help="When sampling, restrict to one question type.")
@click.option("--judge-model", default=None,
              help="Judge model spec 'modelId:providerId' for subjective scoring.")
@click.option("--language", default="zh-CN", show_default=True)
@click.pass_obj
@_handle_errors
def eval_task_run(
    app: AppCtx,
    models: tuple[str, ...],
    eval_ids: tuple[str, ...],
    sample_limit: int | None,
    sample_type: str | None,
    judge_model: str | None,
    language: str,
):
    """Kick off an evaluation task."""
    pid = app.project_id()

    def _split_spec(spec: str) -> dict[str, str]:
        if ":" not in spec:
            raise click.UsageError(
                f"--model expects 'modelId:providerId', got {spec!r}"
            )
        mid, prov = spec.split(":", 1)
        return {"modelId": mid, "providerId": prov}

    model_specs = [_split_spec(s) for s in models]

    if not eval_ids:
        if sample_limit is None:
            raise click.UsageError(
                "provide either --eval-id ID (repeatable) or --sample-limit N"
            )
        sampled = eval_mod.sample(
            app.backend, pid,
            question_type=sample_type, limit=sample_limit,
        )
        sample_data = sampled.get("data", sampled) if isinstance(sampled, dict) else sampled
        if isinstance(sample_data, dict) and "ids" in sample_data:
            eval_ids = tuple(sample_data["ids"])
        else:
            eval_ids = tuple()
        if not eval_ids:
            raise click.UsageError("eval sample returned 0 rows; nothing to run.")

    judge_kwargs: dict[str, Any] = {}
    if judge_model:
        jmid, jprov = judge_model.split(":", 1) if ":" in judge_model else (judge_model, "")
        judge_kwargs["judge_model_id"] = jmid
        judge_kwargs["judge_provider_id"] = jprov

    result = eval_tasks_mod.create_task(
        app.backend, pid,
        models=model_specs,
        eval_dataset_ids=list(eval_ids),
        language=language,
        **judge_kwargs,
    )
    app.emit(result, human_label=f"Eval task created for {len(model_specs)} model(s)")


@eval_task_grp.command("list")
@click.option("--page", type=int, default=1, show_default=True)
@click.option("--page-size", type=int, default=50, show_default=True)
@click.pass_obj
@_handle_errors
def eval_task_list(app: AppCtx, page: int, page_size: int):
    """List evaluation tasks."""
    app.emit(eval_tasks_mod.list_tasks(
        app.backend, app.project_id(), page=page, page_size=page_size,
    ))


@eval_task_grp.command("get")
@click.argument("task_id")
@click.option("--page", type=int, default=1, show_default=True)
@click.option("--page-size", type=int, default=50, show_default=True)
@click.option("--type", "type_filter", default=None)
@click.option("--correct/--incorrect", "is_correct", default=None)
@click.pass_obj
@_handle_errors
def eval_task_get(
    app: AppCtx,
    task_id: str,
    page: int,
    page_size: int,
    type_filter: str | None,
    is_correct: bool | None,
):
    """Show task header + paginated results."""
    app.emit(eval_tasks_mod.get_task(
        app.backend, app.project_id(), task_id,
        page=page, page_size=page_size,
        type_filter=type_filter, is_correct=is_correct,
    ))


@eval_task_grp.command("interrupt")
@click.argument("task_id")
@click.pass_obj
@_handle_errors
def eval_task_interrupt(app: AppCtx, task_id: str):
    """Interrupt a running evaluation task."""
    app.emit(
        eval_tasks_mod.interrupt_task(app.backend, app.project_id(), task_id),
        human_label="Interrupted",
    )


@eval_task_grp.command("delete")
@click.argument("task_id")
@click.pass_obj
@_handle_errors
def eval_task_delete(app: AppCtx, task_id: str):
    """Delete an evaluation task."""
    app.emit(
        eval_tasks_mod.delete_task(app.backend, app.project_id(), task_id) or {"deleted": task_id},
        human_label="Deleted",
    )


# ── blind-test group ──────────────────────────────────────────────────


@cli.group("blind")
def blind_grp():
    """Pairwise model blind-test tasks (model A vs model B)."""


@blind_grp.command("run")
@click.option("--model-a", required=True, help="'modelId:providerId' for model A.")
@click.option("--model-b", required=True, help="'modelId:providerId' for model B.")
@click.option("--eval-id", "eval_ids", multiple=True)
@click.option("--sample-limit", type=int, default=None)
@click.option("--language", default="zh-CN", show_default=True)
@click.pass_obj
@_handle_errors
def blind_run(
    app: AppCtx,
    model_a: str,
    model_b: str,
    eval_ids: tuple[str, ...],
    sample_limit: int | None,
    language: str,
):
    """Create a blind-test task — server runs both models on every eval row."""
    pid = app.project_id()

    def _split(spec: str) -> dict[str, str]:
        if ":" not in spec:
            raise click.UsageError(f"expected 'modelId:providerId', got {spec!r}")
        mid, prov = spec.split(":", 1)
        return {"modelId": mid, "providerId": prov}

    if not eval_ids:
        if sample_limit is None:
            raise click.UsageError("provide either --eval-id ID or --sample-limit N")
        sampled = eval_mod.sample(app.backend, pid, limit=sample_limit)
        sample_data = sampled.get("data", sampled) if isinstance(sampled, dict) else sampled
        if isinstance(sample_data, dict) and "ids" in sample_data:
            eval_ids = tuple(sample_data["ids"])
        if not eval_ids:
            raise click.UsageError("eval sample returned 0 rows; nothing to run.")

    result = blind_mod.create_task(
        app.backend, pid,
        model_a=_split(model_a),
        model_b=_split(model_b),
        eval_dataset_ids=list(eval_ids),
        language=language,
    )
    app.emit(result, human_label="Blind-test task created")


@blind_grp.command("list")
@click.option("--page", type=int, default=1, show_default=True)
@click.option("--page-size", type=int, default=50, show_default=True)
@click.pass_obj
@_handle_errors
def blind_list(app: AppCtx, page: int, page_size: int):
    """List blind-test tasks."""
    app.emit(blind_mod.list_tasks(
        app.backend, app.project_id(), page=page, page_size=page_size,
    ))


@blind_grp.command("get")
@click.argument("task_id")
@click.pass_obj
@_handle_errors
def blind_get(app: AppCtx, task_id: str):
    """Show task detail (final scores + per-question results)."""
    app.emit(blind_mod.get_task(app.backend, app.project_id(), task_id))


@blind_grp.command("question")
@click.argument("task_id")
@click.pass_obj
@_handle_errors
def blind_question(app: AppCtx, task_id: str):
    """Fetch the next question to vote on (with leftAnswer/rightAnswer)."""
    app.emit(blind_mod.get_current(app.backend, app.project_id(), task_id))


@blind_grp.command("vote")
@click.argument("task_id")
@click.option("--vote", "vote_value",
              type=click.Choice(list(blind_mod.VALID_VOTES)),
              required=True)
@click.option("--question-id", required=True)
@click.option("--is-swapped/--not-swapped", default=False,
              help="Forward the swap flag returned by 'blind question' verbatim.")
@click.option("--left-answer", required=True)
@click.option("--right-answer", required=True)
@click.pass_obj
@_handle_errors
def blind_vote(
    app: AppCtx,
    task_id: str,
    vote_value: str,
    question_id: str,
    is_swapped: bool,
    left_answer: str,
    right_answer: str,
):
    """Submit one vote for a blind-test question."""
    app.emit(
        blind_mod.vote(
            app.backend, app.project_id(), task_id,
            vote_value=vote_value, question_id=question_id,
            is_swapped=is_swapped, left_answer=left_answer, right_answer=right_answer,
        ),
        human_label=f"Voted {vote_value}",
    )


@blind_grp.command("auto-vote")
@click.argument("task_id")
@click.option("--judge-rule",
              type=click.Choice(["always-left", "always-right", "always-tie", "longer", "shorter"]),
              default="longer", show_default=True,
              help="Built-in deterministic judge rule. 'longer'/'shorter' compare "
                   "answer length; the others are constants. Use 'easyds blind vote' "
                   "for human or LLM-driven decisions.")
@click.pass_obj
@_handle_errors
def blind_auto_vote(app: AppCtx, task_id: str, judge_rule: str):
    """Drive a blind-test task to completion using a deterministic rule.

    The voting endpoint is plain HTTP — no GUI required. This command lets
    AI agents and CI smoke tests close the blind-test loop without manual
    interaction. For real human alignment use 'easyds blind question' +
    'easyds blind vote'; for LLM-as-judge wrap an external model call around the
    same loop.
    """
    def _decide(payload: dict[str, Any]) -> str:
        left = payload.get("leftAnswer", "")
        right = payload.get("rightAnswer", "")
        if judge_rule == "always-left":
            return "left"
        if judge_rule == "always-right":
            return "right"
        if judge_rule == "always-tie":
            return "tie"
        if judge_rule == "longer":
            if len(left) == len(right):
                return "tie"
            return "left" if len(left) > len(right) else "right"
        # shorter
        if len(left) == len(right):
            return "tie"
        return "left" if len(left) < len(right) else "right"

    summary = blind_mod.run_manual_loop(
        app.backend, app.project_id(), task_id, vote_callback=_decide,
    )
    app.emit(summary, human_label=f"Auto-voted task {task_id}")


# ── ga (Genre-Audience / MGA) group ───────────────────────────────────


@cli.group("ga")
def ga_grp():
    """Genre-Audience (MGA) pair management for question diversification."""


@ga_grp.command("generate")
@click.option("--file", "file_ids", multiple=True, required=True,
              help="Source file id (repeatable).")
@click.option("--model-config", "model_config_id", default=None)
@click.option("--language", default="中文", show_default=True)
@click.option("--append/--overwrite", "append_mode", default=False,
              help="--append adds to existing pairs; --overwrite replaces them.")
@click.option("--mode", type=click.Choice(list(ga_mod.KNOWN_MODES)), default=None,
              help="strict|loose forward-compat flag. Easy-Dataset's GA prompt "
                   "has no mode switch — passing this prints a warning and is "
                   "otherwise a no-op (see spec/04 §I3).")
@click.pass_obj
@_handle_errors
def ga_generate(
    app: AppCtx,
    file_ids: tuple[str, ...],
    model_config_id: str | None,
    language: str,
    append_mode: bool,
    mode: str | None,
):
    """Generate 5 GA pairs per file via the batch endpoint."""
    if mode and not app.json_mode:
        app.skin.warning(
            f"--mode {mode} is a forward-compat no-op: Easy-Dataset's GA "
            f"prompt does not branch on strict/loose."
        )
    pid = app.project_id()
    mid = session_mod.resolve_model_config_id(model_config_id)
    result = ga_mod.batch_generate(
        app.backend, pid,
        file_ids=list(file_ids), model_config_id=mid,
        language=language, append_mode=append_mode,
    )
    app.emit(result, human_label=f"GA pairs generated for {len(file_ids)} file(s)")


@ga_grp.command("list")
@click.argument("file_id")
@click.pass_obj
@_handle_errors
def ga_list(app: AppCtx, file_id: str):
    """List GA pairs for one file."""
    app.emit(ga_mod.list_pairs(app.backend, app.project_id(), file_id))


@ga_grp.command("add-manual")
@click.option("--file", "file_ids", multiple=True, required=True)
@click.option("--genre-title", required=True)
@click.option("--audience-title", required=True)
@click.option("--genre-desc", default="")
@click.option("--audience-desc", default="")
@click.option("--append/--overwrite", "append_mode", default=True,
              help="Default --append: manual pairs augment the existing set.")
@click.pass_obj
@_handle_errors
def ga_add_manual(
    app: AppCtx,
    file_ids: tuple[str, ...],
    genre_title: str,
    audience_title: str,
    genre_desc: str,
    audience_desc: str,
    append_mode: bool,
):
    """Hand-write a GA pair and attach it to one or more files."""
    result = ga_mod.add_manual(
        app.backend, app.project_id(),
        file_ids=list(file_ids),
        genre_title=genre_title, audience_title=audience_title,
        genre_desc=genre_desc, audience_desc=audience_desc,
        append_mode=append_mode,
    )
    app.emit(result, human_label="Manual GA pair attached")


@ga_grp.command("set-active")
@click.option("--file", "file_id", required=True)
@click.option("--id", "ga_pair_id", required=True)
@click.option("--active/--inactive", "is_active", default=True)
@click.pass_obj
@_handle_errors
def ga_set_active(
    app: AppCtx, file_id: str, ga_pair_id: str, is_active: bool
):
    """Toggle one GA pair on or off."""
    app.emit(
        ga_mod.set_active(
            app.backend, app.project_id(), file_id,
            ga_pair_id=ga_pair_id, is_active=is_active,
        ),
        human_label=f"Set ga_pair {ga_pair_id} {'active' if is_active else 'inactive'}",
    )


@ga_grp.command("estimate")
@click.option("--files", "file_count", type=int, required=True)
@click.option("--questions", "base_question_count", type=int, required=True)
@click.option("--inflation-factor", type=float, default=ga_mod.DEFAULT_INFLATION_FACTOR,
              show_default=True,
              help="Token-inflation multiplier (Easy-Dataset docs cite ~3.9×).")
@click.pass_obj
@_handle_errors
def ga_estimate(
    app: AppCtx,
    file_count: int,
    base_question_count: int,
    inflation_factor: float,
):
    """Client-side cost estimate for an MGA expansion run.

    This does NOT call the server; Easy-Dataset has no token-prediction
    endpoint for GA. The numbers come from the official MGA docs and the
    fixed 5-pairs-per-file constant.
    """
    app.emit(ga_mod.estimate_inflation(
        file_count=file_count,
        base_question_count=base_question_count,
        inflation_factor=inflation_factor,
    ))


# ── tags (domain tree) group ──────────────────────────────────────────


@cli.group("tags")
def tags_grp():
    """Domain-tree tag management (project-level Tags table)."""


@tags_grp.command("list")
@click.option("--flat", is_flag=True, help="Show one label per line instead of nested tree.")
@click.pass_obj
@_handle_errors
def tags_list(app: AppCtx, flat: bool):
    """Show the project's domain tree."""
    tree = tags_mod.list_tags(app.backend, app.project_id())
    if flat:
        app.emit(tags_mod.collect_labels(tree))
    else:
        app.emit(tree)


@tags_grp.command("create")
@click.option("--label", required=True)
@click.option("--parent", "parent_id", default=None,
              help="Parent tag id (omit to create a root node).")
@click.pass_obj
@_handle_errors
def tags_create(app: AppCtx, label: str, parent_id: str | None):
    """Create one tag node."""
    result = tags_mod.save_tag(
        app.backend, app.project_id(), label=label, parent_id=parent_id,
    )
    app.emit(result, human_label=f"Created tag {label!r}")


@tags_grp.command("rename")
@click.argument("tag_id")
@click.option("--label", required=True)
@click.pass_obj
@_handle_errors
def tags_rename(app: AppCtx, tag_id: str, label: str):
    """Rename one tag (id stays the same)."""
    result = tags_mod.save_tag(
        app.backend, app.project_id(), label=label, tag_id=tag_id,
    )
    app.emit(result, human_label=f"Renamed tag {tag_id} → {label!r}")


@tags_grp.command("move")
@click.argument("tag_id")
@click.option("--parent", "parent_id", default=None,
              help="New parent id (omit to move to root).")
@click.pass_obj
@_handle_errors
def tags_move(app: AppCtx, tag_id: str, parent_id: str | None):
    """Reparent one tag (its children come along)."""
    # We need the current label to PUT the row; the server's save endpoint
    # uses (id, label, parentId) as the unit of update.
    tree = tags_mod.list_tags(app.backend, app.project_id())
    node = tags_mod.find_tag(tree, tag_id=tag_id)
    if not node:
        raise click.UsageError(f"tag {tag_id!r} not found in project")
    result = tags_mod.save_tag(
        app.backend, app.project_id(),
        label=node["label"], tag_id=tag_id, parent_id=parent_id,
    )
    app.emit(result, human_label=f"Moved tag {tag_id} under {parent_id or 'root'}")


@tags_grp.command("delete")
@click.argument("tag_id")
@click.pass_obj
@_handle_errors
def tags_delete(app: AppCtx, tag_id: str):
    """Delete one tag and its descendants (cascades to questions + datasets)."""
    result = tags_mod.delete_tag(app.backend, app.project_id(), tag_id)
    app.emit(result or {"deleted": tag_id}, human_label="Tag deleted (cascade)")


@tags_grp.command("questions")
@click.argument("tag_name")
@click.pass_obj
@_handle_errors
def tags_questions(app: AppCtx, tag_name: str):
    """List questions whose label matches a tag."""
    app.emit(tags_mod.get_questions_by_tag(app.backend, app.project_id(), tag_name))


# ── task (background job) group ───────────────────────────────────────


@cli.group("task")
def task_grp():
    """Background task management (Task table, NOT project task-config.json)."""


@task_grp.command("list")
@click.option("--type", "task_type",
              type=click.Choice(list(tasks_mod.TASK_TYPES)),
              default=None)
@click.option("--status", type=int, default=None,
              help=f"Filter by status code "
                   f"({tasks_mod.STATUS_PROCESSING}=processing, "
                   f"{tasks_mod.STATUS_COMPLETED}=completed, "
                   f"{tasks_mod.STATUS_FAILED}=failed, "
                   f"{tasks_mod.STATUS_INTERRUPTED}=interrupted).")
@click.option("--page", type=int, default=0, show_default=True,
              help="0-indexed page number (server convention).")
@click.option("--limit", type=int, default=50, show_default=True)
@click.pass_obj
@_handle_errors
def task_list(
    app: AppCtx,
    task_type: str | None,
    status: int | None,
    page: int,
    limit: int,
):
    """List background tasks with optional filters."""
    app.emit(tasks_mod.list_tasks(
        app.backend, app.project_id(),
        task_type=task_type, status=status, page=page, limit=limit,
    ))


@task_grp.command("get")
@click.argument("task_id")
@click.pass_obj
@_handle_errors
def task_get(app: AppCtx, task_id: str):
    """Show one task's full state."""
    app.emit(tasks_mod.get_task(app.backend, app.project_id(), task_id))


@task_grp.command("cancel")
@click.argument("task_id")
@click.pass_obj
@_handle_errors
def task_cancel(app: AppCtx, task_id: str):
    """Mark a task as INTERRUPTED (the in-process loop will stop on next step)."""
    app.emit(
        tasks_mod.cancel_task(app.backend, app.project_id(), task_id),
        human_label=f"Cancelled task {task_id}",
    )


@task_grp.command("delete")
@click.argument("task_id")
@click.pass_obj
@_handle_errors
def task_delete(app: AppCtx, task_id: str):
    """Delete a task row from the table."""
    result = tasks_mod.delete_task(app.backend, app.project_id(), task_id)
    app.emit(result or {"deleted": task_id}, human_label="Deleted")


@task_grp.command("wait")
@click.argument("task_id")
@click.option("--poll-interval", type=float, default=1.0, show_default=True)
@click.option("--timeout", type=float, default=600.0, show_default=True)
@click.pass_obj
@_handle_errors
def task_wait(
    app: AppCtx, task_id: str, poll_interval: float, timeout: float
):
    """Block until a task reaches a terminal status (or timeout).

    Easy-Dataset processes tasks in-process via setImmediate (no real worker
    queue) — there is no streaming endpoint, so polling is the only option.
    """
    final = tasks_mod.wait_for(
        app.backend, app.project_id(), task_id,
        poll_interval=poll_interval, timeout=timeout,
    )
    app.emit(final, human_label=f"Task {task_id} → {tasks_mod.status_label(final.get('status'))}")


# ── REPL ───────────────────────────────────────────────────────────────


@cli.command("repl")
@click.pass_obj
def repl_cmd(app: AppCtx):
    """Start the interactive REPL (default when no subcommand given)."""
    skin = app.skin
    skin.print_banner()
    pt_session = skin.create_prompt_session()
    sess = session_mod.load_session()
    project_name = sess.get("current_project_name", "")

    while True:
        try:
            line = skin.get_input(pt_session, project_name=project_name)
        except (EOFError, KeyboardInterrupt):
            skin.print_goodbye()
            return

        if not line:
            continue
        if line in ("quit", "exit", "q"):
            skin.print_goodbye()
            return
        if line in ("help", "?"):
            skin.help(
                {
                    "status": "check server health and current session",
                    "project new|list|info|use|delete": "project management",
                    "model set|list|use": "LLM model configuration (--type text|vision)",
                    "files upload|list|delete": "source document management",
                    "files import|list-images|prune": "image / PDF-page imports (案例 1, 案例 5)",
                    "chunks split|list": "chunking and domain tree",
                    "questions generate|list": "question generation (--source chunk|image)",
                    "questions template create|list|get|delete|apply": "reusable question templates",
                    "datasets generate|list|confirm|evaluate": "answer generation + quality scoring",
                    "datasets generate --rounds N": "multi-turn dialogue datasets (案例 3)",
                    "datasets conversations-list": "list multi-turn dialogue datasets",
                    "distill auto|step": "zero-shot distillation (no source docs)",
                    "eval list|get|create|count|sample|export|import|delete": "benchmark eval-dataset CRUD",
                    "eval-task run|list|get|interrupt": "automated multi-model evaluation tasks",
                    "blind run|list|question|vote|auto-vote": "pairwise model blind-test",
                    "ga generate|list|add-manual|set-active|estimate": "Genre-Audience (MGA) pair management",
                    "tags list|create|rename|move|delete|questions": "domain tree (Tags table) editing",
                    "task list|get|cancel|delete|wait": "background task system polling",
                    "chunks clean|edit|delete|batch-edit|get": "per-chunk CRUD + clean (D6)",
                    "datasets import|optimize": "import seed JSON + AI optimize answers (G4 / M1)",
                    "questions list|create|edit|delete": "questions CRUD with rich filters",
                    "prompts list|get|set|reset": "manage project-level prompt overrides",
                    "export run -o FILE": "alpaca/sharegpt + --file-type/--field-map/--split/--include-chunk",
                    "export conversations -o FILE": "export multi-turn dialogues (ShareGPT only)",
                    "quit": "exit the REPL",
                }
            )
            continue

        try:
            args = shlex.split(line)
        except ValueError as e:
            skin.error(f"parse error: {e}")
            continue

        # Re-enter Click with the parsed args, sharing the same global flags.
        try:
            cli.main(args=args, prog_name="easyds", standalone_mode=False)
        except click.exceptions.UsageError as e:
            skin.error(f"usage: {e.format_message()}")
        except SystemExit:
            pass  # subcommand may sys.exit on error; keep REPL alive
        except Exception as e:
            skin.error(f"{type(e).__name__}: {e}")

        # Refresh project_name in case the user switched projects.
        sess = session_mod.load_session()
        project_name = sess.get("current_project_name", "")


# ── Entry point ────────────────────────────────────────────────────────


def main():
    cli(prog_name="easyds")


if __name__ == "__main__":
    main()
