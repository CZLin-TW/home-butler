"""Bring the old system's data into the new tables, once, at the switch-over (and as often as wanted on a
trial copy). Sources: the spreadsheet's tabs, and the history the old system already keeps in the
database (`public.history`, from the first step of leaving Sheets).

Carried over: members (with their butler style), remote tablets, device settings, lighting areas,
open to-dos and Notion "done" markers, recurring to-dos, food, pending and needing-attention schedules
(an AC's automatic shutdown with its current run), the butler's recent conversation, the dehumidifiers'
rules, and the sensor, AC and dehumidifier history. Not carried over: computer history (the new system
knows a computer by its key, not its address; charts refill within a day), finished or archived items,
pairing codes, anything with credentials (the dehumidifier Auth column stays in the spreadsheet).

`run(replace=False)` refuses when the new tables already hold household data; `replace=True` empties them
first (a re-run on a trial copy). The report lists, per kind, how many were read and how many written,
and every row that could not be carried over and why. Nothing is ever written to the old system.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone, tzinfo

from sqlalchemy import JSON, Column, Float, MetaData, String, Table, delete, select
from sqlalchemy.orm import Session

from homebutler import modules
from homebutler.domain import households, system_state
from homebutler.domain.todos import recurrence
from homebutler.integrations.sheets_config import sync
from homebutler.store.models import (AcReading, AutoOffCycle, ConversationMessage, Device, FoodItem, LightArea, Member,
                                     Schedule, SensorReading, Todo, TodoTemplate, utcnow)

log = logging.getLogger("homebutler.migration")

TODOS, TEMPLATES, FOOD, SCHEDULES = "待辦事項", "週期待辦模板", "食品庫存", "排程指令"
LIGHTS, CONVERSATION, DEHUMIDIFIER_RULES, STATE = "Hue 照明區域", "對話暫存", "除濕機自動規則", "系統狀態"
TABS = [*sync.TABS, TODOS, TEMPLATES, FOOD, SCHEDULES, LIGHTS, CONVERSATION, DEHUMIDIFIER_RULES, STATE]
OPTIONAL = {CONVERSATION, DEHUMIDIFIER_RULES, STATE, LIGHTS, TEMPLATES}
ACTIONS = {"control_ac": "ac", "control_ir": "ir", "control_dehumidifier": "dehumidifier"}
OPEN_SCHEDULE = {"待執行": "pending", "待確認": "unknown", "執行失敗": "failed"}
DEVICE_KINDS = {"ac", "ir", "sensor"}

old_metadata = MetaData()
old_history = Table("history", old_metadata, Column("kind", String), Column("key", String), Column("t", Float), Column("data", JSON))


class NotEmpty(Exception):
    pass


@dataclass
class Report:
    read: dict = field(default_factory=dict)
    written: dict = field(default_factory=dict)
    skipped: list = field(default_factory=list)

    def count(self, kind: str, read: int = 0, written: int = 0):
        self.read[kind] = self.read.get(kind, 0) + read
        self.written[kind] = self.written.get(kind, 0) + written

    def skip(self, kind: str, row: int, why: str):
        self.skipped.append(f"{kind} 第 {row} 列：{why}")


def _date(text) -> date | None:
    text = str(text or "").strip()
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    return date(int(match[1]), int(match[2]), int(match[3])) if match else None


def _time(text) -> time | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", str(text or "").strip())
    return time(int(match[1]), int(match[2])) if match and int(match[1]) < 24 and int(match[2]) < 60 else None


def _moment(text, zone: tzinfo) -> datetime | None:
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?", str(text or "").strip())
    if not match:
        return None
    return datetime(*(int(match[i]) for i in range(1, 6)), int(match[6] or 0), tzinfo=zone).astimezone(timezone.utc)


def _true(value) -> bool:
    return sync.truthy(value)


def _area_ids(text) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            found = json.loads(text)
        except ValueError:
            return []
        return [str(i).strip() for i in found if str(i).strip()] if isinstance(found, list) else []
    return [text]


TABLES_TO_EMPTY = [ConversationMessage, Schedule, AutoOffCycle, Todo, TodoTemplate, FoodItem, SensorReading, AcReading]


def _has_data(session: Session) -> bool:
    return any(session.scalar(select(model.id).limit(1)) is not None for model in (Todo, FoodItem, Schedule, TodoTemplate))


def _empty(session: Session) -> None:
    from homebutler.store.models import todo_light_areas, template_light_areas
    session.execute(todo_light_areas.delete())
    session.execute(template_light_areas.delete())
    for model in TABLES_TO_EMPTY:
        session.execute(delete(model))
    for name in ("dehumidifier_history", "dehumidifiers"):
        table = modules_table(name)
        if table is not None:
            session.execute(table.delete())


def modules_table(name: str):
    from homebutler.store.models import Base
    modules.load()
    return Base.metadata.tables.get(name)


def run(session: Session, tabs: dict[str, list[dict]], old_rows, zone: tzinfo, default_household: str, *,
        default_light_area: str = "", replace: bool = False, now: datetime | None = None) -> Report:
    """`tabs`: the spreadsheet's tabs as row dicts; `old_rows`: (kind, key, t, data) of the old history."""
    now = now or utcnow()
    report = Report()
    if _has_data(session):
        if not replace:
            raise NotEmpty("新系統已經有資料；要重新搬移請指定 replace")
        _empty(session)
        session.flush()
    home = households.ensure(session, default_household)

    # People and settings, exactly as the regular sync does.
    for tab in sync.TABS:
        problems = sync.APPLY[tab](session, tabs.get(tab, []), default_household)
        report.count(tab, len(tabs.get(tab, [])), len(tabs.get(tab, [])) - len(problems))
        report.skipped += [f"{tab}：{p}" for p in problems]
    session.flush()
    by_line = {m.line_user_id: m for m in session.scalars(select(Member)) if m.line_user_id}
    by_name = {m.name: m for m in session.scalars(select(Member))}
    for row in tabs.get(sync.MEMBERS, []):
        member = by_line.get(row.get("Line User ID")) or by_name.get(row.get("名稱", ""))
        if member is not None:
            member.butler_style = str(row.get("管家風格", "") or "").strip()

    # Lighting areas (HA will refresh names and add or retire areas on its first listing).
    areas = {a.ha_target: a for a in session.scalars(select(LightArea))}
    for number, row in enumerate(tabs.get(LIGHTS, []), start=2):
        target = str(row.get("Hue ID", "") or "").strip()
        if not target:
            report.skip(LIGHTS, number, "沒有 Hue ID")
            continue
        area = areas.get(target) or LightArea(ha_target=target, household_id=home.id)
        session.add(area)
        areas[target] = area
        area.resource_type = str(row.get("資源類型") or "grouped_light")
        area.source_name = str(row.get("Hue 名稱", "") or "")[:80]
        area.display_name = str(row.get("顯示名稱", "") or "")[:80]
        area.enabled = str(row.get("狀態", "") or "啟用").strip() != "停用"
        area.is_default = bool(default_light_area) and default_light_area in (area.display_name, area.source_name)
        report.count(LIGHTS, 1, 1)
    session.flush()

    # Devices named in the spreadsheet, so history can attach before HA first connects (HA's list then rules).
    devices = {(d.kind, d.name): d for d in session.scalars(select(Device))}
    for row in tabs.get(sync.DEVICES, []):
        kind = sync.KINDS.get(row.get("類型", ""))
        name = row.get("名稱", "")
        if kind in DEVICE_KINDS and name and (kind, name) not in devices:
            devices[(kind, name)] = Device(kind=kind, name=name, household_id=home.id, area=row.get("位置", ""),
                                           first_seen_at=now, last_seen_at=now)
            session.add(devices[(kind, name)])
    session.flush()

    def member(name):
        return by_name.get(str(name or "").strip())

    def light_areas(cell):
        return [areas[i] for i in _area_ids(cell) if i in areas]

    # Recurring to-dos first, so their open to-dos can point at them.
    rule_ids: dict[str, TodoTemplate] = {}
    for number, row in enumerate(tabs.get(TEMPLATES, []), start=2):
        rule = recurrence.BY_LABEL.get(str(row.get("重複類型", "")).strip())
        title = str(row.get("事項", "")).strip()
        start = _date(row.get("起始日期")) or now.astimezone(zone).date()
        if not rule or not title:
            report.skip(TEMPLATES, number, "缺事項或重複類型")
            continue
        person = member(row.get("負責人"))
        template = TodoTemplate(
            household_id=person.household_id if person else home.id, title=title, rule=rule,
            weekdays=[int(x) for x in re.split(r"[^0-9]+", str(row.get("星期", ""))) if x and 1 <= int(x) <= 7],
            month_day=int(row["月日"]) if str(row.get("月日", "")).strip().isdigit() else None,
            interval_days=int(row["間隔天數"]) if str(row.get("間隔天數", "")).strip().isdigit() else None,
            due_time=_time(row.get("時間")), assignee=person,
            visibility="public" if row.get("類型") == "公開" else "private",
            light_reminder=_true(row.get("燈光提醒")), light_areas=light_areas(row.get("燈光區域ID")), start_date=start,
            end_date=_date(row.get("結束日期")), active=str(row.get("狀態", "啟用")).strip() != "停用",
            last_generated_on=_date(row.get("最後生成日期")))
        session.add(template)
        rule_ids[str(row.get("規則ID", "")).strip()] = template
        report.count(TEMPLATES, 1, 1)
    session.flush()

    for number, row in enumerate(tabs.get(TODOS, []), start=2):
        status = str(row.get("狀態", "")).strip()
        notion = str(row.get("來源", "")).strip() == "Notion"
        if status != "待辦" and not (notion and status == "已完成"):
            continue                                   # finished local to-dos stay in the old archive
        report.count(TODOS, 1)
        title, person = str(row.get("事項", "")).strip(), member(row.get("負責人"))
        if not title:
            report.skip(TODOS, number, "沒有事項")
            continue
        external = str(row.get("外部ID", "") or "").strip() or None
        if notion and person is None:
            report.skip(TODOS, number, f"Notion 項目找不到負責人「{row.get('負責人')}」")
            continue
        template = rule_ids.get(str(row.get("規則ID", "")).strip())
        session.add(Todo(household_id=person.household_id if person else home.id, title=title, due_date=_date(row.get("日期")),
                         due_time=_time(row.get("時間")), assignee=person,
                         visibility="public" if row.get("類型") == "公開" else "private",
                         status="open" if status == "待辦" else "done", source="notion" if notion else "local",
                         read_only=str(row.get("屬性", "")).strip() == "唯讀", external_id=external if notion else None,
                         light_reminder=_true(row.get("燈光提醒")) and _time(row.get("時間")) is not None,
                         light_areas=light_areas(row.get("燈光區域ID")), template_id=template.id if template else None,
                         completed_at=now if status == "已完成" else None))
        report.count(TODOS, 0, 1)
    session.flush()

    for number, row in enumerate(tabs.get(FOOD, []), start=2):
        if str(row.get("狀態", "")).strip() != "有效":
            continue
        report.count(FOOD, 1)
        name = str(row.get("品名", "")).strip()
        try:
            quantity = max(1, int(float(row.get("數量") or 1)))
        except ValueError:
            quantity = 1
        if not name:
            report.skip(FOOD, number, "沒有品名")
            continue
        person = member(row.get("新增者"))
        session.add(FoodItem(household_id=person.household_id if person else home.id, name=name, quantity=quantity,
                             unit=str(row.get("單位") or "個"), expires_on=_date(row.get("過期日")),
                             added_on=_date(row.get("新增日")) or now.astimezone(zone).date(), added_by=person))
        report.count(FOOD, 0, 1)

    # Schedules: pending ones, those needing attention, and each AC's current automatic-shutdown run.
    cycles: dict[int, AutoOffCycle] = {}
    for number, row in enumerate(tabs.get(SCHEDULES, []), start=2):
        status_text = str(row.get("狀態", "")).strip()
        try:
            raw = json.loads(row.get("參數") or "{}")
        except ValueError:
            raw = None
        meta = raw if isinstance(raw, dict) else {}
        automatic = row.get("來源") == "自動（HA）"
        run_open = automatic and not meta.get("_auto_closed")
        if status_text not in OPEN_SCHEDULE and not run_open:
            continue
        report.count(SCHEDULES, 1)
        kind, name = ACTIONS.get(str(row.get("動作", ""))), str(row.get("設備名稱", "")).strip()
        due = _moment(row.get("觸發時間"), zone)
        if raw is None or kind is None or due is None or not name:
            report.skip(SCHEDULES, number, "動作、參數或觸發時間無法辨識")
            continue
        params = {k: v for k, v in meta.items() if not k.startswith("_auto_")}
        device = devices.get((kind, name)) if kind in DEVICE_KINDS else None
        cycle_id = None
        if run_open:
            if device is None:
                report.skip(SCHEDULES, number, f"自動關機找不到空調「{name}」")
                continue
            cycle = cycles.get(device.id)
            if cycle is None:
                cycle = AutoOffCycle(device_id=device.id, started_at=_moment(row.get("建立時間"), zone) or now,
                                     hours=int(meta.get("_auto_hours") or 0))
                session.add(cycle)
                session.flush()
                cycles[device.id] = cycle
            cycle_id = cycle.id
        status = ("cancelled" if meta.get("_auto_deleted") or status_text == "已取消" else
                  OPEN_SCHEDULE.get(status_text) or {"已執行": "done", "已過期": "expired"}.get(status_text, "cancelled"))
        session.add(Schedule(household_id=device.household_id if device else home.id, target_kind=kind, target_name=name,
                             params=params, due_at=due, source="auto_off" if automatic else "user", cycle_id=cycle_id,
                             status=status, result=str(row.get("執行結果", "") or "")[:500],
                             created_by=str(row.get("建立者", "") or "")[:80],
                             created_at=_moment(row.get("建立時間"), zone) or now))
        report.count(SCHEDULES, 0, 1)

    for number, row in enumerate(tabs.get(CONVERSATION, []), start=2):
        person = by_line.get(row.get("Line User ID"))
        role = str(row.get("角色", "")).strip()
        report.count(CONVERSATION, 1)
        if person is None or role not in ("user", "assistant") or not row.get("內容"):
            continue
        session.add(ConversationMessage(member_id=person.id, role=role, content=str(row["內容"]),
                                        created_at=_moment(row.get("時間"), zone) or now))
        report.count(CONVERSATION, 0, 1)

    rules = modules_table("dehumidifiers")
    if rules is not None:
        for row in tabs.get(DEHUMIDIFIER_RULES, []):
            name = str(row.get("device_name", "")).strip()
            if not name:
                continue
            report.count(DEHUMIDIFIER_RULES, 1)
            session.execute(rules.insert().values(
                name=name, auto_mode=_true(row.get("auto_mode")), sensor_name=str(row.get("sensor_name", "")),
                duration_min=int(float(row.get("duration_min") or 30)) if str(row.get("duration_min", "")).strip() else 30,
                threshold=int(float(row.get("threshold") or 50)),
                threshold_source="custom" if str(row.get("threshold_source", "")).strip() == "自訂" else "fixed",
                phase="disabled", sensor_missing_ticks=0, last_event="", mode="", target="", status_error="", updated_at=now))
            report.count(DEHUMIDIFIER_RULES, 0, 1)

    state = {str(r.get("鍵", "")): str(r.get("值", "")) for r in tabs.get(STATE, [])}
    if state.get("最後每日推播日期"):
        system_state.set_(session, "daily_push_date", state["最後每日推播日期"])

    # History.
    readings = modules_table("dehumidifier_history")
    for kind, key, t, data in old_rows:
        at = datetime.fromtimestamp(float(t), timezone.utc)
        data = data if isinstance(data, dict) else {}
        report.count("history:" + kind, 1)

        def number(name):
            try:
                value = float(data.get(name))
            except (TypeError, ValueError):
                return None
            return value

        if kind == "sensor" and ("sensor", key) in devices:
            session.add(SensorReading(device_id=devices[("sensor", key)].id, t=at, temperature=number("temp"),
                                      humidity=number("humidity"), co2=number("co2")))
        elif kind == "ac" and ("ac", key) in devices and data.get("power") in ("on", "off"):
            session.add(AcReading(device_id=devices[("ac", key)].id, t=at, power=data["power"], mode=str(data.get("mode", "")),
                                  temperature=number("temperature"), fan=str(data.get("fan_speed", ""))))
        elif kind == "dehumidifier" and readings is not None and data.get("power") in ("on", "off"):
            session.execute(readings.insert().values(name=key, t=at, power=data["power"], mode=""))
        else:
            continue
        report.count("history:" + kind, 0, 1)
    session.flush()
    system_state.set_(session, "migrated_from_old_at", now.isoformat())
    return report


def read_old_history(engine, since: float) -> list[tuple]:
    """The first step's history rows, from its own table (public.history)."""
    with engine.connect() as connection:
        return [tuple(r) for r in connection.execute(
            select(old_history.c.kind, old_history.c.key, old_history.c.t, old_history.c.data).where(old_history.c.t >= since)
            .order_by(old_history.c.t))]


def read_tabs(source) -> dict[str, list[dict]]:
    """Every tab the migration uses; the optional ones may be missing."""
    found = source.read([t for t in TABS if t not in OPTIONAL])
    for tab in OPTIONAL:
        try:
            found.update(source.read([tab]))
        except LookupError:
            found[tab] = []
    return found


def migrate_from(settings, store, replace: bool = False) -> Report:
    """Read the old spreadsheet and history with this service's own settings, and migrate."""
    from homebutler.integrations.sheets_config.source import GoogleSheetsSource
    from homebutler.store.db import make_engine
    from zoneinfo import ZoneInfo
    source = GoogleSheetsSource(settings.spreadsheet_id, settings.google_credentials)
    tabs = read_tabs(source)
    old_url = settings.old_database_url or settings.database_url
    old_rows = []
    if not old_url.startswith("sqlite"):
        engine = make_engine(old_url, "public")
        try:
            old_rows = read_old_history(engine, utcnow().timestamp() - 400 * 86400)
        finally:
            engine.dispose()
    with store.transaction() as session:
        report = run(session, tabs, old_rows, ZoneInfo(settings.timezone), settings.default_household,
                     default_light_area=settings.default_light_area, replace=replace)
    return report


def main(argv=None) -> int:
    """python -m homebutler.migration [--replace]: settings from the environment (as the service reads them)."""
    import argparse
    from homebutler.settings import Settings
    from homebutler.store import migrate as schema
    from homebutler.store.db import Store, make_engine
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--replace", action="store_true", help="empty the new tables first (a trial copy)")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if not settings.spreadsheet_id:
        parser.error("SPREADSHEET_ID and GOOGLE_CREDENTIALS are needed")
    store = Store(make_engine(settings.database_url, settings.database_schema))
    schema.upgrade(store.engine, settings.database_schema)
    report = migrate_from(settings, store, replace=args.replace)
    for kind in sorted(set(report.read) | set(report.written)):
        print(f"{kind}: 讀到 {report.read.get(kind, 0)}，寫入 {report.written.get(kind, 0)}")
    for line in report.skipped:
        print("略過 " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
