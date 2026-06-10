"""Dashboard theater API：經 PC agent（capability=theater）轉送到同機的 theater-agent。

theater-agent 是純內網服務（192.168.68.55:8080，無 port forwarding），Render 連不到；
PC agent 的 WebSocket 是 agent 主動外連，所以指令走 send_agent_command 中繼：

    Dashboard → 這裡 → agent_ws.send_agent_command → PC agent → localhost:8080
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


def _agent_error(status_code: int, e: Exception) -> HTTPException:
    return HTTPException(status_code=status_code, detail=str(e))


async def _theater_command(command_type: str, payload: dict) -> dict:
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
