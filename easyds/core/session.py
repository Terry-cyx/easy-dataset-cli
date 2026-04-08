"""Persistent session state for the CLI.

State file: ~/.easyds/session.json

Holds the active base_url, project id, project name, and model-config id so
that successive commands don't have to pass them on every invocation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _session_dir() -> Path:
    return Path.home() / ".easyds"


def session_path() -> Path:
    return _session_dir() / "session.json"


def load_session() -> dict[str, Any]:
    """Read the session file. Returns empty dict if it doesn't exist."""
    p = session_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_session(data: dict[str, Any]) -> None:
    """Atomically write JSON with exclusive file locking.

    Uses ``r+`` + truncate-inside-the-lock so a concurrent reader never sees
    a half-written file. fcntl is unavailable on Windows; we proceed unlocked
    in that case (single-user CLI, no concurrent writers).
    """
    p = session_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    try:
        f = open(p, "r+", encoding="utf-8")
    except FileNotFoundError:
        f = open(p, "w", encoding="utf-8")

    with f:
        _locked = False
        try:
            import fcntl  # type: ignore[import-not-found]
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            _locked = True
        except (ImportError, OSError):
            pass  # Windows or unsupported FS — proceed unlocked
        try:
            f.seek(0)
            f.truncate()
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
        finally:
            if _locked:
                import fcntl  # type: ignore[import-not-found]
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# ── Resolution helpers ────────────────────────────────────────────────

class NoProjectSelected(RuntimeError):
    pass


class NoModelConfigSelected(RuntimeError):
    pass


def resolve_project_id(cli_arg: str | None, session: dict[str, Any] | None = None) -> str:
    """CLI flag > EDS_PROJECT_ID env > session.current_project_id > raise."""
    if cli_arg:
        return cli_arg
    env = os.environ.get("EDS_PROJECT_ID")
    if env:
        return env
    if session is None:
        session = load_session()
    pid = session.get("current_project_id")
    if pid:
        return pid
    raise NoProjectSelected(
        "No project selected. Run 'easyds project use <id>' "
        "or pass --project <id>, or set EDS_PROJECT_ID."
    )


def resolve_model_config_id(
    cli_arg: str | None, session: dict[str, Any] | None = None
) -> str:
    """CLI flag > EDS_MODEL_CONFIG_ID env > session.current_model_config_id > raise."""
    if cli_arg:
        return cli_arg
    env = os.environ.get("EDS_MODEL_CONFIG_ID")
    if env:
        return env
    if session is None:
        session = load_session()
    mid = session.get("current_model_config_id")
    if mid:
        return mid
    raise NoModelConfigSelected(
        "No model config selected. Run 'easyds model use <id>' "
        "or pass --model-config <id>, or set EDS_MODEL_CONFIG_ID."
    )


def set_current_project(project_id: str, project_name: str | None = None) -> None:
    s = load_session()
    s["current_project_id"] = project_id
    if project_name:
        s["current_project_name"] = project_name
    save_session(s)


def set_current_model_config(model_config_id: str) -> None:
    s = load_session()
    s["current_model_config_id"] = model_config_id
    save_session(s)


# ── Eval history ─────────────────────────────────────────────────────
# The datasets-eval feedback loop keeps a tiny rolling log of eval runs
# so an agent retrying the same file can see ("last time we failed on X")
# without having to shell out to a separate store. Scoped per-project.

EVAL_HISTORY_MAX = 20


def append_eval_history(entry: dict[str, Any]) -> None:
    """Record one eval run in the session file (per current project).

    Entry is a free-form dict; callers typically include file,
    file_sha256_prefix, verdict, failing rule names, and a timestamp.
    The list is trimmed to the last ``EVAL_HISTORY_MAX`` records.
    """
    s = load_session()
    history = s.setdefault("eval_history", {})
    pid = s.get("current_project_id", "_no_project_")
    per_proj = history.setdefault(pid, [])
    per_proj.append(entry)
    del per_proj[:-EVAL_HISTORY_MAX]
    save_session(s)


def get_eval_history(project_id: str | None = None) -> list[dict[str, Any]]:
    """Return the eval-history log for a project (default: current)."""
    s = load_session()
    history = s.get("eval_history", {})
    if project_id is None:
        project_id = s.get("current_project_id", "_no_project_")
    return list(history.get(project_id, []))


def set_base_url(base_url: str) -> None:
    s = load_session()
    s["base_url"] = base_url.rstrip("/")
    save_session(s)
