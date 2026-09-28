import logging
import uuid

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ImageJob, JobStatus
from app.queue import image_queue, redis_conn
from app.services.storage import get_storage
from app.services.prompt_builder import RESTYLE_VARIATION_STYLES
from app.worker import process_image_job

# Redis key backing the atomic counter that hands out batch_number values
# (1, 2, 3, ...) for storage folder naming. INCR is atomic in Redis, so this
# is safe under concurrent uploads without needing a DB-level sequence.
_BATCH_NUMBER_COUNTER_KEY = "restyle:batch_number_seq"


def _next_batch_number() -> int:
    return redis_conn.incr(_BATCH_NUMBER_COUNTER_KEY)

logger = logging.getLogger(__name__)
settings = get_settings()


class RestyleJobNotFound(Exception):
    """No ImageJob row exists with the given id."""
    pass


class RestyleJobNotSelectable(Exception):
    """
    Job exists but its status isn't COMPLETED (e.g. FAILED, PENDING, or
    PROCESSING) — it has no usable image_path, so it cannot be marked as
    the merchant's selected result. Raised instead of silently setting
    is_selected=True on a job with no image, which would corrupt the
    "one selected job per batch" guarantee.
    """
    pass


class RestyleBatchNotFound(Exception):
    """No ImageJob rows exist with the given batch_id."""
    pass


class RestyleGenerationInProgress(Exception):
    """
    A job in this batch is still PENDING/PROCESSING. Raised instead of
    silently enqueuing a second concurrent generation for the same batch —
    guards against double-clicks / refresh-and-reclick on the Regenerate
    button creating two simultaneous renders of the same source image.
    """
    pass


class RestyleLimitReached(Exception):
    """
    The batch already has settings.MAX_IMAGES_PER_BATCH rows (original
    upload + regenerations included). Raised so the router can return a
    clear 409/422 instead of quietly enqueuing past the configured cap.
    """
    pass


class RestyleStyleNotChosen(Exception):
    """The batch is still AWAITING_STYLE — the merchant hasn't picked a style yet."""
    pass


class RestyleStyleAlreadyChosen(Exception):
    """A style was already chosen (generation already started) for this batch."""
    pass


class InvalidRestyleStyle(Exception):
    """style_index is outside the range of available styles."""
    pass


def create_restyle_batch(
    db: Session,
    *,
    extra_styling: str | None,
    photo_bytes: bytes,
    photo_ext: str,
) -> ImageJob:
    """
    Merchant uploaded their own photo. Saves it and creates the batch in
    AWAITING_STYLE state — nothing is enqueued and no provider is called
    until the merchant picks a style (see start_restyle).

    photo_ext is the *validated* real extension (see jobs.py), never the
    client-supplied filename — avoids trusting user input in a storage path.
    """
    storage = get_storage(settings)
    batch_id = str(uuid.uuid4())
    batch_number = _next_batch_number()

    # NOTE: batch_number (not batch_id) is what names the folder in storage
    # now — purely so files are easy to spot by eye. batch_id UUID is still
    # the real identifier used everywhere else (API, DB lookups, frontend).
    source_key = storage.build_key(batch_id=str(batch_number), name="source", ext=photo_ext)
    source_path = storage.save(key=source_key, content=photo_bytes)

    job = ImageJob(
        batch_id=batch_id,
        batch_number=batch_number,
        status=JobStatus.AWAITING_STYLE,
        variation_index=0,
        extra_styling=extra_styling,
        source_image_path=source_path,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def start_restyle(db: Session, batch_id: str, style_index: int) -> ImageJob:
    """
    Merchant picked a style: record it on the batch's first job and enqueue
    the generation. Row is locked so a double-click can't start it twice.
    """
    if not 0 <= style_index < len(RESTYLE_VARIATION_STYLES):
        raise InvalidRestyleStyle(
            f"style_index must be between 0 and {len(RESTYLE_VARIATION_STYLES) - 1}"
        )

    if db.query(ImageJob).filter(ImageJob.batch_id == batch_id).count() == 0:
        raise RestyleBatchNotFound(f"No batch with id {batch_id}")

    job = (
        db.query(ImageJob)
        .filter(ImageJob.batch_id == batch_id, ImageJob.status == JobStatus.AWAITING_STYLE)
        .with_for_update()
        .first()
    )
    if job is None:
        raise RestyleStyleAlreadyChosen(f"A style was already chosen for batch {batch_id}")

    job.style_index = style_index
    job.status = JobStatus.PENDING
    db.commit()
    db.refresh(job)

    image_queue.enqueue(
        process_image_job, job.id, job_timeout=settings.RESTYLE_JOB_TIMEOUT_SECONDS
    )
    return job


def _next_unused_style(existing: list[ImageJob]) -> int | None:
    """
    Continues after the merchant's first pick and wraps around, skipping
    styles already used in this batch. E.g. first pick 2 of 7 -> 3,4,5,6,0,1.
    Returns None when every style has been used (caller then lets the model
    choose the surface freely).
    """
    total = len(RESTYLE_VARIATION_STYLES)
    used = {job.style_index for job in existing if job.style_index is not None}
    first = existing[0].style_index or 0
    for step in range(1, total + 1):
        candidate = (first + step) % total
        if candidate not in used:
            return candidate
    return None


def regenerate_restyle(db: Session, batch_id: str) -> ImageJob:
    """
    Merchant didn't like the current image and clicked "Regenerate": run
    the SAME source photo through the provider again with the next unused
    style (or, once all styles are used, a model-chosen surface), as a new row in the same batch.

    Guards (all enforced here, server-side — never trust the frontend
    button being disabled):
      - RestyleStyleNotChosen: the merchant hasn't picked a first style yet.
      - RestyleGenerationInProgress: another attempt in this batch is still
        PENDING/PROCESSING.
      - RestyleLimitReached: MAX_IMAGES_PER_BATCH rows exist.
    """
    existing = (
        db.query(ImageJob)
        .filter(ImageJob.batch_id == batch_id)
        .order_by(ImageJob.variation_index)
        .all()
    )
    if not existing:
        raise RestyleBatchNotFound(f"No batch with id {batch_id}")

    if any(job.status == JobStatus.AWAITING_STYLE for job in existing):
        raise RestyleStyleNotChosen(f"Batch {batch_id} has no style selected yet")

    if any(job.status in (JobStatus.PENDING, JobStatus.PROCESSING) for job in existing):
        raise RestyleGenerationInProgress(
            f"Batch {batch_id} already has a generation in progress"
        )

    if len(existing) >= settings.MAX_IMAGES_PER_BATCH:
        raise RestyleLimitReached(
            f"Batch {batch_id} has reached the maximum of "
            f"{settings.MAX_IMAGES_PER_BATCH} images"
        )

    # All curated styles used -> None -> the model freely picks the surface
    # (same behavior as the original first-generation prompt). Only
    # MAX_IMAGES_PER_BATCH stops the batch from here on.
    next_style = _next_unused_style(existing)

    template = existing[0]  # source photo + extra_styling are identical across a batch

    job = ImageJob(
        batch_id=batch_id,
        batch_number=template.batch_number,  # same folder as the rest of this batch
        status=JobStatus.PENDING,
        variation_index=len(existing),
        style_index=next_style,
        extra_styling=template.extra_styling,
        source_image_path=template.source_image_path,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    image_queue.enqueue(
        process_image_job, job.id, job_timeout=settings.RESTYLE_JOB_TIMEOUT_SECONDS
    )

    return job


def select_restyle(db: Session, job_id: int) -> ImageJob:
    """
    Merchant picked their favorite attempt from a restyle batch. This does
    not enqueue a new render — the chosen output is already full quality —
    it just records the preference and clears any prior selection in the
    same batch.

    Only a COMPLETED job (i.e. one that actually has a generated
    image_path) can be selected. FAILED/PENDING/PROCESSING jobs are
    rejected with RestyleJobNotSelectable — selecting one of those would
    mark a batch's "final pick" as a job with no image, which the
    frontend has no sane way to render.
    """
    chosen = db.get(ImageJob, job_id)
    if chosen is None:
        raise RestyleJobNotFound(f"No restyle job with id {job_id}")

    if chosen.status != JobStatus.COMPLETED:
        raise RestyleJobNotSelectable(
            f"Job {job_id} has status '{chosen.status.value}', not completed — "
            f"it has no image and cannot be selected"
        )

    db.query(ImageJob).filter(
        ImageJob.batch_id == chosen.batch_id,
        ImageJob.is_selected.is_(True),
    ).update({"is_selected": False})

    chosen.is_selected = True
    db.commit()
    db.refresh(chosen)
    return chosen