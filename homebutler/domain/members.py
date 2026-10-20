"""Family members. The identity is the LINE user id; only active members may use the butler or sign in."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from homebutler.store.models import Member


def active_by_line_id(session: Session, line_user_id: str) -> Member | None:
    """The member behind a LINE id, or None when unknown, inactive or removed (never a fallback guess)."""
    if not isinstance(line_user_id, str) or not line_user_id:
        return None
    member = session.scalar(select(Member).where(Member.line_user_id == line_user_id))
    return member if member is not None and member.active and member.removed_at is None else None


def active(session: Session) -> list[Member]:
    return list(session.scalars(select(Member).where(Member.active.is_(True), Member.removed_at.is_(None))
                                .order_by(Member.id)))


def alert_recipients(session: Session) -> list[Member]:
    """Members who asked for system alerts; if nobody did, every active member. Silence because a
    setting was never made is exactly the failure alerts exist to prevent."""
    everyone = [m for m in active(session) if m.line_user_id]
    chosen = [m for m in everyone if m.receives_alerts]
    return chosen or everyone
