"""Independent periodic jobs with fixed deadlines, no overlap and observable health.

Single-process only. Business deduplication remains in Sheets; health resets on restart.
"""
import threading
import time


class JobRunner:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.jobs = {}

    def add(self, name, interval, callback):
        with self.lock:
            self.jobs[name] = {"callback": callback, "interval_seconds": interval, "running": False,
                               "last_started_at": None, "last_success_at": None, "last_error": None,
                               "duration_seconds": None, "next_run_at": time.time()}

    def run_once(self, name):
        with self.lock:
            job = self.jobs[name]
            if job["running"]:
                return False
            job["running"] = True
            job["last_started_at"] = time.time()
        start = time.monotonic()
        try:
            job["callback"]()
        except Exception as exc:
            with self.lock:
                job["last_error"] = type(exc).__name__
            print(f"[job:{name}] failed: {type(exc).__name__}")
        else:
            with self.lock:
                job["last_success_at"] = time.time()
                job["last_error"] = None
        finally:
            with self.lock:
                job["running"] = False
                job["duration_seconds"] = round(time.monotonic() - start, 3)
        return True

    def _loop(self, name):
        deadline = time.monotonic()
        interval = self.jobs[name]["interval_seconds"]
        while not self.stop_event.is_set():
            self.run_once(name)
            # Advance from the prior deadline; skip missed slots instead of burst catch-up.
            deadline += interval
            now = time.monotonic()
            if deadline <= now:
                deadline += (int((now - deadline) // interval) + 1) * interval
            delay = max(0, deadline - time.monotonic())
            with self.lock:
                self.jobs[name]["next_run_at"] = time.time() + delay
            self.stop_event.wait(delay)

    def start(self):
        for name in self.jobs:
            threading.Thread(target=self._loop, args=(name,), name=f"job-{name}", daemon=True).start()

    def snapshot(self):
        with self.lock:
            return {name: {k: v for k, v in job.items() if k != "callback"} for name, job in self.jobs.items()}


jobs = JobRunner()
