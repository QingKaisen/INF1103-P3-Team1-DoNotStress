"""Runs each check-in on a worker thread.

The Flask request only waits on an Event. Gemini and Logic run on the pool,
so a slow model does not freeze every other request on the same process.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable


class CheckinRunner:
    """A small pool of check-in workers, one job id per submission."""

    def __init__(self, workers: int = 2) -> None:
        self.__pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="checkin")
        self.__jobs: dict[str, dict[str, Any]] = {}
        self.__lock = threading.RLock()
        self.__submitted = 0

    @property
    def submitted(self) -> int:
        return self.__submitted

    def submit(self, work: Callable[[], Any]) -> str:
        """Queue work and return its id. The caller waits with wait()."""
        job_id = uuid.uuid4().hex
        event = threading.Event()
        holder: dict[str, Any] = {
            "id": job_id, "status": "queued", "event": event,
            "result": None,
        }
        with self.__lock:
            self.__jobs[job_id] = holder
            self.__submitted += 1

        def run() -> None:
            holder["status"] = "running"
            try:
                holder["result"] = work()
                holder["status"] = "done"
            except Exception:
                holder["status"] = "failed"
            finally:
                event.set()

        self.__pool.submit(run)
        return job_id

    def wait(self, job_id: str, timeout: float = 30) -> dict[str, Any]:
        """Block until the worker finishes, or until timeout seconds."""
        holder = self.__jobs.get(job_id)
        if holder is None:
            return {"id": job_id, "status": "missing", "result": None}
        holder["event"].wait(timeout)
        return holder

    def status(self, job_id: str) -> dict[str, str] | None:
        """queued, running, done, failed, or None when the id is unknown."""
        holder = self.__jobs.get(job_id)
        if holder is None:
            return None
        return {"id": job_id, "status": str(holder["status"])}

    def close(self) -> None:
        self.__pool.shutdown(wait=False, cancel_futures=True)
