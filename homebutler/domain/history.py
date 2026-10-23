"""The history tables keep 400 days; older rows are deleted (docs/refactor-plan.md §3.4).

Rows go one device (or computer) at a time, so each delete walks the (device, t) index instead of
scanning the whole table. The dehumidifier module prunes its own table the same way.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from homebutler.store.models import AcReading, Computer, ComputerMetric, Device, SensorReading

KEEP = timedelta(days=400)


def prune(session: Session, now: datetime) -> int:
    cutoff, removed = now - KEEP, 0
    for device_id in session.scalars(select(Device.id)):
        for model in (SensorReading, AcReading):
            removed += session.execute(delete(model).where(model.device_id == device_id, model.t < cutoff)).rowcount
    for computer_id in session.scalars(select(Computer.id)):
        removed += session.execute(delete(ComputerMetric).where(ComputerMetric.computer_id == computer_id,
                                                                ComputerMetric.t < cutoff)).rowcount
    return removed
