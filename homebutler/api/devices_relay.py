"""The two relays that pass through this service to Home Assistant: SwitchBot's webhook (a hint to re-read
a Hub 2; see integrations/switchbot_push.py, marked for removal) and the home theater controls."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from homebutler.api.auth import DashboardUser, dashboard_user

router = APIRouter()
MAX_WEBHOOK_BYTES = 16384


@router.post("/v2/switchbot/webhook")
async def switchbot_webhook(request: Request):
    """Unsigned by SwitchBot, so it carries no authority: at most one bounded re-read of a hub HA chose.
    Always answers the same way, telling a caller nothing."""
    raw = await request.body()
    if len(raw) <= MAX_WEBHOOK_BYTES:
        try:
            await request.app.state.hub_relay.notify(json.loads(raw))
        except ValueError:
            pass
    return {"ok": True}


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
