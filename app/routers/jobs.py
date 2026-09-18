import io
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.database import get_db
from app.db.models import ImageJob
from app.schemas import JobOut, RegenerateRestyleRequest, SelectRestyleRequest
from app.services import job_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])
settings = get_settings()

# ─── HEIC/HEIF support (optional) ──────────────────────────────────────────
# pillow-heif ships a compiled DLL that can be blocked by Windows Application
# Control policies (WDAC/AppLocker) on locked-down machines. Import failures
# here must NOT crash the whole app on startup — they should just disable
# HEIC support so JPEG/PNG/WEBP uploads keep working.
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except Exception:
    logger.warning(
        "pillow_heif unavailable — HEIC/HEIF uploads will be rejected until "
        "this is resolved.",
        exc_info=True,
    )
    HEIC_SUPPORTED = False
# ────────────────────────────────────────────────────────────────────────────

# Fixed technical mapping: Pillow's decoded format name -> file extension.
# This is NOT a setting — it never changes. Which of these are *currently
# allowed* is controlled by settings.ALLOWED_IMAGE_FORMATS (.env).
_FORMAT_TO_EXT = {
    "JPEG": "jpg",
    "PNG": "png",
    "WEBP": "webp",
    "HEIF": "heic",  # pillow-heif reports HEIC/HEIF files as format "HEIF"
}


def _validate_and_get_ext(photo_bytes: bytes) -> str:
    try:
        with Image.open(io.BytesIO(photo_bytes)) as img:
            img.verify()
        with Image.open(io.BytesIO(photo_bytes)) as img:
            fmt = img.format
    except Exception:
        raise HTTPException(status_code=415, detail="File is not a valid image.")

    if fmt == "HEIF" and not HEIC_SUPPORTED:
        raise HTTPException(
            status_code=415,
            detail="HEIC/HEIF uploads are currently unavailable on this server.",
        )

    allowed = settings.allowed_image_formats_set
    if fmt not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported image format '{fmt}'. Allowed: {', '.join(sorted(allowed))}.",
        )

    ext = _FORMAT_TO_EXT.get(fmt)
    if ext is None:
        raise HTTPException(
            status_code=500,
            detail=f"Format '{fmt}' is allowed but has no known extension mapping.",
        )
    return ext


@router.post("/restyle", response_model=JobOut, status_code=201)
async def create_restyle_batch(
    photo: UploadFile = File(...),
    extra_styling: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """
    Merchant uploads one source photo. Generates exactly ONE restyled
    image. Use POST /jobs/restyle/regenerate afterwards (same batch_id) if
    the merchant wants to try again, up to settings.MAX_IMAGES_PER_BATCH
    total attempts.
    """
    photo_bytes = await photo.read(settings.MAX_UPLOAD_SIZE_BYTES + 1)
    if len(photo_bytes) > settings.MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max {settings.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB.",
        )
    if len(photo_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    photo_ext = _validate_and_get_ext(photo_bytes)

    if settings.MAX_EXTRA_STYLING_LEN is None:
        extra_styling = None
    elif extra_styling is not None:
        extra_styling = extra_styling.strip()[: settings.MAX_EXTRA_STYLING_LEN] or None

    return job_service.create_restyle_batch(
        db,
        extra_styling=extra_styling,
        photo_bytes=photo_bytes,
        photo_ext=photo_ext,
    )


@router.post("/restyle/regenerate", response_model=JobOut, status_code=201)
def regenerate_restyle(req: RegenerateRestyleRequest, db: Session = Depends(get_db)):
    """
    Merchant didn't like the current image — regenerate against the same
    source photo. Blocked with 409 if a generation for this batch is
    already in flight, or if MAX_IMAGES_PER_BATCH has been reached.
    """
    try:
        return job_service.regenerate_restyle(db, req.batch_id)
    except job_service.RestyleBatchNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except job_service.RestyleGenerationInProgress as e:
        raise HTTPException(status_code=409, detail=str(e))
    except job_service.RestyleLimitReached as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/restyle/select", response_model=JobOut, status_code=201)
def select_restyle(req: SelectRestyleRequest, db: Session = Depends(get_db)):
    """
    Merchant picked their favorite completed attempt. Rejected with 404 if
    the job_id doesn't exist, or 409 if it exists but isn't COMPLETED
    (e.g. a FAILED attempt with no image) — see job_service.select_restyle.
    """
    try:
        return job_service.select_restyle(db, req.job_id)
    except job_service.RestyleJobNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except job_service.RestyleJobNotSelectable as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(ImageJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/batch/{batch_id}", response_model=list[JobOut])
def get_batch(batch_id: str, db: Session = Depends(get_db)):
    """
    Full attempt history for a batch, ordered oldest-first (variation_index
    0 = original upload). Frontend uses len(result) vs MAX_IMAGES_PER_BATCH
    to decide whether to show the Regenerate button.
    """
    jobs = db.query(ImageJob).filter(ImageJob.batch_id == batch_id).order_by(ImageJob.created_at).all()
    if not jobs:
        raise HTTPException(status_code=404, detail="Batch not found")
    return jobs