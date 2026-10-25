"""Devices, as Home Assistant shares them.

The device list follows HA: a device appears when HA shares it and is marked removed when HA stops
sharing it (only judged while HA is online, so a disconnect never removes anything). Its household is
the one linked to its HA floor, else the default. Values are HA's: an AC's state is its last command,
not a reading of the unit (IR has no feedback), and while HA is away everything is unknown.

The hand-edited 智能居家 tab only adds settings by name (location, sensor offsets, auto-off); it never
decides what exists or how it is controlled.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from homebutler.domain import households
from homebutler.store.models import AcReading, Device, DeviceEntity, DeviceSetting, Household, SensorReading, utcnow

MODE_INPUTS = {
    "自動": "heat_cool", "auto": "heat_cool", "heat_cool": "heat_cool",
    "冷氣": "cool", "制冷": "cool", "cool": "cool", "冷": "cool",
    "除濕": "dry", "乾燥": "dry", "dry": "dry",
    "送風": "fan_only", "風扇": "fan_only", "fan": "fan_only", "fan_only": "fan_only",
    "暖氣": "heat", "制熱": "heat", "heat": "heat", "暖": "heat",
}
MODE_LABELS = {"cool": "冷氣", "heat": "暖氣", "dry": "除濕", "fan_only": "送風", "heat_cool": "自動", "auto": "自動"}
FAN_LABELS = {"auto": "自動", "low": "低", "medium": "中", "high": "高"}
FAN_INPUTS = {**{label: value for value, label in FAN_LABELS.items()}, **{v: v for v in FAN_LABELS},
              "弱": "low", "小": "low", "強": "high", "大": "high"}


class Invalid(ValueError):
    pass


def ac_patch(data: dict) -> dict:
    """The command HA receives, from what a person or a schedule asked for. Turning off sends nothing
    else; temperatures are whole degrees 16–30 (a half rounds up); unknown modes or speeds are refused,
    never guessed."""
    patch = {k: data[k] for k in ("power", "temperature", "mode", "fan_speed") if data.get(k) is not None}
    patch.setdefault("power", "on")
    if patch["power"] not in ("on", "off"):
        raise Invalid("空調電源設定無效")
    if patch["power"] == "off":
        return {"power": "off"}
    if "temperature" in patch:
        value = patch["temperature"]
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise Invalid("空調溫度需介於 16 至 30 度") from None
        if type(value) is bool or not math.isfinite(number) or not 16 <= number <= 30:
            raise Invalid("空調溫度需介於 16 至 30 度")
        patch["temperature"] = math.floor(number + 0.5)
    if "mode" in patch:
        if not isinstance(patch["mode"], str) or patch["mode"] not in MODE_INPUTS:
            raise Invalid("空調模式無效")
        patch["mode"] = MODE_INPUTS[patch["mode"]]
    if "fan_speed" in patch:
        if not isinstance(patch["fan_speed"], str) or patch["fan_speed"] not in FAN_INPUTS:
            raise Invalid("空調風速無效")
        patch["fan_speed"] = FAN_INPUTS[patch["fan_speed"]]
    return patch


def _shared(state: dict) -> dict:
    """{(kind, name): {"area", "floor_id", "entities": [(ha_id, entity_id, role, label)]}} from a link state."""
    found = defaultdict(lambda: {"area": "", "floor_id": "", "entities": []})

    def add(kind, item, role, label=""):
        entry = found[(kind, item["name"])]
        entry["area"] = entry["area"] or item.get("area", "")
        entry["floor_id"] = entry["floor_id"] or item.get("floor_id", "")
        entry["entities"].append((item["id"], item["entity_id"], role, label))
    for item in state["climates"]:
        add("ac", item, "climate")
    for item in state["ir_buttons"]:
        add("ir", item, "button", item["button"])
    for item in state["environment"]:
        add("sensor", item, item["kind"])
    for item in state["observations"]:
        add("presence", item, item["kind"])
    return found


def sync_registry(session: Session, state: dict, default_household: str) -> dict:
    """Bring the device list in line with what HA shares. Does nothing while HA is offline."""
    if not state["online"]:
        return {"skipped": True}
    floors = {h.ha_floor_id: h.id for h in session.scalars(select(Household).where(Household.ha_floor_id.is_not(None)))}
    default = households.ensure(session, default_household).id
    devices = {(d.kind, d.name): d for d in session.scalars(select(Device))}
    entities = {e.ha_id: e for e in session.scalars(select(DeviceEntity))}
    now, seen_devices, seen_entities, added = utcnow(), set(), set(), 0
    for (kind, name), info in _shared(state).items():
        device = devices.get((kind, name))
        if device is None:
            device = Device(kind=kind, name=name, household_id=default)
            session.add(device)
            devices[(kind, name)] = device
            added += 1
        device.area, device.ha_floor_id = info["area"], info["floor_id"]
        device.household_id = floors.get(info["floor_id"], default)
        device.last_seen_at, device.removed_at = now, None
        session.flush()
        seen_devices.add(device.id)
        for ha_id, entity_id, role, label in info["entities"]:
            entity = entities.get(ha_id)
            if entity is None:
                entity = DeviceEntity(ha_id=ha_id)
                session.add(entity)
                entities[ha_id] = entity
            entity.device_id, entity.entity_id, entity.role, entity.label, entity.removed_at = device.id, entity_id, role, label, None
            seen_entities.add(ha_id)
    removed = 0
    for device in devices.values():
        if device.id not in seen_devices and device.removed_at is None:
            device.removed_at = now
            removed += 1
    for ha_id, entity in entities.items():
        if ha_id not in seen_entities and entity.removed_at is None:
            entity.removed_at = now
    session.flush()
    return {"added": added, "removed": removed}


def ordered_buttons(entities: list[DeviceEntity], state: dict) -> list[DeviceEntity]:
    """A remote's buttons in the order set in HA (the remote's own button list), so every remote reads the
    same way; buttons HA gives no position for follow, in the order first seen."""
    position = {b["id"]: b.get("position") for b in state.get("ir_buttons", [])}
    buttons = [e for e in entities if e.role == "button"]
    return sorted(buttons, key=lambda e: (position.get(e.ha_id) is None, position.get(e.ha_id) or 0, e.id))


def settings_by_name(session: Session) -> dict[str, DeviceSetting]:
    return {s.name: s for s in session.scalars(select(DeviceSetting).where(DeviceSetting.removed_at.is_(None)))}


def adjusted(values: dict, setting: DeviceSetting | None) -> dict:
    """Apply a sensor's offsets (from the 智能居家 tab) to HA's raw readings, once."""
    out = dict(values)
    if setting is not None:
        if out.get("temperature") is not None:
            out["temperature"] = round(out["temperature"] + setting.temperature_offset, 2)
        if out.get("humidity") is not None:
            out["humidity"] = min(100.0, max(0.0, round(out["humidity"] + setting.humidity_offset, 2)))
    return out


def sensor_values(state: dict) -> dict[str, dict]:
    """{sensor name: {kind: value}} for what is available now."""
    found = defaultdict(dict)
    for item in state["environment"]:
        if item["available"] and item["value"] is not None:
            found[item["name"]][item["kind"]] = item["value"]
    return found


def ac_view(climate: dict | None) -> dict:
    """How an AC reads to the family. Uncertain (last command unconfirmed) shows no power/mode."""
    available = bool(climate and climate["available"])
    certain = available and not climate.get("uncertain")
    mode = climate.get("hvac_mode") if certain else None
    return {"available": available, "uncertain": available and not certain,
            "power": ("off" if mode == "off" else "on") if mode else None,
            "mode": MODE_LABELS.get(mode) if mode and mode != "off" else None,
            "temperature": climate.get("temperature") if certain else None,
            "fan": FAN_LABELS.get(climate.get("fan_mode")) if certain else None,
            "updated_at": climate.get("source_updated_at") if climate else None}


def sample(session: Session, state: dict, at: datetime) -> dict:
    """One history sample of every sensor and AC that has a known value now."""
    if not state["online"]:
        return {"sensors": 0, "acs": 0}
    settings = settings_by_name(session)
    devices = {(d.kind, d.name): d for d in session.scalars(select(Device).where(Device.removed_at.is_(None)))}
    sensors = acs = 0
    for name, values in sensor_values(state).items():
        device = devices.get(("sensor", name))
        if device is None:
            continue
        values = adjusted(values, settings.get(name))
        session.add(SensorReading(device_id=device.id, t=at, temperature=values.get("temperature"),
                                  humidity=values.get("humidity"), co2=values.get("co2"), light_level=values.get("light_level")))
        sensors += 1
    for climate in state["climates"]:
        device = devices.get(("ac", climate["name"]))
        view = ac_view(climate)
        if device is None or view["power"] is None:
            continue
        session.add(AcReading(device_id=device.id, t=at, power=view["power"], mode=view["mode"] or "",
                              temperature=view["temperature"], fan=view["fan"] or ""))
        acs += 1
    return {"sensors": sensors, "acs": acs}
