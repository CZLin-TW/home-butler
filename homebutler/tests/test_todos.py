"""To-dos: recurrence, the operations, recurring templates, reminders, lights and Notion."""
import unittest
from datetime import date, datetime, time, timedelta, timezone

from homebutler.domain import households
from homebutler.domain.todos import notion, recurrence, reminders, service, templates
from homebutler.domain.todos.notion import Event
from homebutler.domain.todos.recurrence import Rule
from homebutler.store.models import LightArea, Member, ReminderLog, Todo
from homebutler.tests.helpers import StoreTest

TPE = timezone(timedelta(hours=8))
D = date.fromisoformat


class RecurrenceTests(unittest.TestCase):
    def nxt(self, rule, today, last=None):
        return recurrence.next_occurrence(rule, D(today), D(last) if last else None)

    def test_weekly_first_and_next_and_missed_ones_come_one_by_one(self):
        rule = Rule("weekly", D("2026-10-01"), (1, 3))          # Mondays and Wednesdays
        self.assertEqual(self.nxt(rule, "2026-10-21"), D("2026-10-21"))            # a Wednesday: today
        self.assertEqual(self.nxt(rule, "2026-10-21", "2026-10-21"), D("2026-10-26"))
        self.assertEqual(self.nxt(rule, "2026-10-30", "2026-10-21"), D("2026-10-26"))   # not clamped to today

    def test_monthly_falls_on_the_last_day_of_short_months(self):
        rule = Rule("monthly", D("2026-01-01"), month_day=31)
        self.assertEqual(self.nxt(rule, "2026-02-10", "2026-01-31"), D("2026-02-28"))
        self.assertEqual(self.nxt(rule, "2026-03-01", "2026-02-28"), D("2026-03-31"))

    def test_quarterly_half_yearly_yearly_are_anchored_on_the_start(self):
        self.assertEqual(self.nxt(Rule("quarterly", D("2026-01-31")), "2026-02-01", "2026-01-31"), D("2026-04-30"))
        self.assertEqual(self.nxt(Rule("half_yearly", D("2026-03-15")), "2026-03-16", "2026-03-15"), D("2026-09-15"))
        self.assertEqual(self.nxt(Rule("yearly", D("2024-02-29")), "2025-01-01", "2024-02-29"), D("2025-02-28"))

    def test_every_n_days_counts_from_completion(self):
        rule = Rule("every_n_days", D("2026-10-01"), interval_days=3)
        self.assertEqual(self.nxt(rule, "2026-10-21"), D("2026-10-21"))
        self.assertEqual(self.nxt(rule, "2026-10-25", "2026-10-21"), D("2026-10-28"))   # finished late, next is later

    def test_nothing_after_the_end_and_bad_rules_say_why(self):
        self.assertIsNone(self.nxt(Rule("daily", D("2026-10-01"), end=D("2026-10-20")), "2026-10-21"))
        self.assertIsNotNone(recurrence.validate(Rule("weekly", D("2026-10-01"))))
        self.assertIsNotNone(recurrence.validate(Rule("monthly", D("2026-10-01"), month_day=32)))
        self.assertIsNotNone(recurrence.validate(Rule("every_n_days", D("2026-10-01"), interval_days=0)))
        self.assertIsNotNone(recurrence.validate(Rule("hourly", D("2026-10-01"))))
        self.assertIsNone(self.nxt(Rule("weekly", D("2026-10-01")), "2026-10-21"))

    def test_summaries_and_weekday_parsing(self):
        self.assertEqual(recurrence.summary(Rule("weekly", D("2026-10-01"), (5, 1, 3)), "20:00"), "每週一、三、五 20:00")
        self.assertEqual(recurrence.summary(Rule("every_n_days", D("2026-10-01"), interval_days=3)), "每3天")
        self.assertEqual(recurrence.summary(Rule("yearly", D("2026-03-15"))), "每年 3/15")
        self.assertEqual(recurrence.parse_weekdays("1、3, 5 9 x"), (1, 3, 5))


class TodoTestBase(StoreTest):
    def setUp(self):
        super().setUp()
        with self.store.transaction() as s:
            self.home = households.ensure(s, "A").id
            other = households.ensure(s, "B").id
            for name, line in (("爸", "U1"), ("媽", "U2"), ("妹", None)):
                s.add(Member(household_id=self.home, name=name, line_user_id=line, active=True))
            s.add(Member(household_id=other, name="外", line_user_id="U9", active=True))
            s.add(LightArea(household_id=self.home, ha_target="hue-living", source_name="Living", display_name="大燈", is_default=True))
            s.add(LightArea(household_id=self.home, ha_target="hue-off", display_name="停用區", enabled=False))

    def m(self, s, name):
        return s.query(Member).filter_by(name=name).one()


class ServiceTests(TodoTestBase):
    def test_add_defaults_to_the_actor_and_notifies_someone_else_it_was_assigned_to(self):
        with self.store.transaction() as s:
            out = service.add(s, self.m(s, "爸"), title="倒垃圾", due_date="2026-10-21", due_time="20:00")
            self.assertTrue(out.ok)
            self.assertEqual(out.message, "✅ 已新增待辦：倒垃圾（2026-10-21 20:00）🔒 私人，燈光提醒：大燈")   # a chore with a time
            self.assertEqual((out.todo.assignee.name, out.todo.light_reminder, out.notices), ("爸", True, []))
            out = service.add(s, self.m(s, "爸"), title="繳費", due_date="2026-10-22", assignee="媽", visibility="公開")
            self.assertEqual(out.notices, [("U2", "📋 爸 指派了一項待辦給你：\n繳費（2026-10-22）")])
            self.assertFalse(out.todo.light_reminder)
            self.assertFalse(service.add(s, self.m(s, "爸"), title="x", assignee="不存在").ok)
            self.assertFalse(service.add(s, self.m(s, "爸"), title="x", due_date="2026-13-40").ok)
            self.assertFalse(service.add(s, self.m(s, "爸"), title="").ok)

    def test_members_see_public_and_their_own_and_the_system_sees_all(self):
        with self.store.transaction() as s:
            service.add(s, self.m(s, "爸"), title="私事", due_date="2026-10-21")
            service.add(s, self.m(s, "媽"), title="大家的", due_date="2026-10-21", visibility="公開")
            self.assertEqual([t.title for t in service.open_todos(s, self.m(s, "媽"))], ["大家的"])
            self.assertEqual(len(service.open_todos(s, None)), 2)
            out = service.complete(s, self.m(s, "媽"), datetime(2026, 10, 21, tzinfo=TPE), title="私事")
            self.assertFalse(out.ok)                                            # someone else's private one
            self.assertIn("找不到", out.message)

    def test_choosing_by_id_title_and_original_date_time_never_guesses(self):
        start = datetime(2026, 10, 21, tzinfo=TPE)
        with self.store.transaction() as s:
            me = self.m(s, "爸")
            a = service.add(s, me, title="打電話", due_date="2026-10-21", due_time="09:00").todo
            b = service.add(s, me, title="打電話", due_date="2026-10-21", due_time="15:00").todo
            out = service.complete(s, me, start, title="打電話")
            self.assertFalse(out.ok); self.assertIn("找到多筆待辦", out.message); self.assertIn("09:00", out.message)
            self.assertEqual(s.get(Todo, a.id).status, "open")                    # nothing written
            self.assertTrue(service.complete(s, me, start, title="打電話", time_orig="15:00").ok)
            self.assertEqual(s.get(Todo, b.id).status, "done")
            self.assertFalse(service.complete(s, me, start, todo_id=b.id).ok)      # done: an id never falls back
            self.assertFalse(service.complete(s, me, start, todo_id=999).ok)
            self.assertTrue(service.complete(s, me, start, todo_id=a.id).ok)

    def test_local_done_is_archived_notion_done_is_kept_as_the_marker(self):
        start = datetime(2026, 10, 21, tzinfo=TPE)
        with self.store.transaction() as s:
            me = self.m(s, "爸")
            local = service.add(s, me, title="本地", due_date="2026-10-21").todo
            remote = Todo(household_id=self.home, title="會議", due_date=D("2026-10-21"), assignee=me, source="notion",
                          read_only=True, external_id="page-1")
            s.add(remote); s.flush()
            self.assertEqual(service.complete(s, me, start, title="本地").message, "✅ 已標記「本地」為已完成")
            self.assertIn("下次同步後不再顯示", service.complete(s, me, start, title="會議").message)
            self.assertIsNotNone(s.get(Todo, local.id).archived_at)
            self.assertEqual((s.get(Todo, remote.id).status, s.get(Todo, remote.id).archived_at), ("done", None))
            self.assertIn("已經是完成狀態了", service.complete(s, me, start, title="本地").message)

    def test_a_task_reminded_today_and_gone_reads_as_done_not_missing(self):
        with self.store.transaction() as s:
            me = self.m(s, "爸")
            gone = Todo(household_id=self.home, title="簽核", assignee=me, source="notion", external_id="p",
                        archived_at=datetime(2026, 10, 21, 3, tzinfo=timezone.utc)); s.add(gone); s.flush()
            s.add(ReminderLog(todo_id=gone.id, member_id=me.id, stage="late", title="簽核",
                              sent_at=datetime(2026, 10, 21, 2, tzinfo=timezone.utc)))
            out = service.complete(s, me, datetime(2026, 10, 21, tzinfo=TPE), title="簽核")
            self.assertIn("已經不在待辦清單上了", out.message)
            self.assertIn("❌ 找不到", service.complete(s, me, datetime(2026, 10, 22, tzinfo=TPE), title="簽核").message)

    def test_modify_refuses_read_only_and_tells_a_new_assignee(self):
        with self.store.transaction() as s:
            me = self.m(s, "爸")
            todo = service.add(s, me, title="修水管", due_date="2026-10-21").todo
            out = service.modify(s, me, {"assignee": "媽", "due_time": "10:00", "title": "修水龍頭"}, title="修水管")
            self.assertEqual(out.message, "✅ 已更新「修水管」")
            self.assertEqual(out.notices, [("U2", "📋 爸 將一項待辦指派給你：\n修水龍頭（2026-10-21 10:00）")])
            self.assertEqual((todo.title, todo.due_time), ("修水龍頭", time(10, 0)))
            s.add(Todo(household_id=self.home, title="外部", assignee=me, source="notion", read_only=True, external_id="x"))
            s.flush()
            self.assertIn("外部行事曆", service.modify(s, me, {"title": "y"}, title="外部").message)
            self.assertIn("找不到可操作", service.modify(s, me, {"title": "z"}, todo_id=todo.id).message)   # now 媽's private one
            self.assertIn("沒收到任何要更新的欄位", service.modify(s, None, {}, todo_id=todo.id).message)

    def test_light_areas_must_be_usable_and_unrelated_edits_keep_them(self):
        with self.store.transaction() as s:
            me = self.m(s, "爸")
            off = s.query(LightArea).filter_by(ha_target="hue-off").one()
            self.assertIn("已不可用", service.add(s, me, title="洗碗", due_date="2026-10-21", due_time="21:00",
                                                 light_area_ids=[off.id]).message)
            self.assertIn("至少選擇一個", service.add(s, me, title="洗碗", due_date="2026-10-21", due_time="21:00",
                                                    light_area_ids=[]).message)
            todo = service.add(s, me, title="洗碗", due_date="2026-10-21", due_time="21:00").todo
            service.modify(s, me, {"title": "洗碗盤"}, todo_id=todo.id)
            self.assertEqual([a.ha_target for a in todo.light_areas], ["hue-living"])
            service.modify(s, me, {"light_reminder": False}, todo_id=todo.id)
            self.assertEqual((todo.light_reminder, todo.light_areas), (False, []))

    def test_listing_text(self):
        with self.store.transaction() as s:
            service.add(s, self.m(s, "爸"), title="繳費", due_date="2026-10-21", due_time="09:30")
            self.assertEqual(service.listing(service.open_todos(s)), "待辦事項：\n• 繳費（2026-10-21（三） 09:30）")
            self.assertEqual(service.listing([]), "目前沒有待辦事項")


class TemplateTests(TodoTestBase):
    def test_one_open_to_do_per_rule_and_the_next_appears_after_completion(self):
        today = D("2026-10-21")
        with self.store.transaction() as s:
            me = self.m(s, "爸")
            out = templates.add(s, me, today, title="倒垃圾", rule="每週", weekdays="1,3", due_time="20:00")
            self.assertEqual(out.message, "✅ 已設定週期提醒：倒垃圾（每週一、三 20:00）🔁，燈光提醒：大燈")
            self.assertEqual(templates.materialize(s, today), 0)                 # already one open
            first = s.query(Todo).filter_by(title="倒垃圾").one()
            self.assertEqual((first.due_date, first.light_reminder, len(first.light_areas)), (today, True, 1))
            service.complete(s, me, datetime(2026, 10, 21, tzinfo=TPE), todo_id=first.id)
            self.assertEqual(templates.materialize(s, today), 1)
            nxt = s.query(Todo).filter_by(title="倒垃圾", status="open").one()
            self.assertEqual(nxt.due_date, D("2026-10-26"))

    def test_stop_modify_and_visibility(self):
        today = D("2026-10-21")
        with self.store.transaction() as s:
            me, mom = self.m(s, "爸"), self.m(s, "媽")
            templates.add(s, me, today, title="吃藥", rule="每天", due_time="08:00")
            self.assertIn("找不到", templates.stop(s, mom, title="吃藥").message)      # private to 爸
            self.assertEqual(templates.listing(templates.active(s, mom)), "目前沒有設定週期提醒")
            out = templates.modify(s, me, today, {"rule": "間隔天", "interval_days": "2"}, title="吃藥")
            self.assertEqual(out.message, "✅ 已更新週期提醒：吃藥（每2天 08:00）🔁")
            self.assertIn("請指定月日", templates.modify(s, me, today, {"rule": "每月"}, title="吃藥").message)
            self.assertEqual(templates.stop(s, me, title="吃藥").message, "✅ 已停止週期提醒：吃藥（每2天 08:00）")
            self.assertEqual(templates.active(s), [])

    def test_bad_rule_rejected(self):
        with self.store.transaction() as s:
            out = templates.add(s, self.m(s, "爸"), D("2026-10-21"), title="x", rule="每週")
            self.assertEqual(out.message, "❌ 每週重複請指定星期幾（例如週一、三、五）")


class ReminderTests(TodoTestBase):
    def at(self, text):
        return datetime.fromisoformat(text).replace(tzinfo=TPE)

    def test_three_stages_once_each_private_to_assignee_public_to_the_household(self):
        with self.store.transaction() as s:
            service.add(s, self.m(s, "爸"), title="吃藥", due_date="2026-10-21", due_time="20:00")
            service.add(s, self.m(s, "媽"), title="開會", due_date="2026-10-21", due_time="20:00", visibility="公開")
        def run(now):
            with self.store.transaction() as s:
                found = reminders.due(s, self.at(now), TPE)
                for r in found:
                    reminders.record(s, r)
            return sorted((r.line_user_id, r.text) for r in found)
        self.assertEqual(run("2026-10-21T19:30"), [])
        self.assertEqual(run("2026-10-21T19:45"), [("U1", "⏰ 提醒：吃藥（20:00）"), ("U1", "⏰ 提醒：開會（20:00）"),
                                                    ("U2", "⏰ 提醒：開會（20:00）")])          # 妹 has no LINE; 外 is household B
        self.assertEqual(run("2026-10-21T19:50"), [])                                         # already sent
        self.assertEqual([t for _, t in run("2026-10-21T20:15")][:1], ["⚠️ 未完成：吃藥（原訂 20:00）"])
        self.assertIn(("U1", "⚠️ 已逾時約 1 小時：吃藥（原訂 20:00）"), run("2026-10-21T21:05"))
        self.assertEqual(run("2026-10-21T21:30"), [])
        self.assertIn(("U1", "⚠️ 已逾時約 2 小時：吃藥（原訂 20:00）"), run("2026-10-21T22:00"))
        self.assertEqual(run("2026-10-22T00:30"), [])                                         # stops at midnight

    def test_reminder_before_midnight_crossing(self):
        with self.store.transaction() as s:
            service.add(s, self.m(s, "爸"), title="打卡", due_date="2026-10-22", due_time="00:05")
            self.assertEqual([r.stage for r in reminders.due(s, self.at("2026-10-21T23:50"), TPE)], ["before"])

    def test_light_targets_only_for_open_due_reminders_on_usable_areas(self):
        with self.store.transaction() as s:
            me = self.m(s, "爸")
            service.add(s, me, title="收衣服", due_date="2026-10-21", due_time="17:00")
            service.add(s, me, title="倒垃圾", due_date="2026-10-21", due_time="23:00")
            done = service.add(s, me, title="澆花", due_date="2026-10-21", due_time="08:00").todo
            service.complete(s, me, self.at("2026-10-21T00:00"), todo_id=done.id)
            self.assertEqual(reminders.light_targets(s, self.at("2026-10-21T18:00"), TPE), {"hue-living"})
            s.query(LightArea).filter_by(ha_target="hue-living").one().enabled = False
            self.assertEqual(reminders.light_targets(s, self.at("2026-10-21T18:00"), TPE), set())


class NotionTests(TodoTestBase):
    def events(self, *items):
        return [Event(p, t, D(d), time.fromisoformat(h) if h else None) for p, t, d, h in items]

    def test_renames_follow_notion_status_stays_ours_and_removals_archive(self):
        with self.store.transaction() as s:
            me = self.m(s, "爸")
            self.assertEqual(notion.apply(s, me, self.events(("p1", "週會", "2026-10-22", "10:00"), ("p2", "報告", "2026-10-23", None))).added, 2)
            service.complete(s, me, datetime(2026, 10, 21, tzinfo=TPE), title="週會")
            changes = notion.apply(s, me, self.events(("p1", "週會（改）", "2026-10-22", "11:00"), ("p2", "報告", "2026-10-23", None)))
            self.assertEqual((changes.added, changes.updated, changes.archived), (0, 1, 0))
            p1 = s.query(Todo).filter_by(external_id="p1").one()
            self.assertEqual((p1.title, p1.status, p1.due_time), ("週會（改）", "done", time(11, 0)))   # stays done after a rename
            self.assertEqual(notion.apply(s, me, self.events(("p1", "週會（改）", "2026-10-22", "11:00"))).archived, 1)
            self.assertEqual(notion.apply(s, me, []).archived, 1)           # p1 left too: its marker goes
            self.assertEqual(notion.apply(s, me, self.events(("p1", "週會", "2026-10-29", None))).added, 1)
            back = s.query(Todo).filter_by(external_id="p1").one()
            self.assertEqual((back.status, back.archived_at), ("open", None))       # came back: open again

    def test_unchanged_writes_nothing_and_local_settings_survive(self):
        with self.store.transaction() as s:
            me = self.m(s, "爸")
            notion.apply(s, me, self.events(("p1", "會議", "2026-10-22", "10:00")))
            todo = s.query(Todo).filter_by(external_id="p1").one()
            service.modify(s, None, {"light_reminder": True}, todo_id=todo.id) if not todo.read_only else None
            changes = notion.apply(s, me, self.events(("p1", "會議", "2026-10-22", "10:00")))
            self.assertEqual((changes.added, changes.updated, changes.archived), (0, 0, 0))

    def test_a_page_done_by_one_member_is_not_added_for_another(self):
        with self.store.transaction() as s:
            me, mom = self.m(s, "爸"), self.m(s, "媽")
            notion.apply(s, me, self.events(("shared", "家庭會議", "2026-10-22", None)))
            service.complete(s, me, datetime(2026, 10, 21, tzinfo=TPE), title="家庭會議")
            self.assertEqual(notion.apply(s, mom, self.events(("shared", "家庭會議", "2026-10-22", None))).added, 0)

    def test_permission_decides_read_only(self):
        with self.store.transaction() as s:
            me = self.m(s, "爸"); me.notion_writable = True
            notion.apply(s, me, self.events(("p1", "會議", "2026-10-22", None)))
            todo = s.query(Todo).filter_by(external_id="p1").one()
            self.assertFalse(todo.read_only)
            self.assertTrue(service.modify(s, me, {"due_time": "09:00"}, todo_id=todo.id).ok)


class NotionClientTests(unittest.TestCase):
    def test_filters_and_events(self):
        from homebutler.integrations import notion as client
        filters = client.parse_filters("Status:Incoming, 類型:!私事")
        self.assertTrue(client.matches({"Status": "Incoming", "類型": "公事"}, filters))
        self.assertFalse(client.matches({"Status": "Incoming", "類型": "私事"}, filters))
        self.assertFalse(client.matches({"Status": "Done"}, filters))
        event = client.to_event({"Event": "會", "Date": {"start": "2026-10-22T10:30:00+08:00"}, "_page_id": "p"})
        self.assertEqual((event.due_date, event.due_time), (D("2026-10-22"), time(10, 30)))
        self.assertEqual(client.to_event({"Event": "假", "Date": {"start": "2026-10-23"}, "_page_id": "q"}).due_time, None)
        self.assertIsNone(client.to_event({"Event": "", "Date": {"start": "2026-10-23"}, "_page_id": "q"}))

    def test_a_failed_query_is_none_never_empty(self):
        import httpx
        from homebutler.integrations.notion import Notion
        failing = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500, json={"message": "x"})))
        self.assertIsNone(Notion("t", failing).upcoming("db", "", D("2026-10-21")))
        self.assertIsNone(Notion("", failing).upcoming("db", "", D("2026-10-21")))
        empty = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [], "has_more": False})))
        self.assertEqual(Notion("t", empty).upcoming("db", "", D("2026-10-21")), [])


if __name__ == "__main__":
    unittest.main()
