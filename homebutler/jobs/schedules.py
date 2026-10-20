"""The schedule job, every minute: look at the ACs for the automatic shutdown, then send what is due.

Each schedule: checked and marked claimed in one transaction, sent outside it, its result written in
another. A send that raises is "unknown". People's edits lock the same row, so a schedule is never sent
with settings older than the last edit.
"""
from __future__ import annotations

import logging
import threading
from datetime import tzinfo

from homebutler.domain import schedules
from homebutler.store.db import Store
from homebutler.store.models import utcnow

log = logging.getLogger("homebutler.jobs.schedules")


class Scheduler:
    def __init__(self, store: Store, link, targets: dict, zone: tzinfo, clock=utcnow):
        self.store, self.link, self.targets, self.zone, self.clock = store, link, targets, zone, clock
        self.lock = threading.Lock()
        self.recovered = False

    def reconcile(self) -> set[str]:
        """The automatic shutdown only; also called right after a person's AC command succeeds."""
        with self.lock, self.store.transaction() as session:
            return schedules.reconcile(session, self.link.state(), self.clock(), self.zone)

    def run(self) -> dict:
        with self.lock:
            now = self.clock()
            state = self.link.state()
            with self.store.transaction() as session:
                if not self.recovered:
                    schedules.recover(session, now)
                    self.recovered = True
                changed = schedules.reconcile(session, state, now, self.zone)
                due = schedules.due_ids(session, now)
            sent = 0
            for schedule_id in due:
                with self.store.transaction() as session:
                    claimed = schedules.claim(session, self.targets, schedule_id, state, now)
                if claimed is None:
                    continue
                try:
                    outcome = self.targets[claimed.kind].run(claimed.name, claimed.params)
                except Exception:
                    log.exception("schedule %s: sending raised; result unknown", schedule_id)
                    outcome = {"status": "unknown", "message": "指令結果未確認，請檢查設備狀態。系統不會自動重送。"}
                with self.store.transaction() as session:
                    schedules.finish(session, claimed.attempt_id, outcome, self.clock())
                sent += 1
            return {"changed": sorted(changed), "sent": sent}
