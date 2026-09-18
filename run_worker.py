"""
Local dev worker — handles BOTH Windows and Linux/macOS in one file.
NOT used by Docker (Dockerfile.worker invokes `rq worker` directly via the
CLI, which runs fine on Linux containers). Use this only for running the
worker outside Docker, on any OS.

Auto-detects the platform at runtime:
  - Windows: uses SimpleWorker + TimerDeathPenalty (thread-based timeout),
    since RQ's default SIGALRM-based timeout enforcement doesn't exist on
    Windows, and SimpleWorker avoids os.fork (also unavailable on Windows).
  - Linux/macOS: uses RQ's default Worker (os.fork + SIGALRM) — more
    robust, no reason to use the Windows path here.

Run with: python scripts/run_worker.py
"""
import logging
import os
import sys
import platform

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from redis import Redis  # noqa: E402
from rq import Queue, Worker  # noqa: E402

from app.core.config import get_settings  # noqa: E402

settings = get_settings()

IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    from rq.timeouts import TimerDeathPenalty  # noqa: E402
    from rq.worker import SimpleWorker  # noqa: E402

    class WindowsWorker(SimpleWorker):
        death_penalty_class = TimerDeathPenalty


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger(__name__)

    redis_conn = Redis.from_url(settings.REDIS_URL)
    queue = Queue(settings.RQ_QUEUE_NAME, connection=redis_conn)

    worker_cls = WindowsWorker if IS_WINDOWS else Worker
    logger.info("Starting %s on %s", worker_cls.__name__, platform.system())

    worker = worker_cls([queue], connection=redis_conn)
    worker.work()