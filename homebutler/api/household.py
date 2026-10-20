"""The Dashboard's to-dos, recurring to-dos and food. Members only (a remote tablet has no business here).

Everything is addressed by id. A refused change answers 409 with the reason; messages for someone else
(a to-do assigned to them) are pushed on LINE after the change is saved.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from homebutler.api.auth import DashboardUser, dashboard_user
from homebutler.domain import food
from homebutler.domain.todos import service as todos
from homebutler.domain.todos import templates
from homebutler.store.models import FoodItem, LightArea, Member, Todo, TodoTemplate

log = logging.getLogger("homebutler.api")
router = APIRouter(prefix="/v2")


def member(user: DashboardUser = Depends(dashboard_user)) -> DashboardUser:
    if user.member is None:
        raise HTTPException(403, "遙控器不能使用這個功能")
    return user


def _today(request: Request) -> date:
    return datetime.now(ZoneInfo(request.app.state.settings.timezone)).date()


def _push(request: Request, notices: list) -> None:
    line = getattr(request.app.state, "line", None)
    for target, text in notices if line else []:
        try:
            line.push(target, text)
        except Exception:
            log.warning("notice not delivered")


def _result(request: Request, outcome, view=None):
    if not outcome.ok:
        raise HTTPException(409, outcome.message)
    _push(request, outcome.notices if hasattr(outcome, "notices") else [])
    return {"message": outcome.message, **({"item": view} if view is not None else {})}


# ── views ───────────────────────────────────────────────────────────────────

def todo_view(t: Todo) -> dict:
    return {"id": t.id, "title": t.title, "due_date": t.due_date.isoformat() if t.due_date else None,
            "due_time": todos.show_time(t.due_time) or None, "assignee": t.assignee.name if t.assignee else None,
            "visibility": t.visibility, "status": t.status, "source": t.source, "read_only": t.read_only,
            "light_reminder": t.light_reminder, "light_area_ids": [a.id for a in t.light_areas], "template_id": t.template_id}


def template_view(t: TodoTemplate) -> dict:
    return {"id": t.id, "title": t.title, "rule": t.rule, "weekdays": list(t.weekdays or []), "month_day": t.month_day,
            "interval_days": t.interval_days, "due_time": todos.show_time(t.due_time) or None,
            "assignee": t.assignee.name if t.assignee else None, "visibility": t.visibility, "light_reminder": t.light_reminder,
            "light_area_ids": [a.id for a in t.light_areas], "start_date": t.start_date.isoformat(),
            "end_date": t.end_date.isoformat() if t.end_date else None, "summary": templates.summary(t)}


def area_view(a: LightArea) -> dict:
    return {"id": a.id, "name": a.name, "is_default": a.is_default}


def food_view(i: FoodItem) -> dict:
    return {"id": i.id, "name": i.name, "quantity": i.quantity, "unit": i.unit,
            "expires_on": i.expires_on.isoformat() if i.expires_on else None, "added_on": i.added_on.isoformat() if i.added_on else None}


# ── to-dos ──────────────────────────────────────────────────────────────────

class NewTodo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    due_date: str | None = None
    due_time: str | None = None
    assignee: str | None = None
    visibility: str = "private"
    light_reminder: bool | None = None
    light_area_ids: list[int] | None = None


class TodoChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    due_date: str | None = None
    due_time: str | None = None           # "" clears the time
    assignee: str | None = None
    visibility: str | None = None
    light_reminder: bool | None = None
    light_area_ids: list[int] | None = None


@router.get("/todos")
def todo_page(request: Request, user: DashboardUser = Depends(member)):
    with request.app.state.store.transaction() as s:
        me = s.get(Member, user.member.id)
        return {"todos": [todo_view(t) for t in todos.open_todos(s, me)],
                "templates": [template_view(t) for t in templates.active(s, me)],
                "light_areas": [area_view(a) for a in s.scalars(select(LightArea).where(LightArea.household_id == me.household_id)) if a.usable],
                "members": [m.name for m in s.scalars(select(Member).where(Member.active.is_(True), Member.removed_at.is_(None)))]}


@router.post("/todos")
def add_todo(body: NewTodo, request: Request, user: DashboardUser = Depends(member)):
    with request.app.state.store.transaction() as s:
        outcome = todos.add(s, s.get(Member, user.member.id), title=body.title, due_date=body.due_date, due_time=body.due_time,
                            assignee=body.assignee, visibility=body.visibility, light_reminder=body.light_reminder,
                            light_area_ids=body.light_area_ids)
        view = todo_view(outcome.todo) if outcome.ok else None
    return _result(request, outcome, view)


@router.patch("/todos/{todo_id}")
def change_todo(todo_id: int, body: TodoChanges, request: Request, user: DashboardUser = Depends(member)):
    changes = body.model_dump(exclude_unset=True)
    with request.app.state.store.transaction() as s:
        outcome = todos.modify(s, s.get(Member, user.member.id), changes, todo_id=todo_id)
        view = todo_view(outcome.todo) if outcome.ok else None
    return _result(request, outcome, view)


@router.post("/todos/{todo_id}/complete")
def complete_todo(todo_id: int, request: Request, user: DashboardUser = Depends(member)):
    tz = ZoneInfo(request.app.state.settings.timezone)
    day_start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    with request.app.state.store.transaction() as s:
        outcome = todos.complete(s, s.get(Member, user.member.id), day_start, todo_id=todo_id)
    return _result(request, outcome)


# ── recurring to-dos ────────────────────────────────────────────────────────

class NewTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    rule: str
    due_time: str | None = None
    weekdays: list[int] | None = None
    month_day: int | None = None
    interval_days: int | None = None
    assignee: str | None = None
    visibility: str = "private"
    start_date: str | None = None
    end_date: str | None = None
    light_reminder: bool | None = None
    light_area_ids: list[int] | None = None


class TemplateChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    rule: str | None = None
    due_time: str | None = None
    weekdays: list[int] | None = None
    month_day: int | None = None
    interval_days: int | None = None
    assignee: str | None = None
    visibility: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    light_reminder: bool | None = None
    light_area_ids: list[int] | None = None


@router.post("/todo-templates")
def add_template(body: NewTemplate, request: Request, user: DashboardUser = Depends(member)):
    with request.app.state.store.transaction() as s:
        outcome = templates.add(s, s.get(Member, user.member.id), _today(request), **body.model_dump())
    return _result(request, outcome)


@router.patch("/todo-templates/{template_id}")
def change_template(template_id: int, body: TemplateChanges, request: Request, user: DashboardUser = Depends(member)):
    with request.app.state.store.transaction() as s:
        outcome = templates.modify(s, s.get(Member, user.member.id), _today(request), body.model_dump(exclude_unset=True),
                                   template_id=template_id)
    return _result(request, outcome)


@router.delete("/todo-templates/{template_id}")
def stop_template(template_id: int, request: Request, user: DashboardUser = Depends(member)):
    with request.app.state.store.transaction() as s:
        outcome = templates.stop(s, s.get(Member, user.member.id), template_id=template_id)
    return _result(request, outcome)


# ── food ────────────────────────────────────────────────────────────────────

class NewFood(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    quantity: int = Field(default=1, ge=1, le=9999)
    unit: str = Field(default="個", max_length=20)
    expires_on: str | None = None


class FoodChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    quantity: int | None = Field(default=None, ge=0, le=9999)
    unit: str | None = Field(default=None, max_length=20)
    expires_on: str | None = None


@router.get("/food")
def food_page(request: Request, user: DashboardUser = Depends(member)):
    with request.app.state.store.transaction() as s:
        return {"food": [food_view(i) for i in food.active(s, s.get(Member, user.member.id).household_id)]}


@router.post("/food")
def add_food(body: NewFood, request: Request, user: DashboardUser = Depends(member)):
    with request.app.state.store.transaction() as s:
        outcome = food.add(s, s.get(Member, user.member.id), _today(request), name=body.name, quantity=body.quantity,
                           unit=body.unit, expiry=body.expires_on)
    return _result(request, outcome)


@router.patch("/food/{item_id}")
def change_food(item_id: int, body: FoodChanges, request: Request, user: DashboardUser = Depends(member)):
    changes = {"name": body.name, "quantity": body.quantity, "unit": body.unit, "expiry": body.expires_on}
    with request.app.state.store.transaction() as s:
        outcome = food.modify(s, s.get(Member, user.member.id).household_id, changes, item_id=item_id)
    return _result(request, outcome)


@router.delete("/food/{item_id}")
def consume_food(item_id: int, request: Request, user: DashboardUser = Depends(member)):
    with request.app.state.store.transaction() as s:
        outcome = food.consume(s, s.get(Member, user.member.id).household_id, item_id=item_id)
    return _result(request, outcome)
