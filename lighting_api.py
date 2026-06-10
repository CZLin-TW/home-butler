"""Dashboard lighting API backed by the local PC agent."""

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import lighting_auto
import switchbot_api
from agent_ws import send_agent_command
from auth import verify_api_key
from hue_area_settings import apply_area_settings, upsert_area_setting
from sheets import RequestContext


router = APIRouter(prefix="/api", dependencies=[Depends(verify_api_key)])

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


class HueAreaUpdateRequest(BaseModel):
    display_name: str
    resource_type: Optional[str] = "grouped_light"
    hue_name: Optional[str] = ""


class HueBreatheRequest(BaseModel):
    area_id: str
    resource_type: Optional[str] = "grouped_light"


class HueAreaStateRequest(BaseModel):
    on: Optional[bool] = None
    brightness: Optional[float] = None
    resource_type: Optional[str] = "grouped_light"


class HueSceneRecallRequest(BaseModel):
    action: Optional[str] = "active"
    resource_type: Optional[str] = "scene"


class HueAreaEffectRequest(BaseModel):
    effect: str
    resource_type: Optional[str] = "grouped_light"


class HueAreaNotificationRequest(BaseModel):
    notification: Optional[str] = "alert:breathe"
    resource_type: Optional[str] = "grouped_light"


class LightingAutoRuleRequest(BaseModel):
    enabled: bool
    sensor_device_id: str = ""
    sensor_name: str = ""
    threshold: int = 5
    scene_id: str = ""
    scene_name: str = ""
    scene_type: str = "scene"
    scene_action: str = "active"
    brightness: int = 50
    start_time: str = "18:00"
    end_time: str = "06:00"
    area_name: str = ""


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


@router.patch("/lighting/areas/{area_id}/state")
async def api_set_lighting_area_state(area_id: str, req: HueAreaStateRequest):
    if req.on is None and req.brightness is None:
        raise HTTPException(status_code=400, detail="on or brightness is required")
    try:
        message = await send_agent_command(
            "hue.set_state",
            {
                "area_id": area_id,
                "on": req.on,
                "brightness": req.brightness,
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


@router.post("/lighting/scenes/{scene_id}/recall")
async def api_recall_lighting_scene(scene_id: str, req: HueSceneRecallRequest):
    if not scene_id:
        raise HTTPException(status_code=400, detail="scene_id is required")
    try:
        message = await send_agent_command(
            "hue.recall_scene",
            {
                "scene_id": scene_id,
                "action": req.action or "active",
                "resource_type": req.resource_type or "scene",
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


@router.post("/lighting/areas/{area_id}/effect")
async def api_set_lighting_area_effect(area_id: str, req: HueAreaEffectRequest):
    if not area_id:
        raise HTTPException(status_code=400, detail="area_id is required")
    if not req.effect:
        raise HTTPException(status_code=400, detail="effect is required")
    try:
        message = await send_agent_command(
            "hue.set_effect",
            {
                "area_id": area_id,
                "effect": req.effect,
                "resource_type": req.resource_type or "grouped_light",
            },
            required_capability="hue",
            timeout=20.0,
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


@router.post("/lighting/areas/{area_id}/notification")
async def api_send_lighting_area_notification(area_id: str, req: HueAreaNotificationRequest):
    if not area_id:
        raise HTTPException(status_code=400, detail="area_id is required")
    try:
        message = await send_agent_command(
            "hue.notify",
            {
                "area_id": area_id,
                "notification": req.notification or "alert:breathe",
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


# ── 自動夜燈規則 ────────────────────────────────────────

@router.get("/lighting/auto/rules")
async def api_lighting_auto_rules():
    return {"rules": lighting_auto.get_all_rules()}


@router.patch("/lighting/auto/rules/{area_id}")
async def api_set_lighting_auto_rule(area_id: str, req: LightingAutoRuleRequest):
    if not area_id:
        raise HTTPException(status_code=400, detail="area_id is required")
    if not (1 <= req.threshold <= 20):
        raise HTTPException(status_code=400, detail="亮度門檻需在 1~20")
    if not (1 <= req.brightness <= 100):
        raise HTTPException(status_code=400, detail="開燈亮度需在 1~100")
    if not _TIME_RE.match(req.start_time) or not _TIME_RE.match(req.end_time):
        raise HTTPException(status_code=400, detail="時間格式需為 HH:MM")
    if req.start_time == req.end_time:
        raise HTTPException(status_code=400, detail="開始與結束時間不可相同")
    if req.enabled and (not req.sensor_device_id or not req.scene_id):
        raise HTTPException(status_code=400, detail="啟用時需選擇光感應器與場景")
    try:
        rule = lighting_auto.set_rule(
            area_id,
            enabled=req.enabled,
            sensor_device_id=req.sensor_device_id,
            sensor_name=req.sensor_name,
            threshold=req.threshold,
            scene_id=req.scene_id,
            scene_name=req.scene_name,
            scene_type=req.scene_type,
            scene_action=req.scene_action,
            brightness=req.brightness,
            start_time=req.start_time,
            end_time=req.end_time,
            area_name=req.area_name,
        )
        return {"ok": True, "rule": rule}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/lighting/auto/rules/{area_id}")
async def api_delete_lighting_auto_rule(area_id: str):
    try:
        lighting_auto.delete_rule(area_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/lighting/auto/sensors")
async def api_lighting_auto_sensors():
    """規則設定 UI 的光感應器候選清單（「智能居家」分頁啟用中的感應器）。
    不在這裡逐台確認有沒有 lightLevel——使用者選了之後用 light-level 端點實測。"""
    try:
        ctx = RequestContext()
        ctx.load()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    sensors = []
    for d in ctx.get("智能居家"):
        if d.get("狀態") == "啟用" and d.get("類型") == "感應器" and d.get("Device ID"):
            sensors.append({
                "name": d.get("名稱", ""),
                "location": d.get("位置", ""),
                "device_id": d.get("Device ID", ""),
            })
    return {"sensors": sensors}


@router.get("/lighting/auto/sensors/{device_id}/light-level")
async def api_lighting_auto_sensor_light_level(device_id: str):
    """實測某感應器當下的 lightLevel（1~20），給 UI 調門檻時參考。
    回 null 表示該設備不回報亮度（不是 Hub 2）。"""
    status = switchbot_api.get_device_status(device_id)
    if not isinstance(status, dict) or "error" in status:
        detail = status.get("error") if isinstance(status, dict) else str(status)
        raise HTTPException(status_code=502, detail=str(detail))
    return {"light_level": status.get("lightLevel")}


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
