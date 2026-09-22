from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
import boto3

from app.core.config import Settings

import logging

logger = logging.getLogger(__name__)

class StorageBackend(ABC):
    @abstractmethod
    def save(self, *, key: str, content: bytes) -> str:
        raise NotImplementedError

    @abstractmethod
    def read(self, path: str) -> bytes:
        raise NotImplementedError

    @staticmethod
    def build_key(*, batch_id: str, name: str, ext: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        ext = ext.lstrip(".")
        return f"{batch_id}/{name}_{timestamp}.{ext}"

class S3Mirror:
    """
    Uploads a copy of every saved file to S3, alongside the existing local
    save. Read path is untouched — the app still reads from local disk.
    Failures to mirror are logged, never raised, so an S3 hiccup can't fail
    a generation job.
    """
    def __init__(self, bucket: str, region: str, prefix: str):
        
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._client = boto3.client("s3", region_name=region)

    def upload(self, *, key: str, content: bytes) -> None:
        s3_key = f"{self._prefix}/{key}" if self._prefix else key
        try:
            self._client.put_object(Bucket=self._bucket, Key=s3_key, Body=content)
            logger.info("Mirrored to S3: s3://%s/%s", self._bucket, s3_key)
        except Exception:
            logger.exception("Failed to mirror %s to S3 (bucket=%s)", key, self._bucket)


class LocalStorage(StorageBackend):
    def __init__(self, base_dir: str, *, s3_mirror: "S3Mirror | None" = None):
        self._base_dir = Path(base_dir).expanduser().resolve()
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._s3_mirror = s3_mirror

    def save(self, *, key: str, content: bytes) -> str:
        path = self._base_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

        if self._s3_mirror is not None:
            self._s3_mirror.upload(key=key, content=content)

        relative_path = Path(self._base_dir.name) / key
        return str(relative_path)

    def read(self, path: str) -> bytes:
        source_path = Path(path)
        if not source_path.is_absolute():
            if source_path.parts and source_path.parts[0] == self._base_dir.name:
                # Handles the relative "storage\..." format saved above.
                source_path = self._base_dir.parent / source_path
            else:
                source_path = self._base_dir / source_path

        source_path = source_path.resolve()
        
        try:
            return source_path.read_bytes()
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"Stored image not found: {source_path} (storage root: {self._base_dir})"
            ) from exc


class S3Storage(StorageBackend):
    def __init__(self, bucket: str, region: str):
        self._bucket = bucket
        self._region = region

    def save(self, *, key: str, content: bytes) -> str:
        raise NotImplementedError("S3Storage not wired up yet — set STORAGE_BACKEND=local for now.")

    def read(self, path: str) -> bytes:
        raise NotImplementedError("S3Storage not wired up yet — set STORAGE_BACKEND=local for now.")


def get_storage(settings: Settings) -> StorageBackend:
    if settings.STORAGE_BACKEND == "local":
        mirror = None
        if settings.MIRROR_TO_S3:
            mirror = S3Mirror(settings.S3_BUCKET, settings.S3_REGION, settings.S3_PREFIX)
        return LocalStorage(settings.LOCAL_STORAGE_DIR, s3_mirror=mirror)
    if settings.STORAGE_BACKEND == "s3":
        return S3Storage(settings.S3_BUCKET, settings.S3_REGION)
    raise ValueError(f"Unknown STORAGE_BACKEND: {settings.STORAGE_BACKEND}")