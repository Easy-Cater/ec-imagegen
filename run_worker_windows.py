"""
Local dev worker for Windows — NOT used by Docker (Dockerfile.worker invokes
`rq worker` directly via the CLI, which runs fine on Linux containers). Use
this only for running the worker outside Docker on Windows, where RQ's
default SIGALRM-based job timeout enforcement doesn't exist.

Run with: python scripts/run_worker_windows.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from redis import Redis  # noqa: E402
from rq import Queue  # noqa: E402
from rq.timeouts import TimerDeathPenalty  # noqa: E402
from rq.worker import SimpleWorker  # noqa: E402

from app.core.config import get_settings  # noqa: E402

settings = get_settings()


class WindowsWorker(SimpleWorker):
    """
    RQ's default death-penalty (job timeout enforcement) uses SIGALRM, which
    doesn't exist on Windows. TimerDeathPenalty is a thread-based alternative
    that works cross-platform. Combined with SimpleWorker (no os.fork), this
    lets RQ run natively on Windows for local dev.
    """
    death_penalty_class = TimerDeathPenalty


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    redis_conn = Redis.from_url(settings.REDIS_URL)
    queue = Queue(settings.RQ_QUEUE_NAME, connection=redis_conn)
    worker = WindowsWorker([queue], connection=redis_conn)
    worker.work()