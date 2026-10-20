"""Signing a browser in by sending a six-digit code to LINE (the Dashboard's sign-in).

1. The browser asks for a pairing: it gets a six-digit code to show and a long secret it keeps.
2. A member sends the code to the LINE bot. LINE vouches for who sent it, so the pairing is approved as
   that member.
3. The browser, asking with its secret, collects the sign-in once.

Knowing the code only approves; collecting needs the secret, so a guessed code never gives anyone
else's session. A code lasts 5 minutes; an approved pairing is collected at most once.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from homebutler.store.models import Member, PairingCode

TTL = timedelta(minutes=5)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create(session: Session, now: datetime) -> dict:
    session.execute(delete(PairingCode).where(PairingCode.expires_at < now - timedelta(days=1)))
    waiting = set(session.scalars(select(PairingCode.code_hash).where(PairingCode.expires_at > now)))
    for _ in range(50):
        code = f"{secrets.randbelow(1_000_000):06d}"
        if _hash(code) not in waiting:
            break
    else:
        raise RuntimeError("no free pairing code")
    session.execute(delete(PairingCode).where(PairingCode.code_hash == _hash(code)))
    token = secrets.token_urlsafe(32)
    session.add(PairingCode(code_hash=_hash(code), device_token_hash=_hash(token), created_at=now, expires_at=now + TTL))
    session.flush()
    return {"user_code": code, "device_token": token, "expires_in": int(TTL.total_seconds())}


def approve(session: Session, code: str, member: Member, picture_url: str, now: datetime) -> bool:
    found = session.scalar(select(PairingCode).where(PairingCode.code_hash == _hash(code)).with_for_update())
    if found is None or found.approved_at is not None or found.expires_at < now:
        return False
    found.member_id, found.approved_at, found.picture_url = member.id, now, (picture_url or "")[:500]
    return True


def collect(session: Session, device_token: str, now: datetime) -> dict:
    """status: pending | approved (with the member, once) | expired | consumed | not_found."""
    if not device_token:
        return {"status": "not_found"}
    found = session.scalar(select(PairingCode).where(PairingCode.device_token_hash == _hash(device_token)).with_for_update())
    if found is None:
        return {"status": "not_found"}
    if found.used_at is not None:
        return {"status": "consumed"}
    if found.approved_at is None:
        return {"status": "expired" if found.expires_at < now else "pending"}
    member = session.get(Member, found.member_id)
    if member is None or not member.active or member.removed_at is not None:
        return {"status": "expired"}
    found.used_at = now
    return {"status": "approved", "user": {"lineUserId": member.line_user_id, "name": member.name,
                                           "picture": found.picture_url, "role": "member"}}
