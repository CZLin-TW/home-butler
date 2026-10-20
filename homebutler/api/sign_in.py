"""What the Dashboard's server asks while signing someone in or extending a session (Dashboard key only).

The answers are "active" or not; anything else (an error) must not be taken by the Dashboard as "not
active", or a backend hiccup would sign the whole family out.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from homebutler.api.auth import Caller, caller
from homebutler.domain import members, remotes

router = APIRouter(prefix="/v2/sign-in")


class Who(BaseModel):
    line_user_id: str = Field(max_length=64)


class Password(BaseModel):
    password: str = Field(max_length=200)


@router.post("/member")
def member(body: Who, request: Request, _: Caller = Depends(caller("dashboard"))):
    with request.app.state.store.transaction() as session:
        found = members.active_by_line_id(session, body.line_user_id)
        return {"active": found is not None, "name": found.name if found else None}


@router.post("/remote")
def remote(body: Password, request: Request, _: Caller = Depends(caller("dashboard"))):
    guard = request.app.state.remote_guard
    with request.app.state.store.transaction() as session:
        try:
            found = remotes.sign_in(session, body.password, guard)
        except remotes.LockedOut:
            raise HTTPException(429, "嘗試太多次，請稍後再試") from None
        if found is None:
            raise HTTPException(401, "密碼錯誤")
        return {"remote_id": found.id, "name": found.name}


@router.get("/remote/{remote_id}")
def remote_status(remote_id: int, request: Request, _: Caller = Depends(caller("dashboard"))):
    with request.app.state.store.transaction() as session:
        return {"active": remotes.still_enabled(session, remote_id)}
