"""Lighting areas, as Home Assistant's Hue integration shares them. Each listing brings the table in line:
new areas are added, gone ones marked removed (kept, so to-dos that point at them stay valid), and the
family's own name for an area (display_name) and its on/off for reminders are never overwritten."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from homebutler.store.models import LightArea, utcnow


def discover(session: Session, areas: list[dict], household_id: int) -> dict[str, LightArea]:
    existing = {a.ha_target: a for a in session.scalars(select(LightArea))}
    seen = set()
    for area in areas:
        target = str(area.get("id") or "")
        if not target:
            continue
        seen.add(target)
        row = existing.get(target)
        if row is None:
            row = LightArea(ha_target=target, household_id=household_id)
            session.add(row)
            existing[target] = row
        row.source_name = str(area.get("hue_name") or "")[:80]
        row.resource_type = str(area.get("resource_type") or "grouped_light")[:32]
        row.removed_at = None
    for target, row in existing.items():
        if target not in seen and row.removed_at is None:
            row.removed_at = utcnow()
    session.flush()
    return existing
