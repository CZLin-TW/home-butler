"""When a recurring to-do happens next. Pure functions, no database.

Calendar rules (daily, weekly, monthly, quarterly, half-yearly, yearly) follow fixed dates: the next
occurrence is the first matching date strictly after the last one generated, *not* clamped to today, so
missed past dates come one by one (finishing one reveals the next). "Every N days" counts from
completion instead: the next one is today + N. A rule's first occurrence is the first matching date on
or after max(start, today). Nothing is generated after the end date.

Monthly rules on day 29–31 fall on the month's last day in shorter months. Quarterly, half-yearly and
yearly rules are anchored on the start date (its month and day).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

RULES = {
    "daily": "每天", "weekly": "每週", "monthly": "每月", "quarterly": "每季",
    "half_yearly": "半年", "yearly": "每年", "every_n_days": "間隔天",
}
BY_LABEL = {label: rule for rule, label in RULES.items()}
EVERY_MONTHS = {"quarterly": 3, "half_yearly": 6, "yearly": 12}
WEEKDAY_ZH = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}
SCAN_DAYS = 370     # two yearly occurrences are at most 366 days apart


@dataclass(frozen=True)
class Rule:
    rule: str
    start: date
    weekdays: tuple = ()
    month_day: int | None = None
    interval_days: int | None = None
    end: date | None = None


def validate(rule: Rule) -> str | None:
    """Why this rule cannot work, in words for the family, or None."""
    if rule.rule not in RULES:
        return f"不支援的重複類型（請用：{' / '.join(RULES.values())}）"
    if rule.rule == "weekly" and not rule.weekdays:
        return "每週重複請指定星期幾（例如週一、三、五）"
    if rule.rule == "weekly" and any(d not in WEEKDAY_ZH for d in rule.weekdays):
        return "星期請用 1（週一）到 7（週日）"
    if rule.rule == "monthly" and not (rule.month_day and 1 <= rule.month_day <= 31):
        return "每月重複請指定月日（1~31）"
    if rule.rule == "every_n_days" and not (rule.interval_days and rule.interval_days >= 1):
        return "間隔天數請填 1 以上的整數"
    if rule.end and rule.end < rule.start:
        return "結束日期早於起始日期"
    return None


def _last_day(day: date) -> int:
    return calendar.monthrange(day.year, day.month)[1]


def occurs_on(rule: Rule, day: date) -> bool:
    """Whether a calendar rule falls on this day (ignores the start/end window except as an anchor)."""
    if rule.rule == "daily":
        return True
    if rule.rule == "weekly":
        return day.isoweekday() in rule.weekdays
    if rule.rule == "monthly":
        return rule.month_day is not None and day.day == min(rule.month_day, _last_day(day))
    if rule.rule in EVERY_MONTHS:
        if day < rule.start:
            return False
        months = (day.year - rule.start.year) * 12 + (day.month - rule.start.month)
        return months % EVERY_MONTHS[rule.rule] == 0 and day.day == min(rule.start.day, _last_day(day))
    return False


def _next_calendar(rule: Rule, after: date, inclusive: bool) -> date | None:
    day = after if inclusive else after + timedelta(days=1)
    for _ in range(SCAN_DAYS):
        if occurs_on(rule, day):
            return day
        day += timedelta(days=1)
    return None


def next_occurrence(rule: Rule, today: date, last_generated: date | None) -> date | None:
    """The date the rule's next to-do should have, or None (past the end, or a rule that never fires).
    Called only when the rule has no open to-do."""
    if validate(rule):
        return None
    if rule.rule == "every_n_days":
        day = max(rule.start, today) if last_generated is None else today + timedelta(days=rule.interval_days)
    elif last_generated is None:
        day = _next_calendar(rule, max(rule.start, today), inclusive=True)
    else:
        day = _next_calendar(rule, last_generated, inclusive=False)
    if day is None or day < rule.start or (rule.end and day > rule.end):
        return None
    return day


def summary(rule: Rule, due_time: str = "") -> str:
    """「每週一、三、五 20:00」「每月5號」「每3天 08:00」, shared by replies and the Dashboard."""
    time_part = f" {due_time}" if due_time else ""
    if rule.rule == "daily":
        return f"每天{time_part}"
    if rule.rule == "weekly":
        names = "、".join(WEEKDAY_ZH[d] for d in sorted(rule.weekdays) if d in WEEKDAY_ZH)
        return f"每週{names}{time_part}" if names else f"每週（未設定星期）{time_part}"
    if rule.rule == "monthly":
        return f"每月{rule.month_day}號{time_part}" if rule.month_day else f"每月{time_part}"
    if rule.rule in EVERY_MONTHS:
        label = {"quarterly": "每季", "half_yearly": "每半年", "yearly": "每年"}[rule.rule]
        return f"{label} {rule.start.month}/{rule.start.day}{time_part}"
    if rule.rule == "every_n_days":
        return f"每{rule.interval_days}天{time_part}" if rule.interval_days else f"間隔天{time_part}"
    return "週期"


def parse_weekdays(value) -> tuple:
    """[1,3,5] / "1,3,5" / "1、3、5" / 3 → (1, 3, 5); anything outside 1..7 is dropped."""
    if value is None or value == "":
        return ()
    items = list(value) if isinstance(value, (list, tuple, set)) else str(value).replace("、", ",").replace(" ", ",").split(",")
    found = set()
    for item in items:
        try:
            number = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if 1 <= number <= 7:
            found.add(number)
    return tuple(sorted(found))
