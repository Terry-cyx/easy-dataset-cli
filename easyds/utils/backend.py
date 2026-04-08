"""HTTP client backend for Easy-Dataset.

Easy-Dataset is a long-running Next.js HTTP server (default :1717), not a desktop
binary; the dependency is satisfied by *the running server*. This module is the single point of contact with that server.

If the server is unreachable, every operation raises BackendUnavailable with
clear instructions on how to start it.
"""

from __future__ import annotations

import os
from typing import Any

import requests


DEFAULT_BASE_URL = "http://localhost:1717"


class BackendError(RuntimeError):
    """Server returned a non-2xx response."""


class BackendUnavailable(RuntimeError):
    """The Easy-Dataset server cannot be reached.

    Always raised with restart instructions so AI agents can self-correct.
    """


def _install_instructions(base_url: str) -> str:
    return (
        f"Easy-Dataset server is not reachable at {base_url}.\n"
        "\n"
        "Start it with:\n"
        "    cd /path/to/easy-dataset\n"
        "    pnpm install        # first time only\n"
        "    pnpm dev            # serves on http://localhost:1717\n"
        "\n"
        "Or set EDS_BASE_URL / pass --base-url to point at a remote instance.\n"
        "The Easy-Dataset source lives at https://github.com/ConardLi/easy-dataset"
    )


def resolve_base_url(cli_arg: str | None = None) -> str:
    """CLI flag > EDS_BASE_URL env > default localhost:1717."""
    if cli_arg:
        return cli_arg.rstrip("/")
    env = os.environ.get("EDS_BASE_URL")
    if env:
        return env.rstrip("/")
    return DEFAULT_BASE_URL


class EasyDatasetBackend:
    """Thin HTTP wrapper around the Easy-Dataset Next.js API."""

    def __init__(self, base_url: str | None = None, timeout: float = 600.0):
        self.base_url = resolve_base_url(base_url)
        self.timeout = timeout
        self.session = requests.Session()

    # ── Health ────────────────────────────────────────────────────────

    def check_health(self) -> dict[str, Any]:
        """Verify the server is up by hitting GET /api/projects (cheapest existing route).

        Easy-Dataset has no dedicated /health endpoint. We treat any 2xx/4xx
        response as "server is alive"; only connection errors indicate the
        server is down.
        """
        url = f"{self.base_url}/api/projects"
        try:
            r = self.session.get(url, timeout=min(self.timeout, 5.0))
        except requests.exceptions.ConnectionError as e:
            raise BackendUnavailable(_install_instructions(self.base_url)) from e
        except requests.exceptions.Timeout as e:
            raise BackendUnavailable(
                f"Easy-Dataset server at {self.base_url} did not respond within "
                f"{self.timeout}s. Is it overloaded or stuck?"
            ) from e
        return {"base_url": self.base_url, "status_code": r.status_code, "ok": r.ok}

    # ── Internal request helper ───────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict | None = None,
        files: dict | None = None,
        data: dict | None = None,
        raw: bool = False,
    ) -> Any:
        """Send a request and decode the response.

        ``raw=True`` returns the response body as ``bytes`` regardless of
        Content-Type. Used by file-export endpoints (e.g.
        ``/eval-datasets/export``) that stream json/jsonl/csv with a
        ``Content-Disposition: attachment`` header.
        """
        url = f"{self.base_url}{path}"
        try:
            r = self.session.request(
                method=method,
                url=url,
                json=json_body if files is None else None,
                params=params,
                files=files,
                data=data,
                timeout=self.timeout,
            )
        except requests.exceptions.ConnectionError as e:
            raise BackendUnavailable(_install_instructions(self.base_url)) from e

        if not r.ok:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            raise BackendError(
                f"{method} {path} -> {r.status_code}: {detail}"
            )

        if raw:
            return r.content

        if not r.content:
            return None
        ctype = r.headers.get("Content-Type", "")
        if "application/json" in ctype:
            return r.json()
        return r.text

    # ── Convenience verbs ─────────────────────────────────────────────

    def get(self, path: str, params: dict | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, json_body: Any = None) -> Any:
        return self._request("POST", path, json_body=json_body)

    def post_raw(self, path: str, json_body: Any = None) -> bytes:
        """POST and return the response body as raw bytes (for file streams)."""
        return self._request("POST", path, json_body=json_body, raw=True)

    def put(self, path: str, json_body: Any = None) -> Any:
        return self._request("PUT", path, json_body=json_body)

    def patch(self, path: str, json_body: Any = None) -> Any:
        return self._request("PATCH", path, json_body=json_body)

    def delete(
        self,
        path: str,
        params: dict | None = None,
        json_body: Any = None,
    ) -> Any:
        """DELETE with optional query params or JSON body (for bulk-id deletes)."""
        return self._request("DELETE", path, params=params, json_body=json_body)

    def post_multipart(self, path: str, files: dict, data: dict | None = None) -> Any:
        return self._request("POST", path, files=files, data=data)

    def post_bytes(
        self, path: str, body: bytes, *, headers: dict[str, str] | None = None,
        content_type: str = "application/octet-stream",
    ) -> Any:
        """POST raw bytes with custom headers.

        Used by ``/api/projects/{id}/files`` which reads the filename from
        the ``x-file-name`` request header and the body from
        ``request.arrayBuffer()`` (NOT multipart — see route.js).
        """
        url = f"{self.base_url}{path}"
        merged_headers = {"Content-Type": content_type}
        if headers:
            merged_headers.update(headers)
        try:
            r = self.session.post(
                url, data=body, headers=merged_headers, timeout=self.timeout,
            )
        except requests.exceptions.ConnectionError as e:
            raise BackendUnavailable(_install_instructions(self.base_url)) from e
        if not r.ok:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            raise BackendError(f"POST {path} -> {r.status_code}: {detail}")
        if not r.content:
            return None
        ctype = r.headers.get("Content-Type", "")
        if "application/json" in ctype:
            return r.json()
        return r.text
