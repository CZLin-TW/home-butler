import unittest
from datetime import datetime, timedelta, timezone

from homebutler.domain.todos import service
from homebutler.domain.todos.notion import Event
from homebutler.jobs import todos as jobs
from homebutler.store.models import Member, ReminderLog, Todo
from homebutler.tests.test_todos import TodoTestBase

TPE = timezone(timedelta(hours=8))


class Messenger:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail
    def push(self, who, text):
        if self.fail:
            raise ConnectionError("LINE down")
        self.sent.append((who, text))


class Lights:
    def __init__(self):
        self.calls = []
    def breathe(self, target):
        self.calls.append(target)


class JobTests(TodoTestBase):
    def test_reminders_are_recorded_only_after_delivery(self):
        with self.store.transaction() as s:
            service.add(s, s.query(Member).filter_by(name="爸").one(), title="吃藥", due_date="2026-10-21", due_time="20:00")
        now = datetime(2026, 10, 21, 19, 50, tzinfo=TPE)
        self.assertEqual(jobs.recurring_and_reminders(self.store, Messenger(fail=True), TPE, now)["sent"], 0)
        with self.store.transaction() as s:
            self.assertEqual(s.query(ReminderLog).count(), 0)          # retried next run
        ok = Messenger()
        self.assertEqual(jobs.recurring_and_reminders(self.store, ok, TPE, now)["sent"], 1)
        self.assertEqual(jobs.recurring_and_reminders(self.store, ok, TPE, now)["sent"], 0)
        self.assertEqual(ok.sent, [("U1", "⏰ 提醒：吃藥（20:00）")])

    def test_light_reminders_once_per_area_per_minute(self):
        with self.store.transaction() as s:
            service.add(s, s.query(Member).filter_by(name="爸").one(), title="收衣服", due_date="2026-10-21", due_time="17:00")
        clock = [120.0]
        lights = Lights()
        runner = jobs.LightReminders(self.store, lights, TPE, clock=lambda: clock[0])
        now = datetime(2026, 10, 21, 18, tzinfo=TPE)
        runner.run(now); runner.run(now)
        clock[0] += 60; runner.run(now)
        self.assertEqual(lights.calls, ["hue-living", "hue-living"])

    def test_notion_failed_member_untouched_successful_member_applied(self):
        with self.store.transaction() as s:
            for name, db in (("爸", "db-ok"), ("媽", "db-down")):
                s.query(Member).filter_by(name=name).one().notion_database_id = db
            mom = s.query(Member).filter_by(name="媽").one()
            s.add(Todo(household_id=self.home, title="舊", assignee=mom, source="notion", external_id="old"))

        class Client:
            def upcoming(self, database, filters, today):
                return [Event("p1", "週會", today, None)] if database == "db-ok" else None
        totals = jobs.notion_sync(self.store, Client(), TPE, datetime(2026, 10, 21, tzinfo=TPE))
        self.assertEqual((totals["members"], totals["skipped"], totals["added"]), (1, 1, 1))
        with self.store.transaction() as s:
            self.assertIsNone(s.query(Todo).filter_by(external_id="old").one().archived_at)   # 媽's failed: kept


if __name__ == "__main__":
    unittest.main()
