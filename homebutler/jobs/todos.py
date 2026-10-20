"""The to-do jobs: recurring to-dos and LINE reminders, light reminders, and Notion.

Pattern for anything sent outward: decide inside a transaction, send outside it, record "sent" only
after the send succeeded. A failed send is tried again next run; an unknown result is never replayed
within the same minute for lights.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, tzinfo

from homebutler.domain import members
from homebutler.domain.todos import notion, reminders, templates
from homebutler.store.db import Store

log = logging.getLogger("homebutler.jobs.todos")


def recurring_and_reminders(store: Store, messenger, zone: tzinfo, now=None) -> dict:
    now = now or datetime.now(zone)
    with store.transaction() as session:
        made = templates.materialize(session, now.date())
        due = reminders.due(session, now, zone)
    sent = 0
    for reminder in due:
        try:
            messenger.push(reminder.line_user_id, reminder.text)
        except Exception:
            log.warning("reminder for to-do %s not delivered; next run retries", reminder.todo_id)
            continue
        with store.transaction() as session:
            reminders.record(session, reminder)
        sent += 1
    return {"made": made, "sent": sent}


class LightReminders:
    """At most one flash per area per minute, while its to-do stays due and open."""

    def __init__(self, store: Store, lights, zone: tzinfo, clock=time.time):
        self.store, self.lights, self.zone, self.clock = store, lights, zone, clock
        self.attempted: dict[str, int] = {}

    def run(self, now=None) -> int:
        now = now or datetime.now(self.zone)
        with self.store.transaction() as session:
            targets = reminders.light_targets(session, now, self.zone)
        minute = int(self.clock() // 60)
        flashed = 0
        for target in sorted(targets):
            if self.attempted.get(target) == minute:
                continue
            self.attempted[target] = minute        # an unknown result is not replayed this minute
            try:
                self.lights.breathe(target)
                flashed += 1
            except Exception:
                log.warning("light reminder result unknown; not retried this minute")
        self.attempted = {t: m for t, m in self.attempted.items() if t in targets}
        return flashed


def notion_sync(store: Store, client, zone: tzinfo, now=None) -> dict:
    """Each member with a Notion database, fetched outside any transaction; a member whose query failed
    is left exactly as they were."""
    now = now or datetime.now(zone)
    with store.transaction() as session:
        people = [(m.id, m.notion_database_id, m.notion_filter) for m in members.active(session) if m.notion_database_id]
    totals = {"members": 0, "skipped": 0, "added": 0, "updated": 0, "archived": 0}
    for member_id, database, filters in people:
        events = client.upcoming(database, filters, now.date())
        if events is None:
            totals["skipped"] += 1
            continue
        with store.transaction() as session:
            from homebutler.store.models import Member
            changes = notion.apply(session, session.get(Member, member_id), events)
        totals["members"] += 1
        for key in ("added", "updated", "archived"):
            totals[key] += getattr(changes, key)
    return totals
