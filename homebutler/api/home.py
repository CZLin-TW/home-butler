"""The Dashboard's home page (to-dos and food that need attention) and the weather card.

The weather is its own request because it may wait on the weather service; the rest never does.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from homebutler.api.auth import DashboardUser, dashboard_user
from homebutler.api.household import food_view, member, todo_view
from homebutler.domain import food
from homebutler.domain.todos import service as todos
from homebutler.store.models import Member

router = APIRouter(prefix="/v2")
SOON = timedelta(days=5)


@router.get("/home")
def home(request: Request, user: DashboardUser = Depends(member)):
    today = datetime.now(ZoneInfo(request.app.state.settings.timezone)).date()
    with request.app.state.store.transaction() as s:
        me = s.get(Member, user.member.id)
        due = [t for t in todos.open_todos(s, me) if t.due_date and t.due_date <= today + SOON][:5]
        items = [i for i in food.active(s, me.household_id) if i.expires_on and i.expires_on <= today + SOON]
        return {"todos": [todo_view(t) for t in due], "food": [food_view(i) for i in items]}


@router.get("/weather")
async def weather(request: Request, when: str = "today", location: str | None = None,
                  user: DashboardUser = Depends(dashboard_user)):
    service = request.app.state.butler_services.weather
    if service is None:
        raise HTTPException(503, "天氣服務未設定")
    if len(when) > 10 or (location and len(location) > 40):
        raise HTTPException(422, "查詢條件太長")
    found = await run_in_threadpool(service.summary_data, when, location)
    if "error" in found:
        raise HTTPException(502, found["error"])
    return found
