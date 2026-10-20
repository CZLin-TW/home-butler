"""To-do operations for the butler and the Dashboard.

Rules carried over from the old system:
- Who sees what: the system (no actor) sees everything; a member sees public to-dos and their own.
- A to-do is chosen by its id, or by title (optionally with the original date and time). An id that no
  longer matches never falls back to a title; several matches are listed, never guessed.
- Completing a local to-do archives it; completing a Notion one only marks it done, because that row is
  what keeps the Notion task from coming back on the next sync. Read-only Notion to-dos cannot be
  edited; writable ones can, but the next sync puts Notion's title, date and time back.
- Replies say what really happened (a "done" reply never covers an action that did nothing).

Every function works inside the caller's transaction and returns an Outcome; messages to push (for
example to the person a to-do was assigned to) are returned, not sent, so nothing goes out for a
change that is then rolled back.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from homebutler.domain.todos import lighting
from homebutler.store.models import Member, ReminderLog, Todo, utcnow

WEEKDAY = "一二三四五六日"
VISIBILITY = {"私人": "private", "公開": "public", "private": "private", "public": "public"}


@dataclass
class Outcome:
    ok: bool
    message: str
    todo: Todo | None = None
    notices: list = field(default_factory=list)      # (line_user_id, text) to push after commit


def parse_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("/", "-")
    try:
        return date.fromisoformat("-".join(part.zfill(2) for part in text.split("-")))
    except ValueError:
        raise ValueError(f"日期格式錯誤：{value}") from None


def parse_time(value) -> time | None:
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value
    try:
        hours, minutes = str(value).strip().split(":")[:2]
        return time(int(hours), int(minutes))
    except (ValueError, TypeError):
        raise ValueError(f"時間格式錯誤：{value}") from None


def show_date(day: date | None) -> str:
    return f"{day.isoformat()}（{WEEKDAY[day.weekday()]}）" if day else ""


def show_time(moment: time | None) -> str:
    return moment.strftime("%H:%M") if moment else ""


def when(todo: Todo) -> str:
    return " ".join(p for p in (todo.due_date.isoformat() if todo.due_date else "", show_time(todo.due_time)) if p)


def visible_to(actor: Member | None):
    """SQL condition for what this actor may see."""
    if actor is None:
        return True
    return or_(Todo.visibility == "public", Todo.assignee_id == actor.id)


def open_todos(session: Session, actor: Member | None = None, household_id: int | None = None) -> list[Todo]:
    query = select(Todo).where(Todo.status == "open", Todo.archived_at.is_(None), visible_to(actor))
    if household_id is not None:
        query = query.where(Todo.household_id == household_id)
    return list(session.scalars(query.order_by(Todo.due_date.is_(None), Todo.due_date, Todo.due_time.is_(None), Todo.due_time, Todo.id)))


def member_named(session: Session, name: str) -> Member | None:
    return session.scalar(select(Member).where(Member.name == name, Member.active.is_(True), Member.removed_at.is_(None)))


def _assignment_notice(todo: Todo, by: Member | None, verb: str) -> list:
    if todo.assignee is None or (by is not None and todo.assignee_id == by.id) or not todo.assignee.line_user_id:
        return []
    time_part = f" {show_time(todo.due_time)}" if todo.due_time else ""
    who = by.name if by else "管家"
    text = f"📋 {who} {verb}給你：\n{todo.title}（{todo.due_date.isoformat() if todo.due_date else ''}{time_part}）"
    return [(todo.assignee.line_user_id, text)]


def add(session: Session, actor: Member | None, *, title: str, due_date=None, due_time=None, assignee: str | None = None,
        visibility: str = "私人", light_reminder=None, light_area_ids=None, light_area_name=None,
        household_id: int | None = None) -> Outcome:
    title = str(title or "").strip()
    if not title:
        return Outcome(False, "❌ 請告訴我待辦的內容")
    try:
        day, moment = parse_date(due_date), parse_time(due_time)
    except ValueError as error:
        return Outcome(False, f"❌ {error}")
    person = member_named(session, assignee) if assignee else actor
    if assignee and person is None:
        return Outcome(False, f"❌ 找不到成員「{assignee}」")
    if visibility not in VISIBILITY:
        return Outcome(False, "❌ 類型請用私人或公開")
    home = household_id or (actor.household_id if actor else person.household_id if person else None)
    if home is None:
        return Outcome(False, "❌ 不知道這筆待辦屬於哪一戶")
    wanted = lighting.default_reminder(title, moment is not None) if light_reminder is None else bool(light_reminder)
    try:
        areas = lighting.areas_for(session, home, wanted, moment is not None, area_ids=light_area_ids, area_name=light_area_name)
    except lighting.AreaError as error:
        return Outcome(False, f"❌ {error}")
    todo = Todo(household_id=home, title=title, due_date=day, due_time=moment, assignee=person,
                visibility=VISIBILITY[visibility], light_reminder=wanted and moment is not None, light_areas=areas,
                created_by_id=actor.id if actor else None)
    session.add(todo)
    session.flush()
    time_part = f" {show_time(moment)}" if moment else ""
    kind = "🔒 私人" if todo.visibility == "private" else "📢 公開"
    light = f"，燈光提醒：{lighting.names(areas) or '未指定區域'}" if todo.light_reminder else ""
    return Outcome(True, f"✅ 已新增待辦：{title}（{day.isoformat() if day else ''}{time_part}）{kind}{light}", todo,
                   _assignment_notice(todo, actor, "指派了一項待辦"))


def candidates(session: Session, actor: Member | None, *, todo_id=None, title=None, date_orig=None, time_orig=None) -> list[Todo]:
    """Open to-dos this actor may touch that match; an explicit id matches only itself."""
    query = select(Todo).where(Todo.status == "open", Todo.archived_at.is_(None), visible_to(actor))
    if todo_id not in (None, ""):
        try:
            return list(session.scalars(query.where(Todo.id == int(todo_id))))
        except (TypeError, ValueError):
            return []
    query = query.where(Todo.title == str(title or "").strip())
    if date_orig:
        query = query.where(Todo.due_date == parse_date(date_orig))
    if time_orig:
        query = query.where(Todo.due_time == parse_time(time_orig))
    return list(session.scalars(query.order_by(Todo.due_date, Todo.due_time, Todo.id)))


def selection_message(found: list[Todo], by_id: bool) -> str:
    if len(found) < 2 or by_id:
        return "❌ 找不到可操作的待辦，請重新查詢待辦清單後指定項目。"
    lines = [f"• {t.title}（{when(t) or '未設定日期時間'}／{t.assignee.name if t.assignee else '未指定負責人'}）" for t in found[:8]]
    more = "\n另有其他符合項目，請再縮小日期範圍。" if len(found) > 8 else ""
    return ("找到多筆待辦，請指定要操作哪個日期、時間的項目：\n" + "\n".join(lines) + more
            + "\n若日期、時間也相同，請到 Dashboard 選取該筆待辦。")


def _choose(session, actor, selector) -> tuple[Todo | None, str | None]:
    try:
        found = candidates(session, actor, **selector)
    except ValueError as error:
        return None, f"❌ {error}"
    if len(found) == 1:
        return found[0], None
    if not found and not selector.get("todo_id"):
        return None, None          # the caller explains what happened instead
    return None, selection_message(found, bool(selector.get("todo_id")))


def explain_missing(session: Session, actor: Member | None, title: str, since: datetime) -> str:
    """Nothing open matched a title: say whether it is already done, or was reminded today and has
    since left the list (a Notion task closed over there), rather than a bare "not found"."""
    done = session.scalar(select(Todo).where(Todo.title == title, Todo.status == "done", visible_to(actor))
                          .order_by(Todo.completed_at.desc()))
    if done is not None:
        suffix = f"（{when(done)}）" if when(done) else ""
        return f"✅ 「{title}」已經是完成狀態了{suffix}，不用再標記一次"
    reminded = select(ReminderLog).where(ReminderLog.title == title, ReminderLog.sent_at >= since)
    if actor is not None:
        reminded = reminded.where(ReminderLog.member_id == actor.id)
    if session.scalar(reminded) is not None:
        return f"✅ 「{title}」已經不在待辦清單上了，不用再標記（外部行事曆項目在 Notion 那邊狀態改變後就會自動移除）"
    return f"❌ 找不到「{title}」"


def complete(session: Session, actor: Member | None, day_start: datetime, **selector) -> Outcome:
    """day_start: the start of today in the household's time zone (for "reminded today")."""
    todo, problem = _choose(session, actor, selector)
    if todo is None:
        return Outcome(False, problem or explain_missing(session, actor, str(selector.get("title") or ""), day_start))
    todo.status, todo.completed_at = "done", utcnow()
    if todo.source == "notion" or todo.read_only:
        return Outcome(True, f"✅ 已標記「{todo.title}」為已完成（下次同步後不再顯示）", todo)
    todo.archived_at = todo.completed_at
    return Outcome(True, f"✅ 已標記「{todo.title}」為已完成", todo)


def modify(session: Session, actor: Member | None, changes: dict, **selector) -> Outcome:
    """changes may hold: title, due_date, due_time ("" clears it), assignee, visibility, light_reminder,
    light_area_ids, light_area_name."""
    todo, problem = _choose(session, actor, selector)
    if todo is None:
        return Outcome(False, problem or f"❌ 找不到「{selector.get('title') or ''}」")
    label = todo.title
    if todo.read_only:
        return Outcome(False, f"❌ 「{label}」是外部行事曆的項目，請到原本的日曆上操作")
    known = {"title", "due_date", "due_time", "assignee", "visibility", "light_reminder", "light_area_ids", "light_area_name"}
    if not set(changes) & known:
        return Outcome(False, f"❌ 找到「{label}」但沒收到任何要更新的欄位")
    before = todo.assignee_id
    try:
        if changes.get("title"):
            todo.title = str(changes["title"]).strip()
        if changes.get("due_date"):
            todo.due_date = parse_date(changes["due_date"])
        if "due_time" in changes:
            todo.due_time = parse_time(changes["due_time"])
    except ValueError as error:
        return Outcome(False, f"❌ {error}")
    if changes.get("assignee"):
        person = member_named(session, changes["assignee"])
        if person is None:
            return Outcome(False, f"❌ 找不到成員「{changes['assignee']}」")
        todo.assignee = person
    if changes.get("visibility"):
        if changes["visibility"] not in VISIBILITY:
            return Outcome(False, "❌ 類型請用私人或公開")
        todo.visibility = VISIBILITY[changes["visibility"]]
    if any(k in changes for k in ("light_reminder", "light_area_ids", "light_area_name", "due_time")):
        wanted = bool(changes.get("light_reminder", todo.light_reminder))
        try:
            todo.light_areas = lighting.areas_for(
                session, todo.household_id, wanted, todo.due_time is not None, area_ids=changes.get("light_area_ids"),
                area_name=changes.get("light_area_name"), existing=todo.light_areas)
        except lighting.AreaError as error:
            return Outcome(False, f"❌ {error}")
        todo.light_reminder = wanted and todo.due_time is not None
    session.flush()
    notices = _assignment_notice(todo, actor, "將一項待辦指派") if todo.assignee_id != before else []
    return Outcome(True, f"✅ 已更新「{label}」", todo, notices)


def listing(todos: list[Todo]) -> str:
    if not todos:
        return "目前沒有待辦事項"
    lines = [f"• {t.title}（{show_date(t.due_date)}{(' ' + show_time(t.due_time)) if t.due_time else ''}）" for t in todos]
    return "待辦事項：\n" + "\n".join(lines)
