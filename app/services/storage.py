from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import Settings


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


class LocalStorage(StorageBackend):
    def __init__(self, base_dir: str):
        self._base_dir = Path(base_dir).expanduser().resolve()
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, *, key: str, content: bytes) -> str:
        path = self._base_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

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
        return LocalStorage(settings.LOCAL_STORAGE_DIR)
    if settings.STORAGE_BACKEND == "s3":
        return S3Storage(settings.S3_BUCKET, settings.S3_REGION)
    raise ValueError(f"Unknown STORAGE_BACKEND: {settings.STORAGE_BACKEND}")