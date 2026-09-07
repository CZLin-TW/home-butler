"""HomeKit adapter capability: explicit AC commands, no assistant or personal data.

Reads use the existing background-hydrated cache. Commands revalidate the current
Sheet and merge partial settings before calling the original AC handler once.
"""
import hashlib
import json
import math
import secrets
import time
from collections import OrderedDict
from threading import Lock
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import config
import device_status
import sensor_state
from handlers.device import control_ac_result
from sheets import RequestContext


def verify_homebridge_key(x_api_key: str = Header(default="")):
    key = getattr(config, "HOMEBRIDGE_API_KEY", "")
    other_keys = (config.HOME_BUTLER_API_KEY, config.DEVICE_VOICE_API_KEY)
    if len(key) < 32 or any(key == other for other in other_keys if other):
        raise HTTPException(503, "Homebridge access is not configured")
    if not x_api_key or not secrets.compare_digest(x_api_key.encode(), key.encode()):
        raise HTTPException(401, "Invalid or missing X-API-Key header")


router = APIRouter(prefix="/api/homebridge", dependencies=[Depends(verify_homebridge_key)])
_command_lock = Lock()
_results = OrderedDict()
MODE = {"自動": "auto", "冷氣": "cool", "除濕": "dry", "送風": "fan", "暖氣": "heat"}
FAN = {"自動": "auto", "低": "low", "中": "medium", "高": "high"}


def allowed_names():
    try:
        names = json.loads(getattr(config, "HOMEBRIDGE_DEVICE_NAMES", "[]"))
        if (not isinstance(names, list) or not names or len(names) > 20
                or any(not isinstance(n, str) or not n.strip() for n in names)
                or len(set(names)) != len(names)):
            raise ValueError()
        return names
    except (ValueError, TypeError):
        raise HTTPException(503, "Configure HOMEBRIDGE_DEVICE_NAMES as a JSON name list") from None


def device_key(row):
    return hashlib.sha256(str(row.get("Device ID", "")).encode()).hexdigest()[:32]


def exposed_rows(rows):
    names = allowed_names()
    active = [r for r in rows if r.get("狀態") == "啟用"]
    result = []
    for name in names:
        matches = [r for r in active if r.get("名稱") == name]
        if len(matches) != 1:
            continue
        row = matches[0]
        device_id = str(row.get("Device ID", "")).strip()
        if (row.get("類型") == "空調" and device_id
                and sum(str(r.get("Device ID", "")).strip() == device_id for r in active) == 1):
            result.append(row)
    return result


def number(value, minimum, maximum):
    try:
        v = float(value)
        return v if math.isfinite(v) and minimum <= v <= maximum else None
    except (ValueError, TypeError):
        return None


def project(row, cached=None):
    cached = cached or {}
    def value(cache_key, sheet_key):
        return cached.get(cache_key, row.get(sheet_key, ""))
    power = value("lastPower", "最後電源")
    return {
        "id": device_key(row), "name": row["名稱"], "location": row.get("位置", ""),
        "power": power if power in ("on", "off") else None,
        "temperature": number(value("lastTemperature", "最後溫度"), 16, 30),
        "mode": MODE.get(value("lastMode", "最後模式")),
        "fan_speed": FAN.get(value("lastFanSpeed", "最後風速")),
        "updated_at": value("lastUpdatedAt", "最後更新時間"),
        "uncertain": bool(cached.get("stateUncertain", False)),
        "state_source": "last_command",
    }


@router.get("/devices")
def get_devices():
    rows = device_status.catalog_rows()
    if not rows:
        raise HTTPException(503, "Device catalog is warming up")
    selected = exposed_rows(rows)
    statuses = device_status.snapshot()
    # Only sensors in selected AC locations, never credentials/history/family data.
    locations = {r.get("位置") for r in selected if r.get("位置")}
    sensors = []
    for name, state in sensor_state.snapshot(include_history=False).items():
        if state.get("location") in locations:
            sensors.append({"name": name, "location": state["location"],
                            "temperature": number(state.get("current", {}).get("temp"), -50, 100),
                            "online": bool(state.get("online")),
                            "updated_at": state.get("last_polled_at", 0)})
    return {"protocol": 1, "devices": [project(r, statuses.get(r["名稱"])) for r in selected],
            "sensors": sensors}


class AcPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    power: Literal["on", "off"] | None = None
    temperature: int | None = Field(default=None, ge=16, le=30, strict=True)
    mode: Literal["auto", "cool", "dry", "fan", "heat"] | None = None
    fan_speed: Literal["auto", "low", "medium", "high"] | None = None


def merge_command(req, row):
    patch = req.model_dump(exclude={"request_id"}, exclude_unset=True)
    if not patch or any(v is None for v in patch.values()):
        raise HTTPException(422, "Provide non-null AC settings")
    if patch.get("power") == "off":
        if len(patch) != 1:
            raise HTTPException(422, "Power off cannot be combined with other settings")
        return {"device_name": row["名稱"], "power": "off"}
    previous = project(row)
    power = patch.get("power", previous["power"])
    if power != "on":
        raise HTTPException(409, "Turn on the AC before changing its settings")
    mode = patch.get("mode", previous["mode"])
    temperature = patch.get("temperature", previous["temperature"])
    fan = patch.get("fan_speed", previous["fan_speed"])
    # Never silently reset omitted parameters to the legacy handler defaults.
    if mode is None or temperature is None or fan is None or int(temperature) != temperature:
        raise HTTPException(409, "AC settings are unknown; set a complete state in Dashboard first")
    return {"device_name": row["名稱"], "power": "on", "mode": mode,
            "temperature": int(temperature), "fan_speed": fan}


@router.post("/devices/{device_id}/ac")
def patch_ac(device_id: str, req: AcPatch):
    if not _command_lock.acquire(blocking=False):
        raise HTTPException(409, "Another Homebridge command is in progress")
    try:
        allowed_names()  # Reject disabled configuration even for a cached command.
        now = time.monotonic()
        for key in list(_results):
            if now - _results[key][0] > 600:
                del _results[key]
        request_id = str(req.request_id)
        fingerprint = (device_id, req.model_dump_json())
        if request_id in _results:
            _, old, response = _results[request_id]
            if old != fingerprint:
                raise HTTPException(409, "Request ID reused for different settings")
            return response
        ctx = RequestContext()
        ctx.load(["智能居家", "排程指令"])
        matches = [r for r in exposed_rows(ctx.get("智能居家")) if device_key(r) == device_id]
        if len(matches) != 1:
            raise HTTPException(404, "AC is not available to Homebridge")
        row = matches[0]
        data = merge_command(req, row)
        # Legacy name lookup has a single-device fallback and normalizes names.
        # Limit this context to the exact authorized stable ID before dispatch.
        ctx.set("智能居家", [row])
        response = {"status": "unknown", "message": "Command outcome is uncertain; do not retry automatically",
                    "device": None}
        # Record before the side effect. Bounded, process-local deduplication only.
        if len(_results) >= 256:
            _results.popitem(last=False)
        _results[request_id] = (now, fingerprint, response)
        ctx._ac_state_saved = False
        try:
            result = control_ac_result(data, ctx)
            if result.status == "success" and ctx._ac_state_saved:
                response = {"status": "success", "message": "Command accepted", "device": project(row)}
            elif result.status == "failed":
                response = {"status": "failed", "message": "Device command failed; check the device", "device": None}
        except Exception:
            pass  # A side effect may already have happened; never retry or leak exceptions.
        if response["status"] != "success":
            device_status.update(row["名稱"], {"stateUncertain": True})
        _results[request_id] = (now, fingerprint, response)
        return response
    finally:
        _command_lock.release()
