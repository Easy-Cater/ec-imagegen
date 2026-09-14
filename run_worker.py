"""
Local dev worker for Linux/macOS — NOT used by Docker (Dockerfile.worker
invokes `rq worker` directly via the CLI). Use this only for running the
worker outside Docker on Linux/macOS. For Windows, use run_worker_windows.py
instead (SIGALRM-based timeouts don't exist on Windows).

Run with: python scripts/run_worker_dev.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rq import Worker  # noqa: E402

from app.queue import image_queue, redis_conn  # noqa: E402

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    Worker([image_queue], connection=redis_conn).work()