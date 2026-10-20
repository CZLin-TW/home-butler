"""The evening summary on LINE, once a day after DAILY_PUSH_HOUR (21 by default).

Per active member with LINE: tomorrow's weather (today's for comparison), food past or near its date,
to-dos open until tomorrow (public ones, and their own private ones), pending schedules, worded by
Claude in the member's butler style (the plain facts if Claude fails), plus this month's remaining LINE
push quota. A member with nothing to say gets nothing; one failed push never stops the others.

"Sent today" is recorded after sending, so a restart never sends twice in a day; asleep past midnight,
that day is not made up. A failure to read the marker sends nothing (pushes are a monthly quota).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homebutler.assistant import context
from homebutler.domain import conversation, food, members, schedules, system_state
from homebutler.domain.todos import service as todos
from homebutler.store.models import Member

log = logging.getLogger("homebutler.jobs.daily")
MARKER = "daily_push_date"
WEEKDAY = "一二三四五六日"


class DailySummary:
    def __init__(self, services, claude, line, hour: int = 21):
        self.services, self.claude, self.line, self.hour = services, claude, line, hour

    def run(self) -> str:
        now = self.services.clock()
        local = now.astimezone(self.services.zone)
        if local.hour < self.hour:
            return "not yet"
        today = local.date().isoformat()
        with self.services.store.transaction() as s:
            if system_state.get(s, MARKER) == today:
                return "already sent"
        sent = self.send(now)
        with self.services.store.transaction() as s:
            system_state.set_(s, MARKER, today)
        return f"sent {sent}"

    def _facts(self, s, member, local, weather_today, weather_tomorrow) -> list[str]:
        today, tomorrow = local.date(), local.date() + timedelta(days=1)
        parts = []
        if weather_tomorrow:
            parts.append(f"【重點】明日天氣預報：{weather_tomorrow}")
        if weather_today:
            parts.append(f"（參考）今日天氣：{weather_today}")
        if weather_today and weather_tomorrow:
            parts.append("請以明日天氣為主，今日僅供比較溫差變化。如果明天比今天冷很多或會下雨，主動提醒。")
        expiring = food.expiring(s, today, member.household_id)
        if expiring:
            parts.append("食品到期提醒：" + "、".join(f"{i.name}（{i.expires_on.isoformat()}）" for i in expiring))
        public, private = [], []
        for t in todos.open_todos(s, member):
            if t.due_date is None or t.due_date > tomorrow:
                continue
            label = (f"{'⚠️ 未完成 ' if t.due_date <= today else ''}{'📅 ' if t.source != 'local' else ''}{t.title}"
                     f"（{t.due_date.isoformat()}（{WEEKDAY[t.due_date.weekday()]}）{(' ' + todos.show_time(t.due_time)) if t.due_time else ''}）")
            (private if t.visibility == "private" else public).append(label)
        if public:
            parts.append("明日與未完成待辦：" + "、".join(public))
        if private:
            parts.append("您的私人待辦：" + "、".join(private))
        pending = [f"{x.target_name}｜{context.params_text(x.params)}｜{x.due_at.astimezone(self.services.zone).strftime('%Y-%m-%d %H:%M')}"
                   for x in schedules.visible(s) if x.status == "pending"]
        if pending:
            parts.append("待執行排程：" + "、".join(pending))
        return parts

    def send(self, now) -> int:
        local = now.astimezone(self.services.zone)
        weather = self.services.weather
        today_text = weather.for_notice("today") if weather else None
        tomorrow_text = weather.for_notice("tomorrow") if weather else None
        quota = self.line.quota_left() if hasattr(self.line, "quota_left") else None
        with self.services.store.transaction() as s:
            plans = [(m.id, m.line_user_id, context.style(m), self._facts(s, m, local, today_text, tomorrow_text))
                     for m in members.active(s) if m.line_user_id]
        sent = 0
        for member_id, line_user_id, style, facts in plans:
            if not facts:
                continue
            summary = "\n".join(facts)
            message = self.claude.notify(summary, local.strftime("%Y-%m-%d %H:%M"), style) or summary
            if quota:
                message += f"\n\n📊 本月推播額度：剩餘 {quota[0]}/{quota[1]} 則"
            try:
                self.line.push(line_user_id, message)
            except Exception:
                log.warning("daily summary to one member not delivered")
                continue
            with self.services.store.transaction() as s:
                conversation.save(s, s.get(Member, member_id), "assistant", message, self.services.clock())
            sent += 1
        return sent
