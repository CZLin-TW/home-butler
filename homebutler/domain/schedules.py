"""Device schedules and the automatic AC shutdown.

A schedule is one action on one device at one time. Before it is sent it is checked again (the device
still exists, the action still makes sense, an automatic shutdown is still wanted), then marked
"claimed" in the database, and only then sent; a restart or a lost answer can therefore never send it
twice. More than 2 hours late, it expires instead. Failed and unknown results stay visible until a person
dismisses them and are never sent again, nor revived by editing.

Automatic shutdown (智能居家「自動關機小時數」1–168, and「自動關機暫緩時段」such as 22:00-07:00):
- The first time an AC is seen on, a run (AutoOffCycle) starts and one shutdown is scheduled at
  start + hours, moved to the end of the hold window if it lands inside it (equal ends: always the next
  such time). Changing temperature, mode or fan does not restart it.
- Seen off: the run ends; its shutdown, if still pending and still a shutdown, is cancelled. One that a
  person changed into another action stays as an ordinary schedule.
- Hours set to 0 cancel the run's pending shutdown; setting them back does not revive it (next run).
  New hours or hold window apply from the next run.
- A person may move, change or delete the run's shutdown; a deleted one is not made again in this run,
  whatever restarts or "on" reports follow.
- HA offline, the AC unavailable or its last command unconfirmed is not "off": nothing changes.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from homebutler.domain import devices
from homebutler.store.models import AutoOffCycle, Device, DeviceEntity, Schedule, utcnow

LATE_LIMIT = timedelta(hours=2)
HOURS_MAX = 168
STATUS_LABELS = {"pending": "待執行", "claimed": "處理中", "done": "已執行", "failed": "執行失敗", "unknown": "待確認",
                 "expired": "已過期", "cancelled": "已取消"}
ATTENTION = ("failed", "unknown", "claimed")
_WINDOW = re.compile(r"\s*(\d{1,2})[:：](\d{2})\s*[-–~～]\s*(\d{1,2})[:：](\d{2})\s*")


class Invalid(ValueError):
    pass


class Target(Protocol):
    """A kind of device a schedule can act on. `check` runs inside a transaction and returns the
    household and the params as they will be sent; `run` sends, outside any transaction, and returns
    {"status": success | failed | unknown, "message": ...}."""
    kind: str

    def check(self, session: Session, name: str, params: dict) -> tuple[int, dict]: ...

    def run(self, name: str, params: dict) -> dict: ...


# ── the two spreadsheet cells ────────────────────────────────────────────────

def parse_hours(text) -> int | None:
    """Whole hours 0–168; blank is 0; anything else None (read as disabled, and reported)."""
    raw = str(text if text is not None else "").strip()
    if not raw:
        return 0
    try:
        value = float(raw)
    except ValueError:
        return None
    return int(value) if value.is_integer() and 0 <= value <= HOURS_MAX else None


def parse_window(text) -> tuple[int, int] | None:
    """(start, end) minutes of the day from "22:00-07:00"; equal ends mean all day; else None."""
    match = _WINDOW.fullmatch(text) if isinstance(text, str) else None
    if not match:
        return None
    start_h, start_m, end_h, end_m = map(int, match.groups())
    if max(start_h, end_h) > 23 or max(start_m, end_m) > 59:
        return None
    return start_h * 60 + start_m, end_h * 60 + end_m


def deferred(due: datetime, window: tuple[int, int] | None) -> datetime:
    """Local wall-clock `due`, moved to the window's end when it falls inside (start included, end not)."""
    due = due.replace(second=0, microsecond=0)
    if not window:
        return due
    start, end = window
    minute = due.hour * 60 + due.minute
    if start < end and not start <= minute < end or start > end and end <= minute < start:
        return due
    target = due.replace(hour=end // 60, minute=end % 60)
    return target if target >= due else target + timedelta(days=1)


def shutdown_at(started: datetime, hours: int, window, zone: tzinfo) -> datetime:
    return deferred(started.astimezone(zone) + timedelta(hours=hours), window).astimezone(started.tzinfo)


def settings_view(setting, now: datetime, zone: tzinfo) -> dict:
    """How the two cells are understood, so a typo shows before the AC runs."""
    hours_text = (setting.auto_off_hours_text if setting else "")[:40]
    window_text = (setting.auto_off_hold_text if setting else "")[:40]
    hours, window = parse_hours(hours_text), parse_window(window_text)
    problems = []
    if hours is None:
        problems.append("hours_unreadable")
    if window_text.strip() and not window:
        problems.append("window_unreadable")
    elif window and not hours and hours is not None:
        problems.append("window_without_hours")
    preview = shutdown_at(now, hours, window, zone) if hours else None
    return {"hours": hours or 0, "hours_text": hours_text, "window_text": window_text,
            "window": "%02d:%02d-%02d:%02d" % (*divmod(window[0], 60), *divmod(window[1], 60)) if window else None,
            "problems": problems, "preview_off_at": preview}


# ── the automatic shutdown ───────────────────────────────────────────────────

def _acs(session: Session):
    """(device, climate ha id) for every AC Home Assistant shares."""
    return list(session.execute(
        select(Device, DeviceEntity.ha_id).join(DeviceEntity, DeviceEntity.device_id == Device.id)
        .where(Device.kind == "ac", Device.removed_at.is_(None), DeviceEntity.role == "climate", DeviceEntity.removed_at.is_(None))))


def _power(state: dict, ha_id: str) -> str | None:
    """"on" / "off" only when HA is online and the AC's state is certain."""
    if not state["online"]:
        return None
    found = [c for c in state["climates"] if c["id"] == ha_id]
    view = devices.ac_view(found[0]) if len(found) == 1 else None
    return view["power"] if view and view["available"] and not view["uncertain"] else None


def _open_cycle(session: Session, device_id: int) -> AutoOffCycle | None:
    return session.scalars(select(AutoOffCycle).where(AutoOffCycle.device_id == device_id, AutoOffCycle.closed_at.is_(None))).first()


def _cycle_pending(session: Session, cycle: AutoOffCycle) -> list[Schedule]:
    return list(session.scalars(select(Schedule).where(Schedule.cycle_id == cycle.id, Schedule.status == "pending").with_for_update()))


def _cancel(schedule: Schedule, reason: str, now: datetime) -> None:
    schedule.status, schedule.result, schedule.finished_at = "cancelled", reason, now


def reconcile(session: Session, state: dict, now: datetime, zone: tzinfo) -> set[str]:
    """Look at every AC once and start, end or trim its run. Returns the names that changed."""
    settings = devices.settings_by_name(session)
    changed = set()
    for device, ha_id in _acs(session):
        power = _power(state, ha_id)
        if power is None:
            continue
        setting = settings.get(device.name)
        hours = parse_hours(setting.auto_off_hours_text if setting else "") or 0
        cycle = _open_cycle(session, device.id)
        if power == "off":
            if cycle is not None:
                cycle.closed_at = now
                for schedule in _cycle_pending(session, cycle):
                    if schedule.params.get("power") == "off":
                        _cancel(schedule, "本輪空調已關機，取消尚未執行的自動關機", now)
                changed.add(device.name)
            continue
        if cycle is not None:
            if not hours:
                for schedule in _cycle_pending(session, cycle):
                    _cancel(schedule, "已停用自動關機；本輪不再補建", now)
                    changed.add(device.name)
            continue
        if not hours:
            continue
        cycle = AutoOffCycle(device_id=device.id, started_at=now, hours=hours)
        session.add(cycle)
        session.flush()
        window = parse_window(setting.auto_off_hold_text if setting else "")
        session.add(Schedule(household_id=device.household_id, target_kind="ac", target_name=device.name, params={"power": "off"},
                             due_at=shutdown_at(now, hours, window, zone), source="auto_off", cycle_id=cycle.id,
                             created_by="系統", created_at=now, result=""))
        changed.add(device.name)
    session.flush()
    return changed


def auto_off_view(session: Session, state: dict, now: datetime, zone: tzinfo) -> list[dict]:
    """Per AC: its settings as understood and where its automatic shutdown stands."""
    settings = devices.settings_by_name(session)
    out = []
    for device, ha_id in _acs(session):
        view = settings_view(settings.get(device.name), now, zone)
        power, cycle = _power(state, ha_id), _open_cycle(session, device.id)
        latest = session.scalars(select(Schedule).where(Schedule.cycle_id == cycle.id).order_by(Schedule.id.desc())).first() if cycle else None
        status = ("disabled" if not view["hours"] else "unavailable" if power is None else "waiting_power" if power == "off"
                  else "waiting_observation" if latest is None
                  else {"pending": "counting", "done": "completed", "expired": "expired", "cancelled": "cancelled"}.get(latest.status, "needs_review"))
        out.append({"name": device.name, **view, "status": status,
                    "scheduled_at": latest.due_at if latest is not None and latest.status == "pending" else None})
    return out


# ── people's schedules ───────────────────────────────────────────────────────

def _local(text: str, zone: tzinfo) -> datetime:
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d %H:%M").replace(tzinfo=zone)
    except (TypeError, ValueError):
        raise Invalid("時間格式請用 YYYY-MM-DD HH:MM") from None


def _target(targets: dict, kind: str) -> Target:
    if kind not in targets:
        raise Invalid("不支援的排程設備類型")
    return targets[kind]


def add(session: Session, targets: dict, kind: str, name: str, params: dict, due_local: str, zone: tzinfo, now: datetime,
        created_by: str, created_by_id: int | None = None) -> Schedule:
    due = _local(due_local, zone)
    if due < now - LATE_LIMIT:
        raise Invalid("這個時間已經過了")
    household_id, clean = _target(targets, kind).check(session, name, params or {})
    schedule = Schedule(household_id=household_id, target_kind=kind, target_name=name, params=clean, due_at=due, source="user",
                        created_by=created_by[:80], created_by_id=created_by_id, created_at=now, result="")
    session.add(schedule)
    session.flush()
    return schedule


def _editable(session: Session, schedule_id: int) -> Schedule:
    schedule = session.get(Schedule, schedule_id, with_for_update=True)
    if schedule is None:
        raise LookupError(schedule_id)
    if schedule.status != "pending":
        raise Invalid("這筆排程已經不是待執行，不能修改")
    return schedule


def modify(session: Session, targets: dict, schedule_id: int, zone: tzinfo, now: datetime, *, kind: str | None = None,
           name: str | None = None, params: dict | None = None, due_local: str | None = None) -> Schedule:
    """Only a pending schedule. An automatic shutdown stays on its own AC (its time and action may change)."""
    schedule = _editable(session, schedule_id)
    kind, name = kind or schedule.target_kind, name or schedule.target_name
    if schedule.source == "auto_off" and (kind, name) != ("ac", schedule.target_name):
        raise Invalid("本輪自動關機只能修改原空調，其他設備請另建排程")
    household_id, clean = _target(targets, kind).check(session, name, schedule.params if params is None else params)
    if due_local is not None:
        due = _local(due_local, zone)
        if due < now - LATE_LIMIT:
            raise Invalid("這個時間已經過了")
        schedule.due_at = due
    schedule.target_kind, schedule.target_name, schedule.params, schedule.household_id = kind, name, clean, household_id
    return schedule


def remove(session: Session, schedule_id: int, now: datetime) -> Schedule:
    """A pending one is cancelled (an automatic shutdown then stays gone for this run); a failed or
    unknown one is dismissed, keeping its result."""
    schedule = session.get(Schedule, schedule_id, with_for_update=True)
    if schedule is None:
        raise LookupError(schedule_id)
    if schedule.status == "pending":
        _cancel(schedule, "已由使用者取消", now)
    elif schedule.status in ("failed", "unknown") and schedule.dismissed_at is None:
        schedule.dismissed_at = now
    else:
        raise Invalid("這筆排程不能移除")
    return schedule


def visible(session: Session) -> list[Schedule]:
    return list(session.scalars(select(Schedule).where(
        (Schedule.status == "pending") | (Schedule.status.in_(ATTENTION) & Schedule.dismissed_at.is_(None)))
        .order_by(Schedule.due_at, Schedule.id)))


def as_dict(schedule: Schedule, zone: tzinfo) -> dict:
    return {"id": schedule.id, "target_kind": schedule.target_kind, "target_name": schedule.target_name, "params": schedule.params,
            "due_at": schedule.due_at, "due_local": schedule.due_at.astimezone(zone).strftime("%Y-%m-%d %H:%M"),
            "source": schedule.source, "status": schedule.status, "status_label": STATUS_LABELS[schedule.status],
            "result": schedule.result, "created_by": schedule.created_by, "editable": schedule.status == "pending"}


# ── sending ─────────────────────────────────────────────────────────────────

def recover(session: Session, now: datetime) -> int:
    """At start: anything still "claimed" was being sent when the service stopped. Its result is unknown."""
    return session.execute(update(Schedule).where(Schedule.status == "claimed").values(
        status="unknown", result="服務在送出時重新啟動，結果未確認；請先確認設備狀態。系統不會自動重送。", finished_at=now)).rowcount


def due_ids(session: Session, now: datetime) -> list[int]:
    return list(session.scalars(select(Schedule.id).where(Schedule.status == "pending", Schedule.due_at <= now)
                                .order_by(Schedule.due_at, Schedule.id)))


@dataclass(frozen=True)
class Claimed:
    attempt_id: str
    kind: str
    name: str
    params: dict


def _auto_off_wanted(session: Session, schedule: Schedule, state: dict) -> bool:
    """An automatic shutdown of a run still under way goes out only while the AC is surely on and the
    hours are still set. One a person turned into another action after the run ended is ordinary."""
    cycle = schedule.cycle
    if cycle is None or cycle.closed_at is not None:
        return schedule.params.get("power") != "off"
    setting = devices.settings_by_name(session).get(schedule.target_name)
    if not (parse_hours(setting.auto_off_hours_text if setting else "") or 0):
        return False
    acs = {device.id: ha_id for device, ha_id in _acs(session)}
    return cycle.device_id in acs and _power(state, acs[cycle.device_id]) == "on"


def claim(session: Session, targets: dict, schedule_id: int, state: dict, now: datetime) -> Claimed | None:
    """Check once more and mark it claimed; None when it must not be sent now (it may have expired,
    been refused, or be waiting for the AC to be surely on)."""
    schedule = session.get(Schedule, schedule_id, with_for_update=True)
    if schedule is None or schedule.status != "pending" or schedule.due_at > now:
        return None
    if now - schedule.due_at > LATE_LIMIT:
        schedule.status, schedule.result, schedule.finished_at = "expired", "超過兩小時未能執行", now
        return None
    try:
        _, params = _target(targets, schedule.target_kind).check(session, schedule.target_name, schedule.params)
    except Invalid as error:
        schedule.status, schedule.result, schedule.finished_at = "failed", f"未送出：{error}", now
        return None
    if schedule.source == "auto_off" and not _auto_off_wanted(session, schedule, state):
        return None                      # offline or unsure is not permission to send; expires if it lasts
    attempt = uuid.uuid4().hex
    taken = session.execute(update(Schedule).where(Schedule.id == schedule.id, Schedule.status == "pending")
                            .values(status="claimed", attempt_id=attempt,
                                    result="指令處理中；若結果未更新，請先確認設備狀態。系統不會自動重送。")).rowcount
    if taken != 1:
        return None
    return Claimed(attempt, schedule.target_kind, schedule.target_name, params)


def finish(session: Session, attempt_id: str, outcome: dict, now: datetime) -> None:
    status = {"success": "done", "failed": "failed"}.get(outcome.get("status"), "unknown")
    session.execute(update(Schedule).where(Schedule.attempt_id == attempt_id, Schedule.status == "claimed")
                    .values(status=status, result=str(outcome.get("message") or "")[:500], finished_at=now))
