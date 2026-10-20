"""What each of the butler's actions does. Every handler gets the turn and the action's arguments (already
typed, see pipeline.py) and returns the text to show, or None (unclear). Replies keep the old system's
✅/❌ wording, because the pipeline decides on them.

Optional modules (the dehumidifiers) add their own handlers to `services.extra_actions`; without the
module those actions answer that the feature is not available.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, tzinfo

from sqlalchemy import select

from homebutler.assistant import context
from homebutler.domain import devices, food, schedules
from homebutler.domain.todos import service as todos
from homebutler.domain.todos import templates
from homebutler.store.db import Store
from homebutler.store.models import Device, Member, Schedule, utcnow

SCHEDULE_KINDS = {"control_ac": "ac", "control_ir": "ir", "control_dehumidifier": "dehumidifier"}


@dataclass
class Services:
    store: Store
    link: object
    targets: dict                  # schedule targets by kind; also used to send right away
    scheduler: object              # reconcile() after an AC command
    zone: tzinfo
    weather: object = None         # .summary(date_text, location) -> str
    extra_actions: dict = field(default_factory=dict)
    clock: callable = utcnow


@dataclass
class Turn:
    services: Services
    member_id: int
    member_name: str
    notices: list = field(default_factory=list)       # (line_user_id, text) to push once the turn is done

    @property
    def now(self) -> datetime:
        return self.services.clock()

    @property
    def local(self) -> datetime:
        return self.now.astimezone(self.services.zone)

    def member(self, session) -> Member:
        return session.get(Member, self.member_id)


def _name(value) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


def _fan_alias(value) -> str:
    name = _name(value)
    return name[:-3] + "電扇" if name.endswith("電風扇") else name


def resolve(session, kind: str, wanted: str, label: str) -> tuple[str | None, str | None]:
    """A device of this kind by its exact name; a name left out means the only one there is. IR names also
    match 電扇/電風扇 at the end, nothing looser. An explicit name never falls back to another device."""
    names = list(session.scalars(select(Device.name).where(Device.kind == kind, Device.removed_at.is_(None)).order_by(Device.name)))
    target = _name(wanted)
    if target:
        found = [n for n in names if _name(n) == target]
        if not found and kind == "ir":
            found = [n for n in names if _fan_alias(n) == _fan_alias(target)]
        if len(found) == 1:
            return found[0], None
        return None, ("❌ 設備名稱不明確，請指定唯一的完整設備名稱" if found else f"❌ 找不到「{target}」，請確認設備名稱")
    if len(names) == 1:
        return names[0], None
    if names:
        return None, f"❌ 有多台{label}（{'、'.join(names)}），請指定要控制哪一台"
    return None, f"❌ 找不到{label}設備"


# ── food ────────────────────────────────────────────────────────────────────

def add_food(turn: Turn, d: dict):
    with turn.services.store.transaction() as s:
        return food.add(s, turn.member(s), turn.local.date(), name=d.get("name"), quantity=d.get("quantity", 1),
                        unit=d.get("unit") or "個", expiry=d.get("expiry")).message


def delete_food(turn: Turn, d: dict):
    with turn.services.store.transaction() as s:
        return food.consume(s, turn.member(s).household_id, name=d.get("name")).message


def modify_food(turn: Turn, d: dict):
    changes = {"name": d.get("name_new"), "quantity": d.get("quantity"), "unit": d.get("unit"), "expiry": d.get("expiry")}
    with turn.services.store.transaction() as s:
        return food.modify(s, turn.member(s).household_id, changes, name=d.get("name")).message


def query_food(turn: Turn, d: dict):
    with turn.services.store.transaction() as s:
        return food.listing(food.active(s, turn.member(s).household_id))


# ── to-dos ──────────────────────────────────────────────────────────────────

def _done(turn: Turn, outcome) -> str:
    turn.notices += outcome.notices
    return outcome.message


def add_todo(turn: Turn, d: dict):
    with turn.services.store.transaction() as s:
        return _done(turn, todos.add(s, turn.member(s), title=d.get("item"), due_date=d.get("date"), due_time=d.get("time"),
                                     assignee=d.get("person") or None, visibility=d.get("type") or "私人",
                                     light_reminder=d.get("light_notify"), light_area_name=d.get("light_area")))


def _selector(d: dict) -> dict:
    return {"todo_id": d.get("todo_id"), "title": d.get("item"), "date_orig": d.get("date_orig"), "time_orig": d.get("time_orig")}


def modify_todo(turn: Turn, d: dict):
    names = {"item_new": "title", "date": "due_date", "time": "due_time", "person": "assignee", "type": "visibility",
             "light_notify": "light_reminder", "light_area": "light_area_name"}
    changes = {new: d[old] for old, new in names.items() if old in d}
    with turn.services.store.transaction() as s:
        return _done(turn, todos.modify(s, turn.member(s), changes, **_selector(d)))


def delete_todo(turn: Turn, d: dict):
    day_start = turn.local.replace(hour=0, minute=0, second=0, microsecond=0)
    with turn.services.store.transaction() as s:
        return _done(turn, todos.complete(s, turn.member(s), day_start, **_selector(d)))


def query_todo(turn: Turn, d: dict):
    with turn.services.store.transaction() as s:
        return todos.listing(todos.open_todos(s, turn.member(s)))


def add_recurring_todo(turn: Turn, d: dict):
    with turn.services.store.transaction() as s:
        return templates.add(s, turn.member(s), turn.local.date(), title=d.get("item"), rule=d.get("recur_type", ""),
                             due_time=d.get("time"), weekdays=d.get("weekdays"), month_day=d.get("month_day"),
                             interval_days=d.get("interval_days"), assignee=d.get("person") or None,
                             visibility=d.get("type") or "私人", start_date=d.get("start_date"), end_date=d.get("end_date"),
                             light_reminder=d.get("light_notify"), light_area_name=d.get("light_area")).message


def modify_recurring_todo(turn: Turn, d: dict):
    names = {"item_new": "title", "recur_type_new": "rule", "weekdays": "weekdays", "month_day": "month_day",
             "interval_days": "interval_days", "time": "due_time", "person": "assignee", "type": "visibility",
             "start_date": "start_date", "end_date": "end_date", "light_notify": "light_reminder", "light_area": "light_area_name"}
    changes = {new: d[old] for old, new in names.items() if old in d}
    with turn.services.store.transaction() as s:
        return templates.modify(s, turn.member(s), turn.local.date(), changes, title=d.get("item"), rule_label=d.get("recur_type")).message


def stop_recurring_todo(turn: Turn, d: dict):
    with turn.services.store.transaction() as s:
        return templates.stop(s, turn.member(s), title=d.get("item"), rule_label=d.get("recur_type")).message


def query_recurring_todo(turn: Turn, d: dict):
    with turn.services.store.transaction() as s:
        return templates.listing(templates.active(s, turn.member(s)))


# ── devices ─────────────────────────────────────────────────────────────────

def control_ac(turn: Turn, d: dict):
    with turn.services.store.transaction() as s:
        name, error = resolve(s, "ac", d.get("device_name"), "空調")
        if error:
            return error
        try:
            _, patch = turn.services.targets["ac"].check(s, name, d)
        except schedules.Invalid as problem:
            return f"❌ {problem}"
    result = turn.services.targets["ac"].run(name, patch)
    if result.get("status") != "success":
        return "❌ " + str(result.get("message") or "HA 指令失敗")
    try:
        turn.services.scheduler.reconcile()          # an AC just turned on gets its shutdown at once
    except Exception:
        pass                                         # the next minute's run does it
    return f"✅ {name} 指令已由 HA 執行"


def control_ir(turn: Turn, d: dict):
    if not d.get("button"):
        return "❌ 請指定要按哪個按鈕"
    with turn.services.store.transaction() as s:
        name, error = resolve(s, "ir", d.get("device_name"), "IR ")
        if error:
            return error
        try:
            _, params = turn.services.targets["ir"].check(s, name, {"button": d["button"]})
        except schedules.Invalid as problem:
            return f"❌ {problem}"
    result = turn.services.targets["ir"].run(name, params)
    if result.get("status") == "success":
        return f"✅ {name}「{d['button']}」已由 HA 送出"
    return "❌ " + str(result.get("message") or "HA 遙控指令失敗")


def query_sensor(turn: Turn, d: dict):
    with turn.services.store.transaction() as s:
        name, error = resolve(s, "sensor", d.get("device_name"), "感應器")
        if error:
            return error
        setting = devices.settings_by_name(s).get(name)
    state = turn.services.link.state()
    if not state["online"]:
        return f"❌ 讀取 {name} 失敗：Home Assistant 目前沒有連線"
    values = devices.adjusted(devices.sensor_values(state).get(name, {}), setting)
    if not values:
        return f"❌ 讀取 {name} 失敗：目前沒有讀數"
    return f"🌡️ {name}:溫度 {values.get('temperature', 'N/A')}°C，濕度 {values.get('humidity', 'N/A')}%"


def query_devices(turn: Turn, d: dict):
    with turn.services.store.transaction() as s:
        settings = devices.settings_by_name(s)
        found = list(s.scalars(select(Device).where(Device.removed_at.is_(None), Device.kind.in_(("ac", "ir", "sensor")))
                               .order_by(Device.kind, Device.name)))
        lines = [f"• {x.name}（{context.KIND_LABELS[x.kind]}，{(settings[x.name].location if x.name in settings else '') or x.area or '未設定'}）"
                 for x in found]
    extra = turn.services.extra_actions.get("_devices")
    lines += [f"• {e['name']}（{context.KIND_LABELS.get(e['kind'], e['kind'])}，{e['location'] or '未設定'}）" for e in (extra() if extra else [])]
    return "已設定的設備：\n" + "\n".join(lines) if lines else "目前沒有已設定的智能居家設備"


def query_weather(turn: Turn, d: dict):
    if turn.services.weather is None:
        return "❌ 天氣服務暫時無法使用"
    return turn.services.weather.summary(d.get("date") or "today", d.get("location"))


# ── schedules ───────────────────────────────────────────────────────────────

def _schedule_target(turn: Turn, s, action: str, device_name) -> tuple[str | None, str | None, str | None]:
    kind = SCHEDULE_KINDS.get(action)
    if kind is None or kind not in turn.services.targets:
        return None, None, "❌ 不支援的排程動作"
    if kind == "dehumidifier":
        return kind, _name(device_name), None
    name, error = resolve(s, kind, device_name, {"ac": "空調", "ir": "IR "}[kind])
    return kind, name, error


def add_schedule(turn: Turn, d: dict):
    with turn.services.store.transaction() as s:
        kind, name, error = _schedule_target(turn, s, d.get("target_action", ""), d.get("device_name"))
        if error:
            return error
        member = turn.member(s)
        try:
            made = schedules.add(s, turn.services.targets, kind, name, d.get("params", {}), str(d.get("trigger_time", "")),
                                 turn.services.zone, turn.now, member.name, member.id)
        except schedules.Invalid as problem:
            return f"❌ {problem}"
        return f"✅ 已新增排程：{name} {made.due_at.astimezone(turn.services.zone).strftime('%Y-%m-%d %H:%M')}"


def _pending_for(turn: Turn, s, name: str, trigger: str | None) -> list[Schedule]:
    found = [x for x in schedules.visible(s) if x.status == "pending" and x.target_name == name]
    if trigger:
        try:
            due = datetime.strptime(str(trigger).strip(), "%Y-%m-%d %H:%M").replace(tzinfo=turn.services.zone)
        except ValueError:
            return []
        found = [x for x in found if x.due_at == due]
    return found


def modify_schedule(turn: Turn, d: dict):
    name, trigger = _name(d.get("device_name")), d.get("trigger_time")
    if not name or not trigger:
        return "❌ 請指定原排程的設備名稱與觸發時間"
    with turn.services.store.transaction() as s:
        found = _pending_for(turn, s, name, trigger)
        if len(found) != 1:
            return "❌ 找不到符合條件的排程" if not found else "❌ 找不到唯一的待執行排程，請重新讀取"
        kind = name_new = None
        if d.get("target_action_new") or d.get("device_name_new"):
            kind, name_new, error = _schedule_target(turn, s, d.get("target_action_new") or
                                                     {v: k for k, v in SCHEDULE_KINDS.items()}[found[0].target_kind],
                                                     d.get("device_name_new") or name)
            if error:
                return error
        if not any(k in d for k in ("params_new", "trigger_time_new")) and kind is None:
            return f"❌ 沒收到任何要更新的欄位（{name} {trigger}）"
        try:
            schedules.modify(s, turn.services.targets, found[0].id, turn.services.zone, turn.now, kind=kind, name=name_new,
                             params=d.get("params_new"), due_local=d.get("trigger_time_new"))
        except schedules.Invalid as problem:
            return f"❌ {problem}"
    return f"✅ 已更新「{name} {trigger}」"


def delete_schedule(turn: Turn, d: dict):
    name = _name(d.get("device_name"))
    with turn.services.store.transaction() as s:
        found = _pending_for(turn, s, name, None if d.get("all") else d.get("trigger_time"))
        if not found:
            return "❌ 找不到符合條件的排程"
        if not d.get("all") and len(found) != 1:
            return "❌ 找不到唯一的排程，請重新讀取"
        for schedule in found:
            schedules.remove(s, schedule.id, turn.now)
        return f"✅ 已取消 {len(found)} 筆排程"


def query_schedule(turn: Turn, d: dict):
    with turn.services.store.transaction() as s:
        found = schedules.visible(s)
        if not found:
            return "目前沒有排程"
        lines = []
        for x in found:
            detail = f"｜{x.result}" if x.status in schedules.ATTENTION else ""
            when = x.due_at.astimezone(turn.services.zone).strftime("%Y-%m-%d %H:%M")
            lines.append(f"• {x.target_name}｜{context.params_text(x.params)}｜{when}｜{schedules.STATUS_LABELS[x.status]}{detail}")
        return "排程列表：\n" + "\n".join(lines)


# ── the member ──────────────────────────────────────────────────────────────

def set_style(turn: Turn, d: dict):
    style = str(d.get("style") or "").strip()[:200]
    with turn.services.store.transaction() as s:
        turn.member(s).butler_style = style
    return f"✅ 管家風格已更新為：{style}" if style else "✅ 管家風格已恢復預設"


def _missing_module(turn: Turn, d: dict):
    return "❌ 目前沒有除濕機功能"


HANDLERS = {
    "add_food": add_food, "delete_food": delete_food, "modify_food": modify_food, "query_food": query_food,
    "add_todo": add_todo, "modify_todo": modify_todo, "delete_todo": delete_todo, "query_todo": query_todo,
    "add_recurring_todo": add_recurring_todo, "modify_recurring_todo": modify_recurring_todo,
    "stop_recurring_todo": stop_recurring_todo, "query_recurring_todo": query_recurring_todo,
    "control_ac": control_ac, "control_ir": control_ir, "query_sensor": query_sensor, "query_devices": query_devices,
    "query_weather": query_weather,
    "add_schedule": add_schedule, "modify_schedule": modify_schedule, "delete_schedule": delete_schedule,
    "query_schedule": query_schedule, "set_style": set_style,
    "control_dehumidifier": _missing_module, "query_dehumidifier": _missing_module, "set_dehumidifier_auto": _missing_module,
    "unclear": lambda turn, d: None,
}


def handler(services: Services, action: str):
    return services.extra_actions.get(action) or HANDLERS.get(action)
