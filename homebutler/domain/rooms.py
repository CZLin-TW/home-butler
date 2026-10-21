"""The order rooms are listed in on the Dashboard: one order per household, shared by everyone in it."""
from __future__ import annotations

from sqlalchemy import delete, select

from homebutler.store.models import Room


def ordered(session, household_id: int, names) -> list[str]:
    """`names` (empty ones dropped) in the household's saved order; rooms never placed come last, by name."""
    position = {r.name: r.position for r in session.scalars(select(Room).where(Room.household_id == household_id))}
    return sorted({n for n in names if n}, key=lambda n: (n not in position, position.get(n, 0), n))


def save(session, household_id: int, names: list[str]) -> None:
    """Replace the household's order with `names`, first to last."""
    session.execute(delete(Room).where(Room.household_id == household_id))
    session.add_all(Room(household_id=household_id, name=n, position=i) for i, n in enumerate(names))
