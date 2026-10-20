"""The Dashboard's lighting page, through Home Assistant's Hue integration (HA decides what is allowed;
this passes on only the known actions). Listing also keeps the light-area table up to date."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, model_validator

from homebutler.api.auth import DashboardUser, dashboard_user
from homebutler.domain import households, lighting_areas
from homebutler.store.models import LightArea

router = APIRouter(prefix="/v2/lighting")


async def _hue(request: Request, action: str, payload: dict) -> dict:
    result = await request.app.state.ha.hue_command(action, payload)
    if result.get("status") == "success":
        return result.get("result") if isinstance(result.get("result"), dict) else {}
    # Unknown means it may have happened: the caller must not retry on its own.
    raise HTTPException(504 if result.get("status") == "unknown" else 503, result.get("message") or "照明指令失敗")


@router.get("")
async def areas(request: Request, user: DashboardUser = Depends(dashboard_user)):
    found = (await _hue(request, "hue.list_areas", {})).get("areas") or []
    from starlette.concurrency import run_in_threadpool

    def sync():
        with request.app.state.store.transaction() as s:
            home = households.ensure(s, request.app.state.settings.default_household)
            rows = lighting_areas.discover(s, found, home.id)
            return [{**area, "area_id": rows[area["id"]].id, "display_name": rows[area["id"]].display_name,
                     "reminders": rows[area["id"]].enabled, "is_default": rows[area["id"]].is_default}
                    for area in found if area.get("id") in rows]
    return {"areas": await run_in_threadpool(sync)}


class AreaSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, max_length=80)
    reminders: bool | None = None
    is_default: bool | None = None


@router.patch("/areas/{area_id}")
def area_settings(area_id: int, body: AreaSettings, request: Request, user: DashboardUser = Depends(dashboard_user)):
    if user.member is None:
        raise HTTPException(403, "遙控器不能修改照明設定")
    with request.app.state.store.transaction() as s:
        area = s.get(LightArea, area_id)
        if area is None:
            raise HTTPException(404, "找不到這個照明區域")
        if body.display_name is not None:
            area.display_name = body.display_name.strip()
        if body.reminders is not None:
            area.enabled = body.reminders
        if body.is_default:
            s.query(LightArea).filter(LightArea.household_id == area.household_id).update({"is_default": False})
        if body.is_default is not None:
            area.is_default = body.is_default
        return {"area_id": area.id, "display_name": area.display_name, "reminders": area.enabled, "is_default": area.is_default}


class State(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    on: bool | None = None
    brightness: float | None = Field(default=None, ge=0, le=100)
    hs_color: list[StrictFloat | StrictInt] | None = Field(default=None, min_length=2, max_length=2)
    color_temp_kelvin: StrictInt | None = Field(default=None, ge=1000, le=10000)
    resource_type: str = "grouped_light"

    @model_validator(mode="after")
    def one_colour(self):
        if self.hs_color is not None and (self.color_temp_kelvin is not None or not 0 <= self.hs_color[0] <= 360
                                          or not 0 <= self.hs_color[1] <= 100):
            raise ValueError("Choose valid hue/saturation or white temperature")
        if all(v is None for v in (self.on, self.brightness, self.hs_color, self.color_temp_kelvin)):
            raise ValueError("A lighting setting is required")
        return self


class Effect(BaseModel):
    effect: str = Field(min_length=1, max_length=40)
    resource_type: str = "grouped_light"


class Notify(BaseModel):
    notification: str = Field(default="alert:breathe", max_length=40)
    resource_type: str = "grouped_light"


class Scene(BaseModel):
    action: str = "active"
    resource_type: str = "scene"


@router.patch("/{target}/state")
async def set_state(target: str, body: State, request: Request, user: DashboardUser = Depends(dashboard_user)):
    return await _hue(request, "hue.set_state", {"area_id": target, **body.model_dump(exclude_none=True)})


@router.post("/{target}/effect")
async def set_effect(target: str, body: Effect, request: Request, user: DashboardUser = Depends(dashboard_user)):
    return await _hue(request, "hue.set_effect", {"area_id": target, **body.model_dump()})


@router.post("/{target}/notification")
async def notify(target: str, body: Notify, request: Request, user: DashboardUser = Depends(dashboard_user)):
    return await _hue(request, "hue.notify", {"area_id": target, **body.model_dump()})


@router.post("/scenes/{scene_id}/recall")
async def recall(scene_id: str, body: Scene, request: Request, user: DashboardUser = Depends(dashboard_user)):
    return await _hue(request, "hue.recall_scene", {"scene_id": scene_id, **body.model_dump()})
