"""Reminders for to-dos with a time, and which lights are due to flash.

LINE reminders, in three stages, each sent once per person (recorded in reminder_log, written only after
the push succeeded):
  before        within 20 minutes before the time        「⏰ 提醒：…」
  late          10–60 minutes after, on the day           「⚠️ 未完成：…」
  late_<hours>  every whole hour after that, on the day   「⚠️ 已逾時約 N 小時：…」
Late reminders stop at midnight; a reminder before the time crosses midnight correctly.
Private to-dos go to the assignee, public ones to every active member of the household.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from homebutler.domain.todos.service import show_time
from homebutler.store.models import Member, ReminderLog, Todo


@dataclass(frozen=True)
class Due:
    todo_id: int
    member_id: int
    line_user_id: str
    stage: str
    title: str
    text: str


def _stage(todo: Todo, now: datetime, zone: tzinfo):
    if todo.due_date is None or todo.due_time is None:
        return None
    at = datetime.combine(todo.due_date, todo.due_time, tzinfo=zone)
    minutes = (now - at).total_seconds() / 60
    same_day = todo.due_date == now.astimezone(zone).date()
    time_text = show_time(todo.due_time)
    if -20 <= minutes <= 0:
        return "before", f"⏰ 提醒：{todo.title}（{time_text}）"
    if same_day and minutes >= 60:
        hours = int(minutes // 60)
        return f"late_{hours}", f"⚠️ 已逾時約 {hours} 小時：{todo.title}（原訂 {time_text}）"
    if same_day and minutes >= 10:
        return "late", f"⚠️ 未完成：{todo.title}（原訂 {time_text}）"
    return None


def due(session: Session, now: datetime, zone: tzinfo) -> list[Due]:
    """Reminders to send now that were not sent yet."""
    todos = session.scalars(select(Todo).where(Todo.status == "open", Todo.archived_at.is_(None),
                                               Todo.due_date.is_not(None), Todo.due_time.is_not(None)))
    sent = {(r.todo_id, r.member_id, r.stage) for r in session.scalars(select(ReminderLog))}
    members = list(session.scalars(select(Member).where(Member.active.is_(True), Member.removed_at.is_(None),
                                                        Member.line_user_id.is_not(None))))
    out = []
    for todo in todos:
        stage = _stage(todo, now, zone)
        if stage is None:
            continue
        name, text = stage
        if todo.visibility == "private":
            targets = [m for m in members if m.id == todo.assignee_id]
        else:
            targets = [m for m in members if m.household_id == todo.household_id]
        out += [Due(todo.id, m.id, m.line_user_id, name, todo.title, text) for m in targets if (todo.id, m.id, name) not in sent]
    return out


def record(session: Session, reminder: Due) -> None:
    session.add(ReminderLog(todo_id=reminder.todo_id, member_id=reminder.member_id, stage=reminder.stage, title=reminder.title))


def light_targets(session: Session, now: datetime, zone: tzinfo) -> set[str]:
    """Home Assistant targets to flash: open to-dos with a light reminder whose time has passed. Only the
    area's id goes to Home Assistant, never what the to-do says."""
    targets = set()
    for todo in session.scalars(select(Todo).where(Todo.status == "open", Todo.archived_at.is_(None), Todo.light_reminder.is_(True),
                                                   Todo.due_date.is_not(None), Todo.due_time.is_not(None))):
        if datetime.combine(todo.due_date, todo.due_time, tzinfo=zone) <= now:
            targets.update(area.ha_target for area in todo.light_areas if area.usable)
    return targets
