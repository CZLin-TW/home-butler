"""The facts filled into the butler's prompt, read from the database and Home Assistant.

The wording of each line follows the old system's, since the prompt's examples and rules refer to it.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import tzinfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from homebutler.assistant.prompt import DEFAULT_STYLE
from homebutler.domain import devices, food, members, schedules
from homebutler.domain.todos import service as todos
from homebutler.store.models import Device, DeviceEntity, LightArea, Member

KIND_LABELS = {"ac": "空調", "ir": "IR", "sensor": "感應器", "dehumidifier": "除濕機"}
WEEKDAY = "一二三四五六日"


def family(session: Session) -> str:
    return "、".join(f"{m.name}（稱謂：{m.title}）" for m in members.active(session))


def food_info(session: Session, member: Member) -> str:
    items = food.active(session, member.household_id)
    if not items:
        return "目前庫存是空的"
    return "、".join(f"{i.name} {i.quantity}{i.unit}（過期日 {i.expires_on.isoformat() if i.expires_on else ''}）" for i in items)


def todo_info(session: Session, member: Member) -> str:
    found = todos.open_todos(session, member)
    if not found:
        return "目前沒有待辦事項"
    lines = []
    for t in found:
        time_part = f" {todos.show_time(t.due_time)}" if t.due_time else ""
        kind = "（私人）" if t.visibility == "private" else "（公開）"
        who = t.assignee.name if t.assignee else ""
        lines.append(f"{t.title}／{who}／{t.due_date.isoformat() if t.due_date else ''}{time_part}{kind}／todo_id={t.id}")
    return "、".join(lines)


def _ac_state(climate: dict | None) -> str:
    """The AC's state as HA reports it, so "one degree lower" can be worked out; unknown says so."""
    view = devices.ac_view(climate)
    if view["power"] is None:
        return "，HA 狀態未知，不可據舊設定推算相對溫度"
    if view["power"] == "off":
        return "，目前狀態：關閉"
    parts = [f"{view['temperature']}°C" if view["temperature"] is not None else "", view["mode"] or "",
             f"風速{view['fan']}" if view["fan"] else ""]
    return f"，目前狀態：開機 {' '.join(p for p in parts if p) or '開啟'}"


def device_info(session: Session, state: dict, extra: list[dict]) -> str:
    """Every device HA shares (ACs with their state, IR remotes with their buttons, sensors), plus those
    of optional modules (`extra`: {"name", "kind", "location"})."""
    settings = devices.settings_by_name(session)
    climates = {c["id"]: c for c in state["climates"]}
    rows = session.execute(select(Device, DeviceEntity).join(DeviceEntity, DeviceEntity.device_id == Device.id)
                           .where(Device.removed_at.is_(None), DeviceEntity.removed_at.is_(None), Device.kind.in_(("ac", "ir", "sensor")))
                           .order_by(Device.kind, Device.name, DeviceEntity.id))
    found: dict[int, dict] = {}
    for device, entity in rows:
        entry = found.setdefault(device.id, {"device": device, "buttons": [], "climate": None})
        if entity.role == "button":
            entry["buttons"].append(entity.label)
        elif entity.role == "climate":
            entry["climate"] = climates.get(entity.ha_id)
    lines = []
    for entry in found.values():
        device = entry["device"]
        setting = settings.get(device.name)
        location = (setting.location if setting and setting.location else device.area)
        buttons = f"，按鈕：{'、'.join(entry['buttons'])}" if entry["buttons"] else ""
        state_part = _ac_state(entry["climate"]) if device.kind == "ac" else ""
        lines.append(f"{device.name}（類型：{KIND_LABELS[device.kind]}，位置：{location}{buttons}{state_part}）")
    lines += [f"{e['name']}（類型：{KIND_LABELS.get(e['kind'], e['kind'])}，位置：{e['location']}）" for e in extra]
    return "、".join(lines) if lines else "目前沒有已設定的智能居家設備"


def lighting_info(session: Session, member: Member) -> str:
    areas = [a for a in session.scalars(select(LightArea).where(LightArea.household_id == member.household_id)) if a.usable]
    return "、".join(a.name for a in areas) if areas else "尚未同步 Hue 照明區域；燈光提醒未指定時使用預設區域"


def params_text(params: dict) -> str:
    if params.get("power") == "off":
        return "關機"
    if "button" in params:
        return params["button"]
    parts = ["開機"] if params.get("power") == "on" else []
    if "temperature" in params:
        parts.append(f"{params['temperature']}度")
    if "mode" in params:
        parts.append(f"模式:{params['mode']}")
    if "fan_speed" in params:
        parts.append(f"風速:{params['fan_speed']}")
    if "humidity" in params:
        parts.append(f"濕度{params['humidity']}%")
    return " ".join(parts) or "—"


def schedule_info(session: Session, zone: tzinfo) -> str:
    by_device = defaultdict(list)
    for s in schedules.visible(session):
        if s.status == "pending":
            by_device[s.target_name].append(f"{params_text(s.params)}（{s.due_at.astimezone(zone).strftime('%Y-%m-%d %H:%M')}）")
    return "；".join(f"{name}：{'、'.join(items)}" for name, items in by_device.items()) or "目前沒有排程"


def style(member: Member) -> str:
    if member.butler_style.strip():
        return f"\n使用者自訂風格：{member.butler_style.strip()}。以此風格為主。"
    return f"\n{DEFAULT_STYLE}"
