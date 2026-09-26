import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ImageJob(Base):
    """
    One row per generated *attempt*.

    Restyle-only service: one uploaded source photo -> one ImageJob per
    generation attempt, all sharing batch_id + source_image_path.
    variation_index records the attempt's position within the batch (0 =
    the merchant's initial upload, 1 = their first "Regenerate" click, 2 =
    the second, ...) and also indexes into
    prompt_builder.RESTYLE_VARIATION_STYLES, so it doubles as a record of
    which lighting/background style produced this image — useful later for
    seeing which styles particular merchants gravitate towards.

    Rows are only ever appended one at a time, driven by the merchant
    clicking Regenerate (see services.job_service.regenerate_restyle),
    capped at settings.MAX_IMAGES_PER_BATCH total rows per batch. Rejected
    attempts are kept (not deleted) for now so this history exists if
    needed later; when that's no longer wanted, non-selected rows for a
    batch can simply be deleted (`DELETE FROM image_jobs WHERE batch_id =
    ... AND is_selected = false`, plus removing their image_path files from
    storage) — nothing else references them, so pruning is safe.

    The merchant picks a favorite; is_selected marks it (enforced
    unique-per-batch in job_service, not DB).

    The full prompt text is NOT stored — it's fully deterministic from
    (variation_index, extra_styling), so it's rebuilt on demand in the
    worker and logged there for debugging instead of persisted redundantly
    across every row.
    """
    __tablename__ = "image_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(36), default=_uuid, index=True)

    # Human-readable sequential number (1, 2, 3, ...) used ONLY to name the
    # storage folder (local disk + S3), so files are easy to spot by eye
    # instead of by UUID. batch_id above remains the real identifier used
    # by every endpoint, schema, and the frontend — this column is not
    # exposed via the API and nothing outside app/services/storage.py and
    # app/worker.py should read it.
    # Nullable so pre-existing rows (created before this column existed)
    # don't break; those batches simply keep their old UUID-named folder
    # forever (storage.py falls back to batch_id when this is None).
    # Allocated once per batch (in job_service.create_restyle_batch) via an
    # atomic Redis INCR — see job_service.py — and reused unchanged by every
    # regenerate attempt in the same batch so they land in the same folder.
    batch_number: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING, index=True)

    variation_index: Mapped[int] = mapped_column(Integer, default=0)
    extra_styling: Mapped[str | None] = mapped_column(Text, nullable=True)

    model_used: Mapped[str | None] = mapped_column(String(128), nullable=True)

    source_image_path: Mapped[str] = mapped_column(String(512))

    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)

    image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # NOTE: column name is legacy from the Replicate/USD era. Since the
    # switch to fal.ai, this now stores the INR price
    # (settings.RESTYLE_PRICE_PER_IMAGE_INR), not USD. Left unrenamed to
    # avoid a migration — rename to cost_inr in a future cleanup pass along
    # with schemas.JobOut.cost_usd.
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)