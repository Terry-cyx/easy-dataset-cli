"""Domain tree tags — wraps /api/projects/{id}/tags.

Easy-Dataset's domain tree is a hierarchical ``Tags`` table where each node has
``{id, label, parentId, children, questionCount}``. The server exposes a
single route with three verbs:

* ``GET  /tags``                       — return the whole nested tree
* ``PUT  /tags`` body ``{tags: {id?, label, parentId?}}`` — create when ``id``
  is missing/null, otherwise update
* ``DELETE /tags?id=tagId``            — cascade-delete the node, descendants,
  and any Questions / Datasets that reference its label
* ``POST /tags`` body ``{tagName}``    — return Questions associated with that
  tag name (read helper, not creation)

There's also a nuclear ``batchSaveTags(projectId, tree)`` helper inside
``lib/db/tags.js`` that drops every tag in the project then recursively
re-inserts the supplied tree. The CLI exposes that as ``replace_tree`` and
warns the caller that it cascades through the question/dataset tables.
"""

from __future__ import annotations

from typing import Any, Iterator

from easyds.utils.backend import EasyDatasetBackend


def list_tags(backend: EasyDatasetBackend, project_id: str) -> list[dict[str, Any]]:
    """GET /api/projects/{id}/tags — return the full nested tree."""
    result = backend.get(f"/api/projects/{project_id}/tags")
    if isinstance(result, dict) and "tags" in result:
        return result["tags"] or []
    return result or []


def save_tag(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    label: str,
    parent_id: str | None = None,
    tag_id: str | None = None,
) -> dict[str, Any]:
    """PUT /api/projects/{id}/tags — create (id is None) or update (id given).

    The server uses the same endpoint for create and update; whether ``id`` is
    in the body distinguishes the two. ``parent_id=None`` makes a root node.
    """
    if not label or not label.strip():
        raise ValueError("label must be a non-empty string")
    body: dict[str, Any] = {"tags": {"label": label, "parentId": parent_id}}
    if tag_id:
        body["tags"]["id"] = tag_id
    return backend.put(f"/api/projects/{project_id}/tags", json_body=body)


def delete_tag(
    backend: EasyDatasetBackend, project_id: str, tag_id: str
) -> dict[str, Any] | None:
    """DELETE /api/projects/{id}/tags?id=tagId — cascade through subtree.

    The server cascades the delete to: descendants, ``Questions`` rows whose
    ``label`` matches a deleted tag, and ``Datasets`` rows whose
    ``questionLabel`` matches.
    """
    return backend.delete(
        f"/api/projects/{project_id}/tags", params={"id": tag_id}
    )


def get_questions_by_tag(
    backend: EasyDatasetBackend, project_id: str, tag_name: str
) -> dict[str, Any]:
    """POST /api/projects/{id}/tags — return Questions whose label == tag_name.

    Reuses the tags route; the server branches on body shape:
    ``{tagName: ...}`` triggers the question lookup, ``{tags: {...}}`` saves.
    """
    return backend.post(
        f"/api/projects/{project_id}/tags", json_body={"tagName": tag_name}
    )


def find_tag(
    tree: list[dict[str, Any]], *, label: str | None = None, tag_id: str | None = None
) -> dict[str, Any] | None:
    """Walk a nested tag tree (as returned by ``list_tags``) to find one node.

    Match by ``label`` (first match wins, depth-first) or by ``tag_id`` (exact).
    Returns the node dict (with its ``children``) or ``None``.
    """
    if label is None and tag_id is None:
        raise ValueError("provide either label or tag_id")
    for node in walk_tree(tree):
        if tag_id is not None and node.get("id") == tag_id:
            return node
        if label is not None and node.get("label") == label:
            return node
    return None


def walk_tree(tree: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Depth-first iterator over every node in a nested tag tree.

    Used by ``find_tag`` and the CLI's ``tags list --flat`` rendering.
    """
    for node in tree:
        if not isinstance(node, dict):
            continue
        yield node
        children = node.get("children") or node.get("child") or []
        if isinstance(children, list):
            yield from walk_tree(children)


def collect_labels(tree: list[dict[str, Any]]) -> list[str]:
    """Flatten the tree to a list of labels in DFS order."""
    return [n.get("label", "") for n in walk_tree(tree) if n.get("label")]
