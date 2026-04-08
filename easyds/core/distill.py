"""Zero-shot distillation — wraps /api/projects/{id}/distill/*.

Easy-Dataset's distillation pipeline lets you build a dataset without any
source documents: you provide a top-level topic and a label tree, and the
server generates child labels (via /distill/tags), then questions per leaf
(via /distill/questions), then optionally answers (via /datasets) or multi-turn
conversations (via /dataset-conversations).

This module exposes:

* ``generate_tags`` — single call to /distill/tags
* ``generate_questions`` — single call to /distill/questions
* ``run_auto`` — high-level orchestrator that walks a user-provided label tree
  and chains the two endpoints together.

The orchestration logic is pure tree-walking + sequential HTTP calls — no LLM
calls or distillation algorithms run client-side. Reproduces spec/03 §案例 3
(物理学多轮对话蒸馏数据集).
"""

from __future__ import annotations

from typing import Any, Iterator

from easyds.utils.backend import EasyDatasetBackend


def generate_tags(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    parent_tag: str,
    tag_path: str,
    parent_tag_id: str | None = None,
    count: int = 10,
    model: dict[str, Any],
    language: str = "zh",
) -> dict[str, Any]:
    """POST /api/projects/{id}/distill/tags."""
    body = {
        "parentTag": parent_tag,
        "parentTagId": parent_tag_id,
        "tagPath": tag_path,
        "count": count,
        "model": model,
        "language": language,
    }
    return backend.post(
        f"/api/projects/{project_id}/distill/tags", json_body=body
    )


def generate_questions(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    tag_path: str,
    current_tag: str,
    tag_id: str | None = None,
    count: int = 5,
    model: dict[str, Any],
    language: str = "zh",
) -> dict[str, Any]:
    """POST /api/projects/{id}/distill/questions."""
    body = {
        "tagPath": tag_path,
        "currentTag": current_tag,
        "tagId": tag_id,
        "count": count,
        "model": model,
        "language": language,
    }
    return backend.post(
        f"/api/projects/{project_id}/distill/questions", json_body=body
    )


# ── orchestration ────────────────────────────────────────────────────


def _walk_tree(
    tree: dict[str, Any],
    *,
    parent_path: str = "",
) -> Iterator[tuple[str, str, list[str], bool]]:
    """Walk a nested label-tree dict and yield (tag_path, tag_name, children, is_leaf).

    The expected tree shape is::

        {
            "name": "物理学",
            "children": [
                {"name": "经典力学", "children": [
                    {"name": "牛顿定律"}, {"name": "动量守恒"}
                ]},
                {"name": "电磁学", "children": [...]}
            ]
        }

    A node without ``children`` is a leaf and should drive question generation.
    """
    name = tree["name"]
    path = f"{parent_path}/{name}" if parent_path else name
    children = tree.get("children", []) or []
    child_names = [c["name"] for c in children]
    yield (path, name, child_names, len(children) == 0)
    for child in children:
        yield from _walk_tree(child, parent_path=path)


def run_auto(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    label_tree: dict[str, Any],
    model: dict[str, Any],
    questions_per_leaf: int = 5,
    language: str = "zh",
) -> dict[str, Any]:
    """High-level orchestrator: walk a label tree and generate questions at each leaf.

    Returns a summary dict with ``{tags_called, questions_called,
    leaves_processed, results: [...]}``. Pure orchestration — every actual
    operation is a server-side call.

    To convert the generated questions into multi-turn dialogue datasets,
    follow up with ``datasets.generate_multi_turn`` per question id (the CLI
    'distill auto --type multi' command does this).
    """
    summary = {
        "tags_called": 0,
        "questions_called": 0,
        "leaves_processed": 0,
        "results": [],
    }

    for tag_path, tag_name, children, is_leaf in _walk_tree(label_tree):
        if is_leaf:
            res = generate_questions(
                backend,
                project_id,
                tag_path=tag_path,
                current_tag=tag_name,
                count=questions_per_leaf,
                model=model,
                language=language,
            )
            summary["questions_called"] += 1
            summary["leaves_processed"] += 1
            summary["results"].append(
                {"tag_path": tag_path, "kind": "questions", "result": res}
            )
        else:
            # The user supplied children explicitly — no need to ask the
            # server to expand. We still record the path for visibility.
            summary["results"].append(
                {"tag_path": tag_path, "kind": "tag", "children": children}
            )

    return summary


def run_auto_expand(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    root_topic: str,
    model: dict[str, Any],
    levels: int = 2,
    tags_per_level: int = 10,
    questions_per_leaf: int = 5,
    language: str = "zh",
) -> dict[str, Any]:
    """Build a label tree from scratch by recursively calling /distill/tags.

    Use this when the user only has a root topic (e.g. '物理学') and wants
    Easy-Dataset to generate the whole tree. Each level expands by calling
    /distill/tags with the parent tag, then leaves get /distill/questions.
    """
    summary = {
        "tags_called": 0,
        "questions_called": 0,
        "leaves_processed": 0,
        "results": [],
    }

    def _expand(parent_tag: str, parent_path: str, depth_left: int):
        if depth_left <= 0:
            res = generate_questions(
                backend,
                project_id,
                tag_path=parent_path,
                current_tag=parent_tag,
                count=questions_per_leaf,
                model=model,
                language=language,
            )
            summary["questions_called"] += 1
            summary["leaves_processed"] += 1
            summary["results"].append(
                {"tag_path": parent_path, "kind": "questions", "result": res}
            )
            return

        tags_res = generate_tags(
            backend,
            project_id,
            parent_tag=parent_tag,
            tag_path=parent_path,
            count=tags_per_level,
            model=model,
            language=language,
        )
        summary["tags_called"] += 1
        children = tags_res.get("tags") or tags_res.get("data") or []
        if not isinstance(children, list):
            children = []
        summary["results"].append(
            {"tag_path": parent_path, "kind": "tags", "children": children}
        )

        for child in children:
            child_name = child if isinstance(child, str) else child.get("label", "")
            if not child_name:
                continue
            _expand(child_name, f"{parent_path}/{child_name}", depth_left - 1)

    _expand(root_topic, root_topic, levels)
    return summary
