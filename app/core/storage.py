"""Private storage for the documents applicants upload.

Files are never served from here directly: the API reads them and hands out short-lived signed links (or, with
an object store later, the store's own signed URLs). Keys are random and never contain the original name.

Only the `local` backend (a directory) exists today. It is for development and tests: production needs a
backend that survives a deploy (an object store); `Settings` warns when production still uses `local`.
"""
import re
import uuid
from pathlib import Path
from typing import Protocol

from app.core.config import settings

# What a key may look like: folders of ids and a random name. Anything else could escape the directory.
_KEY = re.compile(r"^[a-z0-9_-]+(/[a-z0-9_-]+)*$")


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


class StorageError(Exception):
    pass


def new_key(*folders: str) -> str:
    """A random key under the given folders (`applications/<organization id>/<random>`)."""
    return "/".join([*folders, uuid.uuid4().hex])


class LocalStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        if not _KEY.match(key):
            raise StorageError("invalid storage key")
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise StorageError("invalid storage key")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError("file not found") from exc

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def get_storage() -> Storage:
    if settings.storage_backend == "local":
        return LocalStorage(settings.storage_local_dir)
    raise StorageError(f"unknown STORAGE_BACKEND '{settings.storage_backend}'")
