import asyncio
import logging
import time

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.db.models import ImageJob, JobStatus
from app.inference.base import InferenceError
from app.inference.provider_factory import get_restyle_provider
from app.services.prompt_builder import build_restyle_prompt
from app.services.storage import get_storage

logger = logging.getLogger(__name__)
settings = get_settings()

def _short_error_message(e: Exception, *, max_lines: int = 2, max_len: int = 300) -> str:
    """
    Keeps only the first `max_lines` lines of the exception's string form,
    capped at `max_len` characters total. Full detail (status codes, links,
    stack traces) still goes to the logger — this is only what's stored in
    the DB, so the jobs table stays readable instead of filling with
    multi-line provider dumps.
    """
    text = str(e).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    short = " ".join(lines[:max_lines])
    if len(short) > max_len:
        short = short[: max_len - 1].rstrip() + "…"
    return short


def _batch_status_counts(db, batch_id: str) -> tuple[int, int, int]:
    jobs = db.query(ImageJob).filter(ImageJob.batch_id == batch_id).all()
    completed = sum(job.status == JobStatus.COMPLETED for job in jobs)
    failed = sum(job.status == JobStatus.FAILED for job in jobs)
    return len(jobs), completed, failed


def process_image_job(job_id: int) -> None:
    """
    Entry point enqueued via RQ. Kept synchronous (RQ's default) and runs the
    actual async HTTP call via asyncio.run.

    Image-to-image restyle only: every job reads the merchant's uploaded
    source photo from storage and sends it + a prompt to whichever provider
    settings.INFERENCE_PROVIDER selects (fal.ai or Replicate — see
    app.inference.provider_factory). The prompt itself is not stored in the
    DB (it's fully deterministic from variation_index + extra_styling), so
    it's rebuilt here and logged for debugging.

    job.error_message stores a short, human-readable summary only — full
    technical detail always goes to the logger, never to the DB.

    IMPORTANT: every failure path below must update the job's status in the
    DB. If an exception type isn't caught here, the job silently stays stuck
    at PROCESSING forever even after RQ has already abandoned it (e.g. on a
    job timeout kill). The broad `except Exception` at the bottom exists
    specifically to prevent that stuck state.
    """
    db = SessionLocal()
    started_at = time.perf_counter()
    try:
        job = db.get(ImageJob, job_id)
        if job is None:
            logger.error("process_image_job: no job with id %s", job_id)
            return

        job.status = JobStatus.PROCESSING
        db.commit()

        logger.info(
            "image_generation_started job_id=%s batch_id=%s status=%s provider=%s",
            job.id,
            job.batch_id,
            job.status.value,
            settings.INFERENCE_PROVIDER,
        )

        storage = get_storage(settings)
        provider_started_at = time.perf_counter()

        try:
            if not job.source_image_path:
                raise InferenceError(
                    f"Restyle job {job_id} has no source_image_path", retryable=False
                )
            source_bytes = storage.read(job.source_image_path)

            prompt = build_restyle_prompt(job.extra_styling, variation_index=job.variation_index)
            logger.info(
                "restyle_prompt job_id=%s batch_id=%s variation_index=%s prompt=%r",
                job.id,
                job.batch_id,
                job.variation_index,
                prompt,
            )

            provider = get_restyle_provider(settings)
            result = asyncio.run(
                provider.generate(
                    prompt=prompt,
                    model=settings.active_restyle_model,
                    size=settings.IMAGE_SIZE,
                    input_image=source_bytes,
                )
            )

        except InferenceError as e:
            job.status = JobStatus.FAILED
            job.error_message = _short_error_message(e)  # short, DB-safe
            db.commit()
            total_seconds = time.perf_counter() - started_at
            provider_seconds = time.perf_counter() - provider_started_at
            batch_total, batch_completed, batch_failed = _batch_status_counts(db, job.batch_id)
            logger.error(
                "image_generation_finished job_id=%s batch_id=%s status=failed "
                "error_type=%s retryable=%s provider_seconds=%.3f total_seconds=%.3f "
                "batch_completed=%s batch_failed=%s batch_total=%s error=%s",
                job.id,
                job.batch_id,
                type(e).__name__,
                e.retryable,
                provider_seconds,
                total_seconds,
                batch_completed,
                batch_failed,
                batch_total,
                e,  # full detail — terminal/log only
            )
            return

        except Exception as e:
            # Catches anything else: RQ's JobTimeoutException, storage.read()
            # failures, provider crashes we didn't anticipate, etc. Without
            # this, the job would stay stuck at PROCESSING forever with no
            # error recorded.
            job.status = JobStatus.FAILED
            job.error_message = _short_error_message(e)  # short, DB-safe
            db.commit()
            total_seconds = time.perf_counter() - started_at
            provider_seconds = time.perf_counter() - provider_started_at
            batch_total, batch_completed, batch_failed = _batch_status_counts(db, job.batch_id)
            logger.exception(
                "image_generation_finished job_id=%s batch_id=%s status=failed "
                "error_type=%s provider_seconds=%.3f total_seconds=%.3f "
                "batch_completed=%s batch_failed=%s batch_total=%s",
                job.id,
                job.batch_id,
                type(e).__name__,
                provider_seconds,
                total_seconds,
                batch_completed,
                batch_failed,
                batch_total,
                # full traceback comes from logger.exception automatically
            )
            return

        provider_seconds = time.perf_counter() - provider_started_at
        storage_started_at = time.perf_counter()
        # Use the human-readable batch_number for the folder name (matches
        # the source image saved in job_service.py). Falls back to the
        # UUID batch_id for rows created before batch_number existed, so
        # those older in-flight batches still land in their original folder.
        folder_name = str(job.batch_number) if job.batch_number is not None else job.batch_id
        key = storage.build_key(
            batch_id=folder_name,
            name=f"restyle_{job.variation_index}",
            ext=settings.OUTPUT_IMAGE_FORMAT,
        )
        path = storage.save(key=key, content=result.content)
        storage_seconds = time.perf_counter() - storage_started_at

        job.status = JobStatus.COMPLETED
        job.image_path = path
        job.model_used = result.model
        job.cost_usd = result.cost_usd
        db.commit()

        total_seconds = time.perf_counter() - started_at
        batch_total, batch_completed, batch_failed = _batch_status_counts(db, job.batch_id)
        logger.info(
            "image_generation_finished job_id=%s batch_id=%s status=completed "
            "provider=%s model=%s provider_seconds=%.3f storage_seconds=%.3f "
            "total_seconds=%.3f batch_completed=%s batch_failed=%s batch_total=%s",
            job.id,
            job.batch_id,
            result.provider,
            result.model,
            provider_seconds,
            storage_seconds,
            total_seconds,
            batch_completed,
            batch_failed,
            batch_total,
        )

    except Exception as e:
        # Last-resort safety net: if something fails even before/around the
        # inner try (e.g. db.get() itself, or the initial PROCESSING commit),
        # still try to mark the job FAILED rather than leaving it stuck.
        logger.exception("Fatal error in process_image_job for job %s", job_id)
        try:
            job = db.get(ImageJob, job_id)
            if job is not None and job.status != JobStatus.COMPLETED:
                job.status = JobStatus.FAILED
                job.error_message = "Unexpected internal error."  # short, DB-safe
                db.commit()
                batch_total, batch_completed, batch_failed = _batch_status_counts(db, job.batch_id)
                logger.error(
                    "image_generation_finished job_id=%s batch_id=%s status=failed "
                    "error_type=%s total_seconds=%.3f batch_completed=%s batch_failed=%s "
                    "batch_total=%s error=%s",
                    job.id,
                    job.batch_id,
                    type(e).__name__,
                    time.perf_counter() - started_at,
                    batch_completed,
                    batch_failed,
                    batch_total,
                    e,  # full detail — terminal/log only
                )
        except Exception:
            logger.exception("Could not even mark job %s as FAILED", job_id)

    finally:
        db.close()