"""Model config CRUD — wraps /api/projects/{id}/model-config*."""

from __future__ import annotations

from typing import Any

from easyds.utils.backend import EasyDatasetBackend


def list_configs(backend: EasyDatasetBackend, project_id: str) -> list[dict[str, Any]]:
    """GET /api/projects/{id}/model-config."""
    result = backend.get(f"/api/projects/{project_id}/model-config")
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    return result or []


VALID_MODEL_TYPES = ("text", "vision")


def set_config(
    backend: EasyDatasetBackend,
    project_id: str,
    *,
    provider_id: str,
    provider_name: str,
    endpoint: str,
    api_key: str,
    model_id: str,
    model_name: str | None = None,
    model_type: str = "text",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    top_p: float = 0.9,
    top_k: int | None = None,
) -> dict[str, Any]:
    """POST /api/projects/{id}/model-config — register a model config.

    ``model_type`` distinguishes ``text`` (LLM) from ``vision`` (VLM); the
    server's ModelConfig schema includes this column. CLI's
    ``questions generate --source image`` will resolve to the active model
    of type='vision'.
    """
    if model_type not in VALID_MODEL_TYPES:
        raise ValueError(
            f"model_type must be one of {VALID_MODEL_TYPES}, got {model_type!r}"
        )
    body = {
        "providerId": provider_id,
        "providerName": provider_name,
        "endpoint": endpoint,
        "apiKey": api_key,
        "modelId": model_id,
        "modelName": model_name or model_id,
        "type": model_type,
        "temperature": temperature,
        "maxTokens": max_tokens,
        "topP": top_p,
    }
    if top_k is not None:
        body["topK"] = top_k
    return backend.post(f"/api/projects/{project_id}/model-config", json_body=body)


def find_config_by_type(
    configs: list[dict[str, Any]], model_type: str
) -> dict[str, Any] | None:
    """Helper: pick the first model config matching ``model_type``."""
    for c in configs:
        if isinstance(c, dict) and c.get("type") == model_type:
            return c
    return None


def get_config_object(
    backend: EasyDatasetBackend, project_id: str, model_config_id: str
) -> dict[str, Any]:
    """Look up the full model-config dict for ``model_config_id``.

    The Easy-Dataset server expects every LLM-driven endpoint (/split,
    /generate-questions, /datasets, etc.) to receive the *whole* model
    config object as ``model``, NOT just the id. The frontend reads it
    from localStorage (selectedModelInfoAtom) before each fetch. The CLI
    must do the same: list configs and find by id. Raises ValueError if
    the id is not found in the project.
    """
    configs = list_configs(backend, project_id)
    for c in configs:
        if isinstance(c, dict) and c.get("id") == model_config_id:
            return c
    raise ValueError(
        f"model config {model_config_id!r} not found in project {project_id}. "
        f"Run 'easyds model list' to see what's registered."
    )


def update_config(
    backend: EasyDatasetBackend, project_id: str, model_config_id: str, **fields: Any
) -> dict[str, Any]:
    """PUT /api/projects/{id}/model-config/{configId}."""
    return backend.put(
        f"/api/projects/{project_id}/model-config/{model_config_id}", json_body=fields
    )
