"""Who is calling. Every caller sends its own key (`Authorization: Bearer hb_…`); the Dashboard also
sends a short-lived assertion of the signed-in user, signed with a key only it and this service hold.

The assertion replaces trusting a plain "this is the user" header: even a leaked Dashboard key cannot
act as an arbitrary member without also forging a fresh signature.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status

from homebutler.domain import members, tokens
from homebutler.store.models import ApiToken, Member

MAX_ASSERTION_SECONDS = 300
AUDIENCE = "homebutler"
ROLES = ("member", "remote")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def sign_assertion(key: str, claims: dict) -> str:
    """HS256 compact token. The Dashboard does the same in TypeScript; tests use this."""
    head = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64encode(json.dumps(claims, separators=(",", ":")).encode())
    mac = hmac.new(key.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64encode(mac)}"


class AssertionError_(ValueError):
    pass


def read_assertion(key: str, token: str, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    try:
        head, body, mac = token.split(".")
        header = json.loads(_b64decode(head))
        expected = hmac.new(key.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
        if header.get("alg") != "HS256" or not hmac.compare_digest(expected, _b64decode(mac)):
            raise AssertionError_("bad signature")
        claims = json.loads(_b64decode(body))
    except (ValueError, TypeError, json.JSONDecodeError):
        raise AssertionError_("malformed") from None
    iat, exp = claims.get("iat"), claims.get("exp")
    if (claims.get("aud") != AUDIENCE or type(iat) is not int or type(exp) is not int
            or not iat - 30 <= now <= exp or exp - iat > MAX_ASSERTION_SECONDS):
        raise AssertionError_("expired or out of bounds")
    if claims.get("role") not in ROLES or not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise AssertionError_("bad subject")
    return claims


@dataclass
class Caller:
    token: ApiToken


@dataclass
class DashboardUser:
    role: str                 # "member" or "remote"
    member: Member | None     # the member, for role "member"
    remote_id: int | None     # the remote, for role "remote"


def _bearer(authorization: str) -> str:
    return authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""


def caller(scope: str):
    """Dependency: a live key with this scope, or 401."""
    def check(request: Request, authorization: str = Header(default="")) -> Caller:
        store = request.app.state.store
        with store.transaction() as session:
            token = tokens.verify(session, _bearer(authorization), scope)
            if token is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "需要有效的金鑰")
            session.expunge(token)
        return Caller(token)
    return check


def dashboard_user(request: Request, who: Caller = Depends(caller("dashboard")),
                   x_dashboard_assertion: str = Header(default="")) -> DashboardUser:
    """Dependency for Dashboard requests made on behalf of a signed-in person."""
    key = request.app.state.settings.dashboard_assertion_key
    if not key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "尚未設定 Dashboard 驗證")
    try:
        claims = read_assertion(key, x_dashboard_assertion)
    except AssertionError_:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登入資訊無效或已過期") from None
    with request.app.state.store.transaction() as session:
        if claims["role"] == "remote":
            from homebutler.domain import remotes
            remote_id = int(claims["sub"]) if claims["sub"].isdigit() else -1
            if not remotes.still_enabled(session, remote_id):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "這台遙控器已停用")
            return DashboardUser("remote", None, remote_id)
        member = members.active_by_line_id(session, claims["sub"])
        if member is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "不是啟用中的家庭成員")
        session.expunge(member)
    return DashboardUser("member", member, None)
