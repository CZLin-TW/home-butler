"""Device jobs: keep the device list in line with HA (every minute), sample history (every 5 minutes), and record the
machine reports HA passes on (every 30 seconds; each sample once)."""
from __future__ import annotations

from homebutler.domain import computers, devices
from homebutler.store.db import Store
from homebutler.store.models import utcnow


def sync_registry(store: Store, link, default_household: str) -> dict:
    state = link.state()
    with store.transaction() as session:
        return devices.sync_registry(session, state, default_household)


def sample_history(store: Store, link) -> dict:
    state = link.state()
    with store.transaction() as session:
        return devices.sample(session, state, utcnow())


def record_computers(store: Store, link) -> int:
    recorded = 0
    for report in link.computer_reports():
        with store.transaction() as session:
            recorded += computers.record_report(session, report, utcnow()) is not None
    return recorded
