"""The Dashboard's schedules: list (with each AC's automatic shutdown), add, change, remove.

Times in and out are the household's wall clock ("YYYY-MM-DD HH:MM"); `due_at` is also given in UTC.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from homebutler.api.auth import DashboardUser, dashboard_user
from homebutler.domain import schedules
from homebutler.store.models import utcnow

router = APIRouter(prefix="/v2/schedules")


class NewSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_kind: str = Field(max_length=16)
    target_name: str = Field(min_length=1, max_length=160)
    params: dict = Field(default_factory=dict)
    due_local: str = Field(max_length=16)


class Changes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_kind: str | None = Field(default=None, max_length=16)
    target_name: str | None = Field(default=None, min_length=1, max_length=160)
    params: dict | None = None
    due_local: str | None = Field(default=None, max_length=16)


def _zone(request: Request):
    return ZoneInfo(request.app.state.settings.timezone)


def _who(user: DashboardUser) -> tuple[str, int | None]:
    return (user.member.name, user.member.id) if user.member else ("遙控器", None)


def _call(request: Request, action):
    try:
        with request.app.state.store.transaction() as session:
            return schedules.as_dict(action(session), _zone(request))
    except LookupError:
        raise HTTPException(404, "找不到這筆排程") from None
    except schedules.Invalid as error:
        raise HTTPException(409, str(error)) from None


@router.get("")
def listing(request: Request, user: DashboardUser = Depends(dashboard_user)):
    zone, now = _zone(request), utcnow()
    with request.app.state.store.transaction() as session:
        return {"schedules": [schedules.as_dict(s, zone) for s in schedules.visible(session)],
                "auto_off": schedules.auto_off_view(session, request.app.state.ha.state(), now, zone)}


@router.post("")
def add(body: NewSchedule, request: Request, user: DashboardUser = Depends(dashboard_user)):
    name, member_id = _who(user)
    targets = request.app.state.schedule_targets
    return _call(request, lambda session: schedules.add(session, targets, body.target_kind, body.target_name, body.params,
                                                        body.due_local, _zone(request), utcnow(), name, member_id))


@router.patch("/{schedule_id}")
def change(schedule_id: int, body: Changes, request: Request, user: DashboardUser = Depends(dashboard_user)):
    if body.model_dump(exclude_none=True) == {}:
        raise HTTPException(422, "沒有要修改的欄位")
    targets = request.app.state.schedule_targets
    return _call(request, lambda session: schedules.modify(session, targets, schedule_id, _zone(request), utcnow(), kind=body.target_kind,
                                                           name=body.target_name, params=body.params, due_local=body.due_local))


@router.delete("/{schedule_id}")
def remove(schedule_id: int, request: Request, user: DashboardUser = Depends(dashboard_user)):
    return _call(request, lambda session: schedules.remove(session, schedule_id, utcnow()))
