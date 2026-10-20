"""Households. Home Assistant's floors will decide which area belongs to which; until that link is made
everything belongs to the default household."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from homebutler.store.models import Household


def ensure(session: Session, key: str, name: str | None = None) -> Household:
    household = session.scalar(select(Household).where(Household.key == key))
    if household is None:
        household = Household(key=key, name=name or key)
        session.add(household)
        session.flush()
    return household


def by_key(session: Session, key: str) -> Household | None:
    return session.scalar(select(Household).where(Household.key == key))
