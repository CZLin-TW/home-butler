"""API keys, one per caller. The plain key is returned once by issue(); only its SHA-256 is stored.

Keys are long random strings, so a plain hash is enough (no slow hashing needed); the prefix tells what
a key is for when it turns up in a configuration screen, and is not secret.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from homebutler.store.models import ApiToken, utcnow

# What each kind of caller may do. A key never gets scopes beyond its kind's.
KINDS = {
    "dashboard": {"dashboard"},
    "home_assistant": {"home_assistant"},
    "telemetry": {"telemetry"},
    "admin": {"admin"},
}
PREFIX = "hb_"
TOUCH_EVERY = timedelta(minutes=5)   # last_used_at is a hint, not an audit log; spare the writes


def digest(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def issue(session: Session, kind: str, label: str, member_id: int | None = None) -> tuple[ApiToken, str]:
    if kind not in KINDS:
        raise ValueError("Unknown kind")
    plain = PREFIX + kind + "_" + secrets.token_urlsafe(32)
    token = ApiToken(kind=kind, label=label, member_id=member_id, token_hash=digest(plain), scopes=sorted(KINDS[kind]))
    session.add(token)
    session.flush()
    return token, plain


def verify(session: Session, plain: str, scope: str) -> ApiToken | None:
    """The live key with this scope, or None. Compares hashes, so timing tells nothing about the key."""
    if not isinstance(plain, str) or not plain.startswith(PREFIX) or len(plain) > 200:
        return None
    token = session.scalar(select(ApiToken).where(ApiToken.token_hash == digest(plain)))
    if token is None or token.revoked_at is not None or scope not in (token.scopes or []) or scope not in KINDS.get(token.kind, ()):
        return None
    now = utcnow()
    if token.last_used_at is None or now - token.last_used_at > TOUCH_EVERY:
        token.last_used_at = now
    return token


def revoke(session: Session, token_id: int) -> bool:
    token = session.get(ApiToken, token_id)
    if token is None or token.revoked_at is not None:
        return False
    token.revoked_at = utcnow()
    return True
