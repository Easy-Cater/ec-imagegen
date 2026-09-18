import logging
import uuid

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ImageJob, JobStatus
from app.queue import image_queue
from app.services.storage import get_storage
from app.worker import process_image_job

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


def create_restyle_batch(
    db: Session,
    *,
    extra_styling: str | None,
    photo_bytes: bytes,
    photo_ext: str,
) -> ImageJob:
    """
    Merchant uploaded their own photo — restyle it via the configured
    inference provider (fal.ai / Replicate) into a single professional
    image. This creates and enqueues exactly ONE job (variation_index=0).
    Further images for the same batch only come from regenerate_restyle,
    one at a time, driven by the merchant clicking "Regenerate" in the UI.

    photo_ext is the *validated* real extension (see jobs.py), never the
    client-supplied filename — avoids trusting user input in a storage path.
    """
    storage = get_storage(settings)
    batch_id = str(uuid.uuid4())

    source_key = storage.build_key(batch_id=batch_id, name="source", ext=photo_ext)
    source_path = storage.save(key=source_key, content=photo_bytes)

    job = ImageJob(
        batch_id=batch_id,
        status=JobStatus.PENDING,
        variation_index=0,
        extra_styling=extra_styling,
        source_image_path=source_path,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    image_queue.enqueue(
        process_image_job, job.id, job_timeout=settings.RESTYLE_JOB_TIMEOUT_SECONDS
    )

    return job


def regenerate_restyle(db: Session, batch_id: str) -> ImageJob:
    """
    Merchant didn't like the current image and clicked "Regenerate": run
    the SAME source photo through the provider again with the next style
    variation, as a new row in the same batch.

    Guards (both enforced here, server-side — never trust the frontend
    button being disabled):
      - RestyleGenerationInProgress: another attempt in this batch is still
        PENDING/PROCESSING.
      - RestyleLimitReached: the batch already has
        settings.MAX_IMAGES_PER_BATCH rows (original included).
    """
    existing = (
        db.query(ImageJob)
        .filter(ImageJob.batch_id == batch_id)
        .order_by(ImageJob.variation_index)
        .all()
    )
    if not existing:
        raise RestyleBatchNotFound(f"No batch with id {batch_id}")

    if any(job.status in (JobStatus.PENDING, JobStatus.PROCESSING) for job in existing):
        raise RestyleGenerationInProgress(
            f"Batch {batch_id} already has a generation in progress"
        )

    if len(existing) >= settings.MAX_IMAGES_PER_BATCH:
        raise RestyleLimitReached(
            f"Batch {batch_id} has reached the maximum of "
            f"{settings.MAX_IMAGES_PER_BATCH} images"
        )

    template = existing[0]  # source photo + extra_styling are identical across a batch
    next_index = len(existing)

    job = ImageJob(
        batch_id=batch_id,
        status=JobStatus.PENDING,
        variation_index=next_index,
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