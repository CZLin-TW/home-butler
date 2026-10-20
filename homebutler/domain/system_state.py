"""Small named values that must survive a restart."""
from __future__ import annotations

from sqlalchemy.orm import Session

from homebutler.store.models import SystemState


def get(session: Session, key: str, default: str = "") -> str:
    row = session.get(SystemState, key)
    return row.value if row is not None else default


def set_(session: Session, key: str, value: str) -> None:
    row = session.get(SystemState, key)
    if row is None:
        session.add(SystemState(key=key, value=value))
    else:
        row.value = value
