"""Recurring to-dos: the template is the rule; each active template always has exactly one open to-do,
"the next one". Completing it lets materialize() create the following one.

The database is the only truth for "does this template have an open to-do" and "when was the last one
for", so restarts and overlapping runs never create duplicates.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from homebutler.domain.todos import lighting, recurrence
from homebutler.domain.todos.service import VISIBILITY, Outcome, member_named, parse_date, parse_time, show_time
from homebutler.store.models import Member, Todo, TodoTemplate

log = logging.getLogger("homebutler.todos")


def as_rule(template: TodoTemplate) -> recurrence.Rule:
    return recurrence.Rule(template.rule, template.start_date, tuple(template.weekdays or ()), template.month_day,
                           template.interval_days, template.end_date)


def summary(template: TodoTemplate) -> str:
    return recurrence.summary(as_rule(template), show_time(template.due_time))


def _visible(session: Session, actor: Member | None):
    query = select(TodoTemplate).where(TodoTemplate.active.is_(True))
    if actor is not None:
        query = query.where((TodoTemplate.visibility == "public") | (TodoTemplate.assignee_id == actor.id))
    return query


def active(session: Session, actor: Member | None = None) -> list[TodoTemplate]:
    return list(session.scalars(_visible(session, actor).order_by(TodoTemplate.id)))


def _choose(session, actor, template_id=None, title=None, rule_label=None):
    query = _visible(session, actor)
    if template_id not in (None, ""):
        try:
            return list(session.scalars(query.where(TodoTemplate.id == int(template_id))))
        except (TypeError, ValueError):
            return []
    query = query.where(TodoTemplate.title == str(title or "").strip())
    if rule_label:
        query = query.where(TodoTemplate.rule == recurrence.BY_LABEL.get(rule_label, rule_label))
    return list(session.scalars(query))


def add(session: Session, actor: Member | None, today: date, *, title: str, rule: str, due_time=None, weekdays=None,
        month_day=None, interval_days=None, assignee: str | None = None, visibility: str = "私人", start_date=None,
        end_date=None, light_reminder=None, light_area_ids=None, light_area_name=None,
        household_id: int | None = None) -> Outcome:
    title = str(title or "").strip()
    if not title:
        return Outcome(False, "❌ 請告訴我要週期提醒的事項")
    kind = recurrence.BY_LABEL.get(rule, rule)
    try:
        moment, start, end = parse_time(due_time), parse_date(start_date) or today, parse_date(end_date)
        day_of_month = int(month_day) if month_day not in (None, "") else None
        every = int(interval_days) if interval_days not in (None, "") else None
    except ValueError as error:
        return Outcome(False, f"❌ {error}")
    candidate = recurrence.Rule(kind, start, recurrence.parse_weekdays(weekdays), day_of_month, every, end)
    problem = recurrence.validate(candidate)
    if problem:
        return Outcome(False, f"❌ {problem}")
    person = member_named(session, assignee) if assignee else actor
    if assignee and person is None:
        return Outcome(False, f"❌ 找不到成員「{assignee}」")
    if visibility not in VISIBILITY:
        return Outcome(False, "❌ 類型請用私人或公開")
    home = household_id or (actor.household_id if actor else person.household_id if person else None)
    if home is None:
        return Outcome(False, "❌ 不知道這個週期提醒屬於哪一戶")
    wanted = lighting.default_reminder(title, moment is not None) if light_reminder is None else bool(light_reminder)
    try:
        areas = lighting.areas_for(session, home, wanted, moment is not None, area_ids=light_area_ids, area_name=light_area_name)
    except lighting.AreaError as error:
        return Outcome(False, f"❌ {error}")
    template = TodoTemplate(household_id=home, title=title, rule=kind, weekdays=list(candidate.weekdays),
                            month_day=day_of_month if kind == "monthly" else None,
                            interval_days=every if kind == "every_n_days" else None, due_time=moment, assignee=person,
                            visibility=VISIBILITY[visibility], light_reminder=wanted and moment is not None, light_areas=areas,
                            start_date=start, end_date=end, created_by_id=actor.id if actor else None)
    session.add(template)
    session.flush()
    materialize(session, today)
    light = f"，燈光提醒：{lighting.names(areas) or '未指定區域'}" if template.light_reminder else ""
    return Outcome(True, f"✅ 已設定週期提醒：{title}（{summary(template)}）🔁{light}")


def stop(session: Session, actor: Member | None, *, template_id=None, title=None, rule_label=None) -> Outcome:
    """Stops the rule (kept, can be revived). The open to-do it made is left alone."""
    if not title and not template_id:
        return Outcome(False, "❌ 請告訴我要停止哪個週期提醒")
    found = _choose(session, actor, template_id, title, rule_label)
    label = title or "週期提醒"
    if not found:
        return Outcome(False, f"❌ 找不到啟用中的週期提醒「{label}」")
    if len(found) > 1:
        options = "、".join(summary(t) for t in found)
        return Outcome(False, f"❌ 有多個「{title}」週期提醒（{options}），請說明是哪一個（例如「停掉每週的{title}」）")
    found[0].active = False
    return Outcome(True, f"✅ 已停止週期提醒：{found[0].title}（{summary(found[0])}）")


def modify(session: Session, actor: Member | None, today: date, changes: dict, *, template_id=None, title=None, rule_label=None) -> Outcome:
    if not title and not template_id:
        return Outcome(False, "❌ 請告訴我要修改哪個週期提醒")
    found = _choose(session, actor, template_id, title, rule_label)
    if not found:
        return Outcome(False, f"❌ 找不到啟用中的週期提醒「{title or '週期提醒'}」")
    if len(found) > 1:
        return Outcome(False, f"❌ 有多個「{title}」週期提醒（{'、'.join(summary(t) for t in found)}），請指定要改哪一個")
    template = found[0]
    if not changes:
        return Outcome(False, f"❌ 沒收到任何要更新的欄位（{title or template.title}）")
    try:
        kind = recurrence.BY_LABEL.get(changes["rule"], changes["rule"]) if changes.get("rule") else template.rule
        weekdays = recurrence.parse_weekdays(changes["weekdays"]) if "weekdays" in changes else tuple(template.weekdays or ())
        month_day = int(changes["month_day"]) if changes.get("month_day") not in (None, "") else template.month_day
        interval = int(changes["interval_days"]) if changes.get("interval_days") not in (None, "") else template.interval_days
        start = parse_date(changes["start_date"]) if changes.get("start_date") else template.start_date
        end = parse_date(changes["end_date"]) if "end_date" in changes else template.end_date
        moment = parse_time(changes["due_time"]) if "due_time" in changes else template.due_time
    except ValueError as error:
        return Outcome(False, f"❌ {error}")
    candidate = recurrence.Rule(kind, start, weekdays, month_day, interval, end)
    problem = recurrence.validate(candidate)
    if problem:
        return Outcome(False, f"❌ {problem}")
    if changes.get("assignee"):
        person = member_named(session, changes["assignee"])
        if person is None:
            return Outcome(False, f"❌ 找不到成員「{changes['assignee']}」")
        template.assignee = person
    if changes.get("visibility"):
        if changes["visibility"] not in VISIBILITY:
            return Outcome(False, "❌ 類型請用私人或公開")
        template.visibility = VISIBILITY[changes["visibility"]]
    template.title = str(changes.get("title") or template.title).strip()
    template.rule, template.weekdays, template.start_date, template.end_date, template.due_time = kind, list(weekdays), start, end, moment
    template.month_day = month_day if kind == "monthly" else None
    template.interval_days = interval if kind == "every_n_days" else None
    if any(k in changes for k in ("light_reminder", "light_area_ids", "light_area_name", "due_time")):
        wanted = bool(changes.get("light_reminder", template.light_reminder))
        try:
            template.light_areas = lighting.areas_for(session, template.household_id, wanted, moment is not None,
                                                      area_ids=changes.get("light_area_ids"), area_name=changes.get("light_area_name"),
                                                      existing=template.light_areas)
        except lighting.AreaError as error:
            return Outcome(False, f"❌ {error}")
        template.light_reminder = wanted and moment is not None
    return Outcome(True, f"✅ 已更新週期提醒：{template.title}（{summary(template)}）🔁")


def listing(templates: list[TodoTemplate]) -> str:
    if not templates:
        return "目前沒有設定週期提醒"
    return "週期提醒：\n" + "\n".join(
        f"🔁 {t.title}｜{summary(t)}{f'（{t.assignee.name}）' if t.assignee else ''}" for t in templates)


def materialize(session: Session, today: date) -> int:
    """Give every active template without an open to-do its next one. Returns how many were made.
    A broken template is logged and skipped; it never stops the others."""
    with_open = set(session.scalars(select(Todo.template_id).where(Todo.template_id.is_not(None), Todo.status == "open",
                                                                    Todo.archived_at.is_(None))))
    made = 0
    for template in session.scalars(select(TodoTemplate).where(TodoTemplate.active.is_(True))):
        if template.id in with_open:
            continue
        try:
            day = recurrence.next_occurrence(as_rule(template), today, template.last_generated_on)
        except Exception:
            log.exception("template %s could not compute its next date", template.id)
            continue
        if day is None:
            continue
        session.add(Todo(household_id=template.household_id, title=template.title, due_date=day, due_time=template.due_time,
                         assignee_id=template.assignee_id, visibility=template.visibility,
                         light_reminder=template.light_reminder, light_areas=list(template.light_areas),
                         template_id=template.id, created_by_id=template.created_by_id))
        template.last_generated_on = day
        with_open.add(template.id)
        made += 1
    session.flush()
    return made
