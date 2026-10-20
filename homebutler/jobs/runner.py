"""Background jobs in this process: each on its own interval and thread, a failure in one never stops
another, a run never overlaps itself, and a missed interval is not made up in a burst.

Single process only (see docs/refactor-plan.md): with more than one process every job would run twice.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("homebutler.jobs")


@dataclass
class Job:
    name: str
    interval: float
    run: callable
    started_at: float | None = None
    finished_at: float | None = None
    succeeded_at: float | None = None
    error: str | None = None
    running: bool = False
    runs: int = 0


@dataclass
class Jobs:
    clock: callable = time.time
    jobs: dict = field(default_factory=dict)
    stopping: threading.Event = field(default_factory=threading.Event)
    threads: list = field(default_factory=list)

    def add(self, name: str, interval: float, run) -> None:
        if name in self.jobs:
            raise ValueError("Duplicate job")
        self.jobs[name] = Job(name, interval, run)

    def run_once(self, name: str) -> None:
        job = self.jobs[name]
        job.running, job.started_at = True, self.clock()
        try:
            job.run()
            job.succeeded_at, job.error = self.clock(), None
        except Exception as error:   # one job's failure is recorded, never spreads
            job.error = type(error).__name__
            log.exception("job %s failed", name)
        finally:
            job.running, job.finished_at = False, self.clock()
            job.runs += 1

    def _loop(self, name: str) -> None:
        job = self.jobs[name]
        while not self.stopping.is_set():
            started = time.monotonic()
            self.run_once(name)
            self.stopping.wait(max(1.0, job.interval - (time.monotonic() - started)))

    def start(self) -> None:
        for name in self.jobs:
            thread = threading.Thread(target=self._loop, args=(name,), name="job-" + name, daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self, timeout: float = 5) -> None:
        self.stopping.set()
        for thread in self.threads:
            thread.join(timeout)

    def status(self) -> list[dict]:
        return [{"name": j.name, "interval": j.interval, "running": j.running, "runs": j.runs,
                 "started_at": j.started_at, "succeeded_at": j.succeeded_at, "error": j.error}
                for j in self.jobs.values()]
