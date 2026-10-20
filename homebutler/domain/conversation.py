"""The butler's memory of a LINE conversation: the last few turns per member."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from homebutler.store.models import ConversationMessage, Member

TURNS = 6
KEEP = timedelta(days=90)


def recent(session: Session, member: Member, limit: int = TURNS) -> list[dict]:
    rows = list(session.scalars(select(ConversationMessage).where(ConversationMessage.member_id == member.id)
                                .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc()).limit(limit)))
    return [{"role": r.role, "content": r.content} for r in reversed(rows) if r.content]


def save(session: Session, member: Member, role: str, content: str, now: datetime) -> None:
    session.add(ConversationMessage(member_id=member.id, role=role, content=content, created_at=now))


def prune(session: Session, now: datetime) -> int:
    return session.execute(delete(ConversationMessage).where(ConversationMessage.created_at < now - KEEP)).rowcount
