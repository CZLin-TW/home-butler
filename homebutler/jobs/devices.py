"""Device jobs: keep the device list in line with HA (every minute) and sample history (every 5 minutes)."""
from __future__ import annotations

from homebutler.domain import devices
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
