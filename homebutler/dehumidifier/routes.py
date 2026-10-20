"""The Dashboard's dehumidifier endpoints. Cloud calls are blocking, so these are plain (threadpool) routes."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from homebutler.api.auth import DashboardUser, dashboard_user
from homebutler.dehumidifier.service import Refused
from homebutler.store.models import utcnow

router = APIRouter(prefix="/v2/dehumidifiers")


def _service(request: Request):
    return request.app.state.modules["dehumidifier"]


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    power: str | None = None
    mode: str | None = None
    humidity: int | None = Field(default=None, ge=30, le=80)


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_mode: bool
    sensor_name: str | None = None
    duration_min: int | None = Field(default=None, ge=0, le=1440)
    threshold: int | None = Field(default=None, ge=30, le=80)
    threshold_source: str | None = None


@router.get("")
def list_units(request: Request, user: DashboardUser = Depends(dashboard_user)):
    return {"dehumidifiers": _service(request).views()}


@router.get("/history")
def history(request: Request, hours: int = 24, user: DashboardUser = Depends(dashboard_user)):
    if not 1 <= hours <= 24 * 31:
        raise HTTPException(422, "hours 需介於 1 至 744")
    return {"history": _service(request).history(utcnow() - timedelta(hours=hours))}


def _call(action):
    try:
        return action()
    except LookupError:
        raise HTTPException(404, "找不到這台除濕機") from None
    except Refused as error:
        raise HTTPException(409, str(error)) from None


@router.post("/{name}/control")
def control(name: str, body: Control, request: Request, user: DashboardUser = Depends(dashboard_user)):
    result = _call(lambda: _service(request).control(name, body.power, body.mode, body.humidity))
    if result["status"] == "success":
        return result
    # Unknown means it may have happened: the caller must not retry on its own.
    raise HTTPException(504 if result["status"] == "unknown" else 502, result["message"])


@router.put("/{name}/rule")
def set_rule(name: str, body: Rule, request: Request, user: DashboardUser = Depends(dashboard_user)):
    return _call(lambda: _service(request).set_rule(name, body.auto_mode, body.sensor_name, body.duration_min, body.threshold,
                                                    body.threshold_source))
