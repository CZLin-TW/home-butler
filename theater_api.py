"""Dashboard theater API：把指令中繼到純內網的 theater-agent。

theater-agent 沒有 port forwarding，Render 連不到，所以一定要經過某個從內網主動
外連的中繼。目前有兩條，由 THEATER_VIA_HA 決定：

    Dashboard → 這裡 → home_assistant_api（HA 主動 WSS）→ HA → theater-agent
    Dashboard → 這裡 → agent_ws.send_agent_command → PC agent → localhost:8080

HA 那條走獨立的中繼車道，不與空調指令共用 in-flight slot，所以開一次裝置頁不會
讓空調指令回「忙碌中」。兩條的回傳形狀相同，Dashboard 的契約不因切換而改變。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agent_ws import send_agent_command
from auth import verify_api_key


router = APIRouter(prefix="/api", dependencies=[Depends(verify_api_key)])


class TheaterFlagsRequest(BaseModel):
    kef_link: Optional[bool] = None
    tv_screen_auto: Optional[bool] = None
    tv_avr_sync: Optional[bool] = None


def _agent_error(status_code: int, e: Exception) -> HTTPException:
    return HTTPException(status_code=status_code, detail=str(e))


async def _theater_via_ha(action: str, payload: dict) -> dict:
    from home_assistant_api import link
    message = await link.theater_command(action, payload)
    status = message.get("status")
    if status == "success":
        result = message.get("result") if isinstance(message.get("result"), dict) else {}
        return {"agent_id": "home_assistant", **result}
    detail = message.get("message") or "theater relay failed"
    # Unknown means the call may have landed; the caller must not retry on its own.
    raise HTTPException(status_code=504 if status == "unknown" else 503, detail=detail)


async def _theater_command(command_type: str, payload: dict) -> dict:
    import ha_theater
    if ha_theater.enabled():
        action = {"theater.summary": "summary", "theater.set_flags": "set_flags"}[command_type]
        return await _theater_via_ha(action, payload.get("flags") if action == "set_flags" else {})
    try:
        message = await send_agent_command(
            command_type,
            payload,
            required_capability="theater",
            timeout=20.0,
        )
    except TimeoutError as e:
        raise _agent_error(504, e)
    except Exception as e:
        raise _agent_error(503, e)

    if message.get("status") != "ok":
        raise HTTPException(status_code=502, detail=message.get("error") or "theater command failed")

    result = message.get("result") if isinstance(message.get("result"), dict) else {}
    return {"agent_id": message.get("agent_id", ""), **result}


@router.get("/theater/summary")
async def api_theater_summary():
    """聚合狀態：flags + monitor 狀態 + 設備狀態 + 兩份 log 尾端。

    agent_id（= PC hostname）一併回傳，Dashboard 用它把劇院區塊掛到正確的 PC 卡片。"""
    return await _theater_command("theater.summary", {})


@router.post("/theater/flags")
async def api_theater_set_flags(req: TheaterFlagsRequest):
    flags = {key: value for key, value in req.model_dump().items() if value is not None}
    if not flags:
        raise HTTPException(status_code=400, detail="No flags provided")
    return await _theater_command("theater.set_flags", {"flags": flags})
