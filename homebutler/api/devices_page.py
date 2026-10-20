"""The Dashboard's devices: one request for the whole page, cheap enough to repeat every few seconds
(it reads only the database and the latest Home Assistant snapshot, never a cloud service), the
charts' history separately, and the AC / IR commands.

Every part carries when it was last known, so the page can say "updated N seconds ago" and mark what
is old instead of showing it as current.
"""
from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from homebutler.api.auth import DashboardUser, dashboard_user
from homebutler.domain import computers, devices, schedules
from homebutler.store.models import AcReading, Computer, ComputerMetric, Device, DeviceEntity, SensorReading, utcnow

router = APIRouter(prefix="/v2")
AC_OPTIONS = {"modes": [{"value": v, "label": l} for v, l in (("cool", "冷氣"), ("heat", "暖氣"), ("dry", "除濕"), ("fan_only", "送風"),
                                                               ("heat_cool", "自動"))],
              "fan_speeds": [{"value": v, "label": l} for v, l in devices.FAN_LABELS.items()], "temperature": {"min": 16, "max": 30}}


def _devices(session, kind):
    rows = session.execute(select(Device, DeviceEntity).join(DeviceEntity, DeviceEntity.device_id == Device.id)
                           .where(Device.kind == kind, Device.removed_at.is_(None), DeviceEntity.removed_at.is_(None))
                           .order_by(Device.name, DeviceEntity.id))
    found: dict[int, tuple] = {}
    for device, entity in rows:
        found.setdefault(device.id, (device, []))[1].append(entity)
    return list(found.values())


def page(request: Request) -> dict:
    app = request.app
    state, now = app.state.ha.state(), utcnow()
    zone = ZoneInfo(app.state.settings.timezone)
    climates = {c["id"]: c for c in state["climates"]}
    buttons = {b["id"]: b for b in state["ir_buttons"]}
    live = devices.sensor_values(state)
    with app.state.store.transaction() as s:
        settings = devices.settings_by_name(s)

        def location(device):
            setting = settings.get(device.name)
            return (setting.location if setting and setting.location else "") or device.area
        acs = [{"name": d.name, "location": location(d), **devices.ac_view(next((climates.get(e.ha_id) for e in es if e.role == "climate"), None))}
               for d, es in _devices(s, "ac")]
        irs = [{"name": d.name, "location": location(d),
                "buttons": [{"label": e.label, "available": bool(buttons.get(e.ha_id, {}).get("available"))} for e in es if e.role == "button"]}
               for d, es in _devices(s, "ir")]
        sensors = [{"name": d.name, "location": location(d), "available": bool(live.get(d.name)),
                    **devices.adjusted(live.get(d.name, {}), settings.get(d.name))} for d, _ in _devices(s, "sensor")]
        machines = [{"id": c.id, "hostname": c.hostname, "ip": c.ip, "cpu_model": c.cpu_model, "gpu_model": c.gpu_model,
                     "online": computers.online(c, now), "last_heartbeat_at": c.last_heartbeat_at, "current": c.current}
                    for c in s.scalars(select(Computer).order_by(Computer.id))]
        planned = [schedules.as_dict(x, zone) for x in schedules.visible(s)]
        auto_off = schedules.auto_off_view(s, state, now, zone)
    module = app.state.modules.get("dehumidifier")
    return {"generated_at": now,
            "home_assistant": {k: state[k] for k in ("connected", "online", "received_at", "age_seconds", "stale_after_seconds", "capabilities")},
            "acs": acs, "irs": irs, "sensors": sensors, "presence": state["observations"],
            "dehumidifiers": module.views() if module else None, "computers": machines,
            "schedules": planned, "auto_off": auto_off, "automations": app.state.ha.automation_log(),
            "options": {"ac": AC_OPTIONS, "schedule_targets": sorted(app.state.schedule_targets)}}


@router.get("/devices")
def devices_page(request: Request, user: DashboardUser = Depends(dashboard_user)):
    return page(request)


@router.get("/history")
def history(request: Request, hours: int = 24, parts: str = "sensors,acs,computers,dehumidifiers",
            user: DashboardUser = Depends(dashboard_user)):
    """`parts` limits what is sent (computers report every minute, so theirs is the largest)."""
    if not 1 <= hours <= 24 * 31:
        raise HTTPException(422, "hours 需介於 1 至 744")
    wanted = set(parts.split(","))
    since = utcnow() - timedelta(hours=hours)
    out = {"sensors": {}, "acs": {}, "computers": {}, "dehumidifiers": {}}
    with request.app.state.store.transaction() as s:
        names = {d.id: d.name for d in s.scalars(select(Device))}
        for r in (s.scalars(select(SensorReading).where(SensorReading.t >= since).order_by(SensorReading.device_id, SensorReading.t)) if "sensors" in wanted else []):
            out["sensors"].setdefault(names.get(r.device_id, "?"), []).append(
                {"t": r.t, "temperature": r.temperature, "humidity": r.humidity, "co2": r.co2, "light_level": r.light_level})
        for r in (s.scalars(select(AcReading).where(AcReading.t >= since).order_by(AcReading.device_id, AcReading.t)) if "acs" in wanted else []):
            out["acs"].setdefault(names.get(r.device_id, "?"), []).append(
                {"t": r.t, "power": r.power, "mode": r.mode, "temperature": r.temperature, "fan": r.fan})
        for r in (s.scalars(select(ComputerMetric).where(ComputerMetric.t >= since).order_by(ComputerMetric.computer_id, ComputerMetric.t))
                  if "computers" in wanted else []):
            out["computers"].setdefault(str(r.computer_id), []).append(
                {k: getattr(r, k) for k in ("t", "cpu_pct", "ram_pct", "gpu_pct", "cpu_temp", "gpu_temp", "soc_temp",
                                            "mem_pressure_pct", "mem_pressure_level", "power_w")})
    module = request.app.state.modules.get("dehumidifier")
    if module and "dehumidifiers" in wanted:
        out["dehumidifiers"] = module.history(since)
    return out


class AcCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    power: str | None = None
    temperature: float | None = None
    mode: str | None = None
    fan_speed: str | None = None


class IrCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    button: str = Field(min_length=1, max_length=60)


def _send(request: Request, kind: str, name: str, params: dict) -> dict:
    target = request.app.state.schedule_targets[kind]
    with request.app.state.store.transaction() as s:
        try:
            _, clean = target.check(s, name, params)
        except schedules.Invalid as error:
            raise HTTPException(409, str(error)) from None
    result = target.run(name, clean)
    if result.get("status") == "success":
        if kind == "ac":
            try:
                request.app.state.scheduler.reconcile()       # its automatic shutdown shows on the next read
            except Exception:
                pass
        return {"status": "success", "message": result.get("message", "")}
    # Unknown means it may have happened: the caller must not retry on its own.
    raise HTTPException(504 if result.get("status") == "unknown" else 502, result.get("message") or "指令失敗")


@router.post("/devices/ac/{name}")
def ac_command(name: str, body: AcCommand, request: Request, user: DashboardUser = Depends(dashboard_user)):
    return _send(request, "ac", name, body.model_dump(exclude_none=True))


@router.post("/devices/ir/{name}")
def ir_command(name: str, body: IrCommand, request: Request, user: DashboardUser = Depends(dashboard_user)):
    return _send(request, "ir", name, {"button": body.button})
