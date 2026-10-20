"""API keys, one per caller. The plain key is returned once by issue(); only its SHA-256 is stored.

Keys are long random strings, so a plain hash is enough (no slow hashing needed). Keys made here carry a
prefix telling what they are for; keys set in the environment (see from_environment) may be any long
random string, so the family can make them and the existing Home Assistant key keeps working.
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
ENV_LABEL = "env:"       # label prefix of keys that come from the environment
MIN_LENGTH = 24
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
    if not isinstance(plain, str) or not MIN_LENGTH <= len(plain) <= 200:
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


def from_environment(session: Session, wanted: list[tuple[str, str, str]]) -> dict:
    """Make the environment's keys the live ones: (kind, label, plain) each gets a token; an environment
    key no longer listed (rotated or removed) is revoked. Keys issued by other means are left alone."""
    now, kept, added, revoked = utcnow(), set(), 0, 0
    existing = {t.token_hash: t for t in session.scalars(select(ApiToken).where(ApiToken.label.startswith(ENV_LABEL)))}
    for kind, label, plain in wanted:
        if kind not in KINDS or not isinstance(plain, str) or not MIN_LENGTH <= len(plain) <= 200:
            raise ValueError(f"The {label} key must be {MIN_LENGTH} to 200 characters")
        token_hash = digest(plain)
        token = existing.get(token_hash) or session.scalar(select(ApiToken).where(ApiToken.token_hash == token_hash))
        if token is None:
            token = ApiToken(kind=kind, label=ENV_LABEL + label, token_hash=token_hash, scopes=sorted(KINDS[kind]))
            session.add(token)
            added += 1
        token.kind, token.label, token.scopes, token.revoked_at = kind, ENV_LABEL + label, sorted(KINDS[kind]), None
        kept.add(token_hash)
    for token_hash, token in existing.items():
        if token_hash not in kept and token.revoked_at is None:
            token.revoked_at = now
            revoked += 1
    session.flush()
    return {"added": added, "revoked": revoked}
