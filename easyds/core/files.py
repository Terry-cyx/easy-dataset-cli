"""File upload — wraps /api/projects/{id}/files and /images/*.

In addition to plain document upload (``/files``) this module also drives
the image-import endpoints used by spec/03 §案例 1 (汽车图片识别) and §案例 5
(图文 PPT 提取):

* ``/images/zip-import`` — server unpacks an uploaded ZIP and imports every
  image. The CLI zips a local directory client-side using ``zipfile`` from the
  Python stdlib (no Pillow / pypdf2 dependency).
* ``/images/pdf-convert`` — server converts an uploaded PDF into per-page
  images. The CLI just forwards the raw PDF.
* ``DELETE /images?imageId=...`` — remove a single image (案例 5 noise pruning).
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from easyds.utils.backend import EasyDatasetBackend


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}
ALLOWED_DOC_EXTS = {".md", ".pdf"}


def upload(backend: EasyDatasetBackend, project_id: str, file_path: str) -> dict[str, Any]:
    """POST /api/projects/{id}/files — raw-body upload with x-file-name header.

    Easy-Dataset's file upload endpoint is NOT multipart. The route handler
    reads the filename from the URL-encoded ``x-file-name`` request header
    and the file bytes from ``request.arrayBuffer()`` (see
    easy-dataset/app/api/projects/[projectId]/files/route.js). Only ``.md``
    and ``.pdf`` files are accepted server-side.
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    file_name = os.path.basename(file_path)
    ext = os.path.splitext(file_name)[1].lower()
    if ext not in ALLOWED_DOC_EXTS:
        raise ValueError(
            f"Easy-Dataset only accepts {sorted(ALLOWED_DOC_EXTS)} for /files; got {ext!r}"
        )
    with open(file_path, "rb") as fh:
        body = fh.read()
    content_type = "application/pdf" if ext == ".pdf" else "text/markdown"
    return backend.post_bytes(
        f"/api/projects/{project_id}/files",
        body,
        headers={"x-file-name": quote(file_name)},
        content_type=content_type,
    )


def list_files(backend: EasyDatasetBackend, project_id: str) -> list[dict[str, Any]]:
    """GET /api/projects/{id}/files."""
    result = backend.get(f"/api/projects/{project_id}/files")
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    return result or []


def delete_file(
    backend: EasyDatasetBackend, project_id: str, file_id: str
) -> dict[str, Any] | None:
    """DELETE /api/projects/{id}/files?fileId=..."""
    return backend.delete(
        f"/api/projects/{project_id}/files", params={"fileId": file_id}
    )


# ── images ───────────────────────────────────────────────────────────


def _zip_directory(dir_path: str) -> tuple[bytes, list[str]]:
    """Pack every image file under ``dir_path`` into an in-memory ZIP.

    Returns ``(zip_bytes, included_filenames)``. Raises ``FileNotFoundError``
    if the directory contains no images.
    """
    root = Path(dir_path)
    if not root.is_dir():
        raise FileNotFoundError(f"Not a directory: {dir_path}")

    included: list[str] = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                arcname = str(p.relative_to(root))
                zf.write(p, arcname)
                included.append(arcname)

    if not included:
        raise FileNotFoundError(
            f"No image files (one of {sorted(IMAGE_EXTS)}) found under {dir_path}"
        )

    return buf.getvalue(), included


def import_image_directory(
    backend: EasyDatasetBackend, project_id: str, dir_path: str
) -> dict[str, Any]:
    """Zip an entire local directory of images and POST to /images/zip-import.

    Used by `easyds files import --type image --dir LOCAL_DIR` (案例 1). The
    server extracts the ZIP and imports each image into the project. The
    client-side zipping is pure file packaging — no image processing.
    """
    zip_bytes, included = _zip_directory(dir_path)
    files = {
        "file": (
            f"{Path(dir_path).name or 'images'}.zip",
            zip_bytes,
            "application/zip",
        )
    }
    result = backend.post_multipart(
        f"/api/projects/{project_id}/images/zip-import", files=files
    )
    if isinstance(result, dict):
        result.setdefault("imported_count", len(included))
        result.setdefault("imported_files", included)
    return result


def import_pdf_as_images(
    backend: EasyDatasetBackend, project_id: str, pdf_path: str
) -> dict[str, Any]:
    """POST /images/pdf-convert with a raw PDF; server converts to per-page images.

    Used by `easyds files import --type image --from-pdf LOCAL.pdf` (案例 5).
    The CLI does NOT do any PDF parsing — it just forwards the bytes.
    """
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if not pdf_path.lower().endswith(".pdf"):
        raise ValueError("only .pdf files are accepted")
    name = os.path.basename(pdf_path)
    with open(pdf_path, "rb") as fh:
        files = {"file": (name, fh.read(), "application/pdf")}
    return backend.post_multipart(
        f"/api/projects/{project_id}/images/pdf-convert", files=files
    )


def list_images(
    backend: EasyDatasetBackend, project_id: str
) -> list[dict[str, Any]]:
    """GET /api/projects/{id}/images."""
    result = backend.get(f"/api/projects/{project_id}/images")
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    if isinstance(result, dict) and "images" in result:
        return result["images"]
    return result or []


def delete_image(
    backend: EasyDatasetBackend, project_id: str, image_id: str
) -> dict[str, Any] | None:
    """DELETE /api/projects/{id}/images?imageId=..."""
    return backend.delete(
        f"/api/projects/{project_id}/images", params={"imageId": image_id}
    )
