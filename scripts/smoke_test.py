"""
Local smoke test — NOT part of the app. Verifies the restyle-batch -> select
flow works end to end using a stub inference provider (no real Replicate
calls, no API cost). Run with a local Redis up:

    python scripts/smoke_test.py
"""
import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///./smoke.db")
os.environ.setdefault("REPLICATE_API_TOKEN", "test-token")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.worker as worker_module  # noqa: E402
from app.db.database import SessionLocal, init_db  # noqa: E402
from app.db.models import ImageJob, JobStatus  # noqa: E402
from app.inference.base import GeneratedImage  # noqa: E402
from app.queue import image_queue, redis_conn  # noqa: E402
from app.services import job_service  # noqa: E402
from rq import SimpleWorker  # noqa: E402


class StubProvider:
    """Returns a tiny fake JPEG instead of calling Replicate — for local testing only."""
    async def generate(self, *, prompt: str, model: str, size: str, input_image: bytes) -> GeneratedImage:
        fake_jpg = b"\xff\xd8\xff\xe0" + b"FAKE" * 10
        return GeneratedImage(content=fake_jpg, content_type="image/jpeg",
                               provider="stub", model=model, cost_usd=0.04)


def get_stub_restyle_provider(_settings):
    return StubProvider()


def run_worker_burst():
    SimpleWorker([image_queue], connection=redis_conn).work(burst=True)


def main():
    worker_module.get_restyle_provider = get_stub_restyle_provider  # monkeypatch: no real API calls

    init_db()
    db = SessionLocal()

    print("1) Creating restyle batch...")
    fake_photo = b"\xff\xd8\xff\xe0" + b"NOTAREALPHOTO" * 20
    jobs = job_service.create_restyle_batch(
        db,
        extra_styling="rustic wooden table",
        photo_bytes=fake_photo,
        photo_ext="jpg",
    )
    batch_id = jobs[0].batch_id
    print(f"   -> {len(jobs)} restyle jobs created, batch_id={batch_id}")

    run_worker_burst()

    db.expire_all()
    jobs = db.query(ImageJob).filter(ImageJob.batch_id == batch_id).all()
    for j in jobs:
        assert j.status == JobStatus.COMPLETED, f"job {j.id} did not complete: {j.status} {j.error_message}"
    print(f"   -> all {len(jobs)} restyle variations COMPLETED, e.g. image_path={jobs[0].image_path}")

    print("2) Selecting a variation...")
    selected = job_service.select_restyle(db, jobs[0].id)
    db.expire_all()
    selected = db.get(ImageJob, selected.id)
    assert selected.is_selected is True
    print(f"   -> job {selected.id} marked is_selected=True")

    others = [j for j in jobs if j.id != selected.id]
    db.expire_all()
    for o in others:
        o = db.get(ImageJob, o.id)
        assert o.is_selected is False, f"job {o.id} should not be selected"
    print(f"   -> other {len(others)} variations correctly NOT selected")

    print("3) Selecting a different variation (should clear the first)...")
    second_pick = job_service.select_restyle(db, others[0].id)
    db.expire_all()
    first_pick_refreshed = db.get(ImageJob, selected.id)
    second_pick_refreshed = db.get(ImageJob, second_pick.id)
    assert first_pick_refreshed.is_selected is False
    assert second_pick_refreshed.is_selected is True
    print(f"   -> selection correctly moved from job {selected.id} to job {second_pick.id}")

    print("4) Selecting a nonexistent job (should raise RestyleJobNotFound)...")
    try:
        job_service.select_restyle(db, 999999)
        print("   -> FAIL: expected RestyleJobNotFound, none raised")
        sys.exit(1)
    except job_service.RestyleJobNotFound as e:
        print(f"   -> correctly rejected: {e}")

    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()