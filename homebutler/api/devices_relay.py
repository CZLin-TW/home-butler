"""The home theater controls, passed through this service to Home Assistant.

(SwitchBot's webhook used to come here too; since 2026-10-25 SwitchBot notifies Home Assistant directly.)"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from homebutler.api.auth import DashboardUser, dashboard_user

router = APIRouter()


async def _theater(request: Request, action: str, payload: dict) -> dict:
    message = await request.app.state.ha.theater_command(action, payload)
    if message.get("status") == "success":
        return {"agent_id": "home_assistant", **(message.get("result") if isinstance(message.get("result"), dict) else {})}
    # Unknown means it may have happened: the caller must not retry on its own.
    raise HTTPException(504 if message.get("status") == "unknown" else 503, message.get("message") or "劇院中繼失敗")


class TheaterFlags(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kef_link: bool | None = None
    tv_screen_auto: bool | None = None
    tv_avr_sync: bool | None = None


@router.get("/v2/theater")
async def theater_summary(request: Request, user: DashboardUser = Depends(dashboard_user)):
    return await _theater(request, "summary", {})


@router.post("/v2/theater/flags")
async def theater_flags(flags: TheaterFlags, request: Request, user: DashboardUser = Depends(dashboard_user)):
    if user.role != "member":
        raise HTTPException(403, "遙控器不能修改劇院設定")
    chosen = {k: v for k, v in flags.model_dump().items() if v is not None}
    if not chosen:
        raise HTTPException(400, "沒有要變更的設定")
    return await _theater(request, "set_flags", chosen)
