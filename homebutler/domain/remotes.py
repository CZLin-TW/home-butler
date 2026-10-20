"""Wall tablets: sign in with a shared password for the restricted remote role.

Consecutive wrong passwords lock every attempt for a while (the Dashboard is a public address). The
counter is per process, which fits the single-process deployment; a restart clears it.
"""
from __future__ import annotations

import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from homebutler.domain.passwords import verify_password
from homebutler.store.models import Remote

MAX_FAILS = 5
LOCKOUT_SECONDS = 300


class LockedOut(Exception):
    pass


class Guard:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.fails = 0
        self.locked_until = 0.0
        self.lock = threading.Lock()

    def check(self):
        with self.lock:
            if self.clock() < self.locked_until:
                raise LockedOut()

    def record(self, ok: bool):
        with self.lock:
            if ok:
                self.fails = 0
                return
            self.fails += 1
            if self.fails >= MAX_FAILS:
                self.fails = 0
                self.locked_until = self.clock() + LOCKOUT_SECONDS


def sign_in(session: Session, password: str, guard: Guard) -> Remote | None:
    """The enabled remote whose password this is, or None. Raises LockedOut while locked."""
    guard.check()
    found = None
    if isinstance(password, str) and password:
        for remote in session.scalars(select(Remote).where(Remote.enabled.is_(True), Remote.removed_at.is_(None))):
            if verify_password(password, remote.password_hash):
                found = remote
                break
    guard.record(found is not None)
    return found


def still_enabled(session: Session, remote_id: int) -> bool:
    """Checked before a remote's session is extended: turning a remote off signs it out."""
    remote = session.get(Remote, remote_id)
    return bool(remote and remote.enabled and remote.removed_at is None)
