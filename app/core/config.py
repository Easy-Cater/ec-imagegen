from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        extra="ignore",
    )

    # --- Inference provider (image-to-image restyle only) ---
    REPLICATE_API_TOKEN: str = ""
    RESTYLE_MODEL: str = "black-forest-labs/flux-kontext-pro"
    RESTYLE_PRICE_PER_IMAGE_USD: float = 0.04
    RESTYLE_POLL_TIMEOUT_SECONDS: int = 180

    # --- Upload validation ---
    MAX_UPLOAD_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB
    ALLOWED_IMAGE_FORMATS: str

    # Currently None = feature disabled; extra_styling is ignored end-to-end
    # (see jobs.py). Set to an integer in .env to enable merchant/backend
    # styling notes with that max length, no code change needed.
    MAX_EXTRA_STYLING_LEN: int | None = None

    MAX_RESTYLE_VARIATIONS: int = 3
    IMAGE_SIZE: str = "1024x1024"

    # --- Storage (local disk for now; swap for S3 client later) ---
    STORAGE_BACKEND: str = "local"
    LOCAL_STORAGE_DIR: str = str(_PROJECT_ROOT / "storage")
    S3_BUCKET: str = ""
    S3_REGION: str = ""

    @field_validator("LOCAL_STORAGE_DIR", mode="before")
    @classmethod
    def normalize_local_storage_dir(cls, value: str | None) -> str:
        if value is None or value == "":
            return str(_PROJECT_ROOT / "storage")

        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (_PROJECT_ROOT / path).resolve()
        return str(path)

    # --- DB / queue ---
    DATABASE_URL: str
    REDIS_URL: str = "redis://localhost:6379/0"
    RQ_QUEUE_NAME: str = "imagegen"
    # --- DB connection pool (Postgres only; ignored for SQLite) ---
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_RECYCLE_SECONDS: int = 1800  # 30 minutes

    # --- HTTP behavior towards the inference API ---
    REQUEST_TIMEOUT_SECONDS: int = 60
    MAX_RETRIES: int = 3
    RETRY_BACKOFF_SECONDS: float = 2.0
    
    # Format Replicate returns the restyled output in, and the extension it's
    # saved with. Not tied to the input format — every job outputs this same
    # format regardless of what the merchant uploaded. Kept in .env so it can
    # be changed (e.g. to "png") without a code deploy.
    OUTPUT_IMAGE_FORMAT: str = "jpg"
    RESTYLE_JOB_TIMEOUT_SECONDS: int = 600

    @property
    def allowed_image_formats_set(self) -> set[str]:
        return {f.strip().upper() for f in self.ALLOWED_IMAGE_FORMATS.split(",") if f.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()