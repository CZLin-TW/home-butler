"""Dashboard lighting API backed by the local PC agent."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agent_ws import send_agent_command
from auth import verify_api_key
from hue_area_settings import apply_area_settings, upsert_area_setting


router = APIRouter(prefix="/api", dependencies=[Depends(verify_api_key)])


class HueAreaUpdateRequest(BaseModel):
    display_name: str
    resource_type: Optional[str] = "grouped_light"
    hue_name: Optional[str] = ""


class HueBreatheRequest(BaseModel):
    area_id: str
    resource_type: Optional[str] = "grouped_light"


def _agent_error(status_code: int, e: Exception) -> HTTPException:
    return HTTPException(status_code=status_code, detail=str(e))


@router.get("/lighting/areas")
async def api_lighting_areas():
    try:
        message = await send_agent_command(
            "hue.list_areas",
            {},
            required_capability="hue",
            timeout=20.0,
        )
    except TimeoutError as e:
        raise _agent_error(504, e)
    except Exception as e:
        raise _agent_error(503, e)

    if message.get("status") != "ok":
        raise HTTPException(status_code=502, detail=message.get("error") or "Hue command failed")

    result = message.get("result") if isinstance(message.get("result"), dict) else {}
    areas = result.get("areas") if isinstance(result.get("areas"), list) else []
    return {
        "agent_id": message.get("agent_id", ""),
        "areas": apply_area_settings(areas),
        "counts": result.get("counts", {}),
    }


@router.patch("/lighting/areas/{area_id}")
async def api_update_lighting_area(area_id: str, req: HueAreaUpdateRequest):
    try:
        setting = upsert_area_setting(
            area_id,
            req.display_name,
            resource_type=req.resource_type or "grouped_light",
            hue_name=req.hue_name or "",
        )
        return {"ok": True, "setting": setting}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/lighting/breathe")
async def api_lighting_breathe(req: HueBreatheRequest):
    if not req.area_id:
        raise HTTPException(status_code=400, detail="area_id is required")
    try:
        message = await send_agent_command(
            "hue.breathe",
            {
                "resource_id": req.area_id,
                "resource_type": req.resource_type or "grouped_light",
            },
            required_capability="hue",
            timeout=15.0,
        )
    except TimeoutError as e:
        raise _agent_error(504, e)
    except Exception as e:
        raise _agent_error(503, e)

    if message.get("status") != "ok":
        raise HTTPException(status_code=502, detail=message.get("error") or "Hue command failed")
    return {
        "ok": True,
        "agent_id": message.get("agent_id", ""),
        "result": message.get("result", {}),
    }
