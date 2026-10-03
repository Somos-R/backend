"""Checks for files that come from the public internet.

The type is decided by the file's own first bytes, never by its name or the `Content-Type` the client claims.
"""
import hashlib
from dataclasses import dataclass

from fastapi import status

from app.core.config import settings
from app.core.errors import ApiError

# (leading bytes, content type): what documents may be.
SIGNATURES = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


@dataclass(frozen=True)
class CheckedFile:
    content_type: str
    size_bytes: int
    sha256: str


def check_document(data: bytes) -> CheckedFile:
    if not data:
        raise ApiError("empty_file", status_code=422, detail="El archivo está vacío")
    if len(data) > settings.document_max_bytes:
        raise ApiError("file_too_large", status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                       detail=f"El archivo supera el máximo de {settings.document_max_bytes // (1024 * 1024)} MB")
    for prefix, content_type in SIGNATURES:
        if data.startswith(prefix):
            return CheckedFile(content_type, len(data), hashlib.sha256(data).hexdigest())
    raise ApiError("unsupported_file_type", status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                   detail="Solo se aceptan archivos PDF, PNG o JPG")


def read_limited(file, limit: int) -> bytes:
    """Read an upload without loading more than `limit` + 1 bytes: a huge file is refused, not buffered."""
    return file.read(limit + 1)


def display_name(raw: str | None) -> str:
    """The name to show for an uploaded file: only the last path part, no control characters, short."""
    name = (raw or "documento").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    return name[:200] or "documento"
