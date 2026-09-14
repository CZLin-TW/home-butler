"""Dashboard lighting API using the explicitly selected HA or legacy PC route."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, model_validator

import switchbot_api
from lighting_transport import send_command as send_agent_command
from auth import verify_api_key
from hue_area_settings import apply_area_settings, upsert_area_setting
from sheets import RequestContext


router = APIRouter(prefix="/api", dependencies=[Depends(verify_api_key)])



class HueAreaUpdateRequest(BaseModel):
    display_name: str
    resource_type: Optional[str] = "grouped_light"
    hue_name: Optional[str] = ""


class HueBreatheRequest(BaseModel):
    area_id: str
    resource_type: Optional[str] = "grouped_light"


class HueAreaStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    on: Optional[bool] = None
    brightness: Optional[float] = None
    resource_type: Optional[str] = "grouped_light"
    hs_color: Optional[list[StrictFloat | StrictInt]] = Field(default=None, min_length=2, max_length=2)
    color_temp_kelvin: Optional[StrictInt] = Field(default=None, ge=1000, le=10000)

    @model_validator(mode="after")
    def validate_color(self):
        if self.hs_color is not None:
            if self.color_temp_kelvin is not None or not 0 <= self.hs_color[0] <= 360 or not 0 <= self.hs_color[1] <= 100:
                raise ValueError("Choose valid hue/saturation or white temperature")
        return self


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
        "areas": await run_in_threadpool(apply_area_settings, areas),
        "counts": result.get("counts", {}),
    }


@router.patch("/lighting/areas/{area_id}")
async def api_update_lighting_area(area_id: str, req: HueAreaUpdateRequest):
    try:
        setting = await run_in_threadpool(upsert_area_setting,
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
    if all(value is None for value in (req.on, req.brightness, req.hs_color, req.color_temp_kelvin)):
        raise HTTPException(status_code=400, detail="A lighting setting is required")
    # Old PC agents silently ignore unknown keys: reject instead of false success.
    if req.hs_color is not None or req.color_temp_kelvin is not None:
        from lighting_transport import ha_enabled
        if not ha_enabled():
            raise HTTPException(status_code=409, detail="光色控制需要更新 HA Home Butler 整合並啟用 HA 照明來源")
    try:
        message = await send_agent_command(
            "hue.set_state",
            {
                "area_id": area_id,
                "on": req.on,
                "brightness": req.brightness,
                "resource_type": req.resource_type or "grouped_light",
                **({"hs_color": req.hs_color} if req.hs_color is not None else {}),
                **({"color_temp_kelvin": req.color_temp_kelvin} if req.color_temp_kelvin is not None else {}),
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


# Legacy rule endpoints stay explicit for older clients; no Sheet reads/writes.
@router.get("/lighting/auto/rules")
async def api_lighting_auto_rules():
    return {"rules": {}, "retired": True}


@router.patch("/lighting/auto/rules/{area_id}")
async def api_set_lighting_auto_rule(area_id: str, req: LightingAutoRuleRequest):
    raise HTTPException(status_code=410, detail="HB 自動夜燈已停用，請在 Home Assistant 設定自動化")


@router.delete("/lighting/auto/rules/{area_id}")
async def api_delete_lighting_auto_rule(area_id: str):
    raise HTTPException(status_code=410, detail="HB 自動夜燈已停用；原設定保留於 Sheet")


@router.get("/lighting/auto/sensors")
async def api_lighting_auto_sensors():
    """規則設定 UI 的光感應器候選清單（「智能居家」分頁啟用中的感應器）。
    不在這裡逐台確認有沒有 lightLevel——使用者選了之後用 light-level 端點實測。"""
    try:
        ctx = RequestContext()
        await run_in_threadpool(ctx.load, ["智能居家"])
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
    """Legacy read-only probe: selected HA snapshot, otherwise native cloud status.

    The old /auto/ URL remains compatible, but no longer reads a nightlight cache.
    This endpoint cannot create or evaluate rules.
    """
    import ha_sensors
    reading = ha_sensors.by_device_id(device_id)
    if reading is not None:
        return {k: reading.get(k) for k in ("light_level", "source", "age_seconds")}
    status = await run_in_threadpool(switchbot_api.get_device_status, device_id)
    if not isinstance(status, dict) or "error" in status:
        detail = status.get("error") if isinstance(status, dict) else str(status)
        raise HTTPException(status_code=502, detail=str(detail))
    return {"light_level": status.get("lightLevel"), "source": "status", "age_seconds": None}


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
