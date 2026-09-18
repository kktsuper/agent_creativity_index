"""Blob storage: local filesystem (dev) or Google Cloud Storage (prod)."""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

from .config import get_settings


class Storage(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    @abstractmethod
    def get(self, key: str) -> bytes: ...
    @abstractmethod
    def exists(self, key: str) -> bool: ...


class LocalStorage(Storage):
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _path(self, key: str) -> str:
        p = os.path.abspath(os.path.join(self.root, key))
        if not p.startswith(self.root):
            raise ValueError("bad key")
        return p

    def put(self, key, data, content_type="application/octet-stream"):
        p = self._path(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)

    def get(self, key):
        with open(self._path(key), "rb") as f:
            return f.read()

    def exists(self, key):
        return os.path.exists(self._path(key))


class GCSStorage(Storage):
    def __init__(self, bucket: str):
        from google.cloud import storage as gcs  # lazy import
        self.client = gcs.Client()
        self.bucket = self.client.bucket(bucket)

    def put(self, key, data, content_type="application/octet-stream"):
        self.bucket.blob(key).upload_from_string(data, content_type=content_type)

    def get(self, key):
        return self.bucket.blob(key).download_as_bytes()

    def exists(self, key):
        return self.bucket.blob(key).exists()


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        s = get_settings()
        if s.storage_backend == "gcs":
            _storage = GCSStorage(s.gcs_bucket)
        else:
            _storage = LocalStorage(os.path.join(s.data_dir, "blobs"))
    return _storage


def reset_storage():
    global _storage
    _storage = None
