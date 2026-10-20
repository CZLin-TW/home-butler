import time
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from homebutler.api.auth import sign_assertion
from homebutler.app import create_app
from homebutler.domain import households, schedules, tokens
from homebutler.domain.device_targets import ACTarget, IRTarget
from homebutler.jobs.schedules import Scheduler
from homebutler.settings import Settings
from homebutler.store.models import AutoOffCycle, Device, DeviceEntity, DeviceSetting, Member, Schedule
from homebutler.tests.helpers import StoreTest

KEY = "k" * 40
ZONE = ZoneInfo("Asia/Taipei")
START = datetime(2026, 9, 14, 9, 0, tzinfo=ZONE).astimezone(timezone.utc)


def local(at):
    return at.astimezone(ZONE).strftime("%Y-%m-%d %H:%M")


class Clock:
    def __init__(self):
        self.at = START

    def __call__(self):
        return self.at

    def advance(self, minutes):
        self.at += timedelta(minutes=minutes)


class FakeLink:
    def __init__(self):
        self.online, self.mode, self.uncertain, self.available = True, "cool", False, True

    def state(self):
        return {"online": self.online, "climates": [{"id": "c1", "name": "冷氣一", "available": self.available, "uncertain": self.uncertain,
                                                     "hvac_mode": self.mode, "temperature": 26, "fan_mode": "auto",
                                                     "source_updated_at": 1}]}


class FakeCommands:
    def __init__(self):
        self.sent, self.outcome = [], {"status": "success", "message": "HA 已執行"}

    def climate(self, ha_id, patch):
        self.sent.append(("climate", ha_id, patch))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def ir(self, ha_id):
        self.sent.append(("ir", ha_id))
        return self.outcome


class WindowTests(unittest.TestCase):
    def test_hold_window_moves_only_deadlines_inside_it(self):
        def due(hour, minute, text):
            start = datetime(2026, 9, 14, hour, minute, 40)
            return schedules.deferred(start + timedelta(hours=3), schedules.parse_window(text))
        night = "22:00-07:00"
        self.assertEqual(due(14, 0, night), datetime(2026, 9, 14, 17, 0))
        self.assertEqual(due(18, 59, night), datetime(2026, 9, 14, 21, 59))
        self.assertEqual(due(19, 0, night), datetime(2026, 9, 15, 7, 0))
        self.assertEqual(due(3, 0, night), datetime(2026, 9, 14, 7, 0))
        self.assertEqual(due(5, 0, night), datetime(2026, 9, 14, 8, 0))
        self.assertEqual(due(10, 30, "13:00-15:00"), datetime(2026, 9, 14, 15, 0))
        self.assertEqual(due(20, 0, "07:00-07:00"), datetime(2026, 9, 15, 7, 0))
        self.assertEqual(due(4, 0, " 7：00 ～ 07:00 "), datetime(2026, 9, 14, 7, 0))
        self.assertEqual(due(4, 1, "07:00-07:00"), datetime(2026, 9, 15, 7, 0))
        for text in ("", None, "22:00", "22-07", "24:00-07:00", "22:60-07:00", "22:00-07:00x", 2200):
            self.assertIsNone(schedules.parse_window(text))

    def test_cells_are_read_strictly_and_explained(self):
        self.assertEqual([schedules.parse_hours(t) for t in ("", "3", "3.0", "0", "168", "169", "1.5", "一", "-1")],
                         [0, 3, 3, 0, 168, None, None, None, None])
        now = datetime(2026, 9, 14, 20, 0, 30, tzinfo=ZONE)
        def read(hours, window):
            return schedules.settings_view(DeviceSetting(name="x", auto_off_hours_text=hours, auto_off_hold_text=window), now, ZONE)
        good = read("1", " 7：00 ～ 07:00 ")
        self.assertEqual((good["window"], good["problems"], good["preview_off_at"]), ("07:00-07:00", [], datetime(2026, 9, 15, 7, 0, tzinfo=ZONE)))
        self.assertIsNone(read("0", "")["preview_off_at"])
        self.assertEqual(read("1", "7點-7點")["problems"], ["window_unreadable"])
        self.assertEqual(read("一", "22:00-07:00")["problems"], ["hours_unreadable"])
        self.assertEqual(read("1.5", "x")["problems"], ["hours_unreadable", "window_unreadable"])
        self.assertEqual(read("0", "22:00-07:00")["problems"], ["window_without_hours"])


class SchedulerTests(StoreTest):
    def setUp(self):
        super().setUp()
        self.clock, self.link, self.commands = Clock(), FakeLink(), FakeCommands()
        self.targets = {"ac": ACTarget(self.store, self.commands), "ir": IRTarget(self.store, self.commands)}
        self.scheduler = Scheduler(self.store, self.link, self.targets, ZONE, clock=self.clock)
        with self.store.transaction() as s:
            home = households.ensure(s, "A")
            ac = Device(kind="ac", name="冷氣一", household_id=home.id)
            fan = Device(kind="ir", name="電扇一", household_id=home.id)
            s.add_all([ac, fan])
            s.flush()
            s.add_all([DeviceEntity(device_id=ac.id, ha_id="c1", entity_id="climate.one", role="climate"),
                       DeviceEntity(device_id=fan.id, ha_id="b1", entity_id="button.power", role="button", label="電源")])
            s.add(DeviceSetting(name="冷氣一", kind="ac", auto_off_hours_text="3"))
            self.home = home.id

    def set_cells(self, hours=None, window=None):
        with self.store.transaction() as s:
            setting = s.get(DeviceSetting, "冷氣一")
            if hours is not None:
                setting.auto_off_hours_text = hours
            if window is not None:
                setting.auto_off_hold_text = window

    def tick(self, minutes=0):
        self.clock.advance(minutes)
        return self.scheduler.run()

    def rows(self, **where):
        with self.store.transaction() as s:
            found = list(s.query(Schedule).filter_by(**where).order_by(Schedule.id))
            for row in found:
                s.expunge(row)
            return found

    def auto(self):
        return self.rows(source="auto_off")

    def test_first_on_arms_once_and_changes_do_not_restart(self):
        self.tick()
        self.assertEqual([(local(s.due_at), s.status) for s in self.auto()], [("2026-09-14 12:00", "pending")])
        self.link.mode = "dry"
        self.tick(60)
        Scheduler(self.store, self.link, self.targets, ZONE, clock=self.clock).run()      # a restart
        self.assertEqual(len(self.auto()), 1)

    def test_seen_off_ends_the_run_and_the_next_on_starts_a_new_one(self):
        self.tick()
        self.link.mode = "off"
        self.tick(30)
        self.assertEqual(self.auto()[0].status, "cancelled")
        self.link.mode = "cool"
        self.tick(30)
        self.assertEqual([s.status for s in self.auto()], ["cancelled", "pending"])
        self.assertEqual(local(self.auto()[1].due_at), "2026-09-14 13:00")

    def test_a_deleted_shutdown_stays_deleted_for_this_run(self):
        self.tick()
        with self.store.transaction() as s:
            schedules.remove(s, self.auto()[0].id, self.clock())
        self.tick(5)
        Scheduler(self.store, self.link, self.targets, ZONE, clock=self.clock).run()
        self.assertEqual([s.status for s in self.auto()], ["cancelled"])

    def test_hours_changes_apply_next_run_and_zero_cancels_without_revival(self):
        self.tick()
        self.set_cells(hours="2")
        self.tick(30)
        self.assertEqual(local(self.auto()[0].due_at), "2026-09-14 12:00")
        self.link.online = False
        self.set_cells(hours="0")
        self.tick(10)
        self.assertEqual(self.auto()[0].status, "pending")      # offline: nothing changes
        self.link.online = True
        self.tick(10)
        self.assertEqual(self.auto()[0].status, "cancelled")
        self.set_cells(hours="2")
        self.tick(10)
        self.assertEqual(len(self.auto()), 1)
        self.link.mode = "off"
        self.tick(10)
        self.link.mode = "cool"
        self.tick(10)
        self.assertEqual(local(self.auto()[-1].due_at), "2026-09-14 12:20")

    def test_window_applies_when_the_run_starts(self):
        self.set_cells(window="10:00-18:00")
        self.tick()
        self.assertEqual(local(self.auto()[0].due_at), "2026-09-14 18:00")
        self.set_cells(window="")
        self.tick(30)
        self.assertEqual(local(self.auto()[0].due_at), "2026-09-14 18:00")

    def test_unsure_state_is_not_off_and_holds_the_shutdown(self):
        self.tick()
        self.clock.at = self.auto()[0].due_at + timedelta(minutes=10)
        for change in ({"uncertain": True}, {"available": False}, {"online": False}):
            for key, value in change.items():
                setattr(self.link, key, value)
            self.tick(1)
            self.assertEqual(self.auto()[0].status, "pending")
            self.assertEqual(self.commands.sent, [])
            self.link.uncertain, self.link.available, self.link.online = False, True, True
        self.clock.at = self.auto()[0].due_at + timedelta(hours=3)
        self.link.uncertain = True
        self.tick()
        self.assertEqual(self.auto()[0].status, "expired")

    def test_due_shutdown_is_sent_once(self):
        self.tick()
        self.tick(180)
        self.assertEqual(self.commands.sent, [("climate", "c1", {"power": "off"})])
        self.assertEqual(self.auto()[0].status, "done")
        self.tick(1)
        self.assertEqual(len(self.commands.sent), 1)

    def test_edited_into_another_action_survives_the_end_of_the_run(self):
        self.tick()
        with self.store.transaction() as s:
            schedules.modify(s, self.targets, self.auto()[0].id, ZONE, self.clock(), params={"power": "on", "temperature": 27},
                             due_local="2026-09-14 20:00")
        self.link.mode = "off"
        self.tick(10)
        self.assertEqual(self.auto()[0].status, "pending")
        self.clock.at = datetime(2026, 9, 14, 20, 0, tzinfo=ZONE).astimezone(timezone.utc)
        self.tick()
        self.assertEqual(self.commands.sent, [("climate", "c1", {"power": "on", "temperature": 27})])

    def test_auto_shutdown_stays_on_its_own_ac(self):
        self.tick()
        with self.store.transaction() as s, self.assertRaises(schedules.Invalid):
            schedules.modify(s, self.targets, self.auto()[0].id, ZONE, self.clock(), kind="ir", name="電扇一", params={"button": "電源"})

    def add(self, minutes=0, kind="ac", name="冷氣一", params=None):
        with self.store.transaction() as s:
            return schedules.add(s, self.targets, kind, name, params or {"power": "off"}, local(self.clock() + timedelta(minutes=minutes)),
                                 ZONE, self.clock(), "爸").id

    def test_unknown_and_failed_are_kept_never_resent_and_can_be_dismissed(self):
        self.set_cells(hours="0")
        first = self.add(1)
        self.commands.outcome = {"status": "unknown", "message": "HA 指令結果未確認；不會自動重送"}
        self.tick(1)
        second = self.add(1, "ir", "電扇一", {"button": "電源"})
        self.commands.outcome = {"status": "failed", "message": "HA 拒絕"}
        self.tick(1)
        self.tick(1)
        self.assertEqual(len(self.commands.sent), 2)
        with self.store.transaction() as s:
            self.assertEqual([(x.id, x.status) for x in schedules.visible(s)], [(first, "unknown"), (second, "failed")])
            with self.assertRaises(schedules.Invalid):
                schedules.modify(s, self.targets, first, ZONE, self.clock(), due_local=local(self.clock() + timedelta(hours=1)))
            schedules.remove(s, first, self.clock())
            self.assertEqual([x.id for x in schedules.visible(s)], [second])

    def test_a_raising_send_is_unknown_and_an_interrupted_one_is_found_at_start(self):
        self.set_cells(hours="0")
        self.add(1)
        self.commands.outcome = RuntimeError("boom")
        self.tick(1)
        self.assertEqual(self.rows()[0].status, "unknown")
        self.add(1)
        with self.store.transaction() as s:
            s.query(Schedule).filter_by(status="pending").update({"status": "claimed"})
        Scheduler(self.store, self.link, self.targets, ZONE, clock=self.clock).run()
        self.assertEqual([r.status for r in self.rows()], ["unknown", "unknown"])
        self.assertIn("重新啟動", self.rows()[1].result)

    def test_a_device_gone_or_bad_params_sends_nothing(self):
        self.set_cells(hours="0")
        self.add(1)
        with self.store.transaction() as s:
            s.query(Device).filter_by(name="冷氣一").update({"removed_at": self.clock()})
        self.tick(1)
        self.assertEqual((self.rows()[0].status, self.commands.sent), ("failed", []))
        with self.store.transaction() as s:
            for bad in ({"temperature": 40}, {"mode": "烤"}):
                with self.assertRaises(schedules.Invalid):
                    schedules.add(s, self.targets, "ir", "電扇一", {"button": "不存在"}, local(self.clock()), ZONE, self.clock(), "爸")
            with self.assertRaises(schedules.Invalid):
                schedules.add(s, self.targets, "ac", "冷氣一", {"power": "off"}, "明天", ZONE, self.clock(), "爸")

    def test_only_one_open_run_per_ac(self):
        self.tick()
        from sqlalchemy.exc import IntegrityError
        with self.assertRaises(IntegrityError):
            with self.store.transaction() as s:
                device = s.query(Device).filter_by(name="冷氣一").one()
                s.add(AutoOffCycle(device_id=device.id, started_at=self.clock(), hours=1))


class RouteTests(StoreTest):
    def setUp(self):
        super().setUp()
        self.app = create_app(Settings(database_schema=self.schema, dashboard_assertion_key=KEY), store=self.store, start_jobs=False)
        self.client = TestClient(self.app); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        with self.store.transaction() as s:
            home = households.ensure(s, "A")
            s.add(Member(household_id=home.id, name="爸", line_user_id="U1", active=True))
            ac = Device(kind="ac", name="冷氣一", household_id=home.id)
            s.add(ac)
            s.flush()
            s.add(DeviceEntity(device_id=ac.id, ha_id="c1", entity_id="climate.one", role="climate"))
            _, self.dashboard = tokens.issue(s, "dashboard", "Dashboard")

    def headers(self):
        now = int(time.time())
        return {"Authorization": "Bearer " + self.dashboard,
                "X-Dashboard-Assertion": sign_assertion(KEY, {"aud": "homebutler", "sub": "U1", "role": "member", "iat": now, "exp": now + 60})}

    def test_add_change_list_remove(self):
        self.assertEqual(self.client.get("/v2/schedules").status_code, 401)
        later = (datetime.now(ZONE) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        made = self.client.post("/v2/schedules", headers=self.headers(),
                                json={"target_kind": "ac", "target_name": "冷氣一", "params": {"power": "on", "temperature": 25.5}, "due_local": later})
        self.assertEqual(made.status_code, 200, made.text)
        body = made.json()
        self.assertEqual((body["params"], body["created_by"], body["due_local"]), ({"power": "on", "temperature": 26}, "爸", later))
        self.assertEqual(self.client.patch(f"/v2/schedules/{body['id']}", headers=self.headers(), json={"params": {"power": "off"}}).json()["params"],
                         {"power": "off"})
        listed = self.client.get("/v2/schedules", headers=self.headers()).json()
        self.assertEqual([s["id"] for s in listed["schedules"]], [body["id"]])
        self.assertEqual(listed["auto_off"][0]["status"], "disabled")
        self.assertEqual(self.client.post("/v2/schedules", headers=self.headers(),
                                          json={"target_kind": "tv", "target_name": "x", "due_local": later}).status_code, 409)
        self.assertEqual(self.client.delete(f"/v2/schedules/{body['id']}", headers=self.headers()).json()["status"], "cancelled")
        self.assertEqual(self.client.delete(f"/v2/schedules/{body['id']}", headers=self.headers()).status_code, 409)
        self.assertEqual(self.client.delete("/v2/schedules/999", headers=self.headers()).status_code, 404)
        self.assertIn("dehumidifier", self.app.state.schedule_targets)


class CommandsTests(unittest.TestCase):
    def test_commands_reach_the_server_loop_and_need_one(self):
        import asyncio
        import threading
        from homebutler.integrations.ha.commands import Commands

        class Link:
            async def climate_command(self, ha_id, patch):
                return {"status": "success", "seen": (ha_id, patch)}
        self.assertEqual(Commands(Link(), lambda: None).climate("c1", {})["status"], "failed")
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        try:
            self.assertEqual(Commands(Link(), lambda: loop).climate("c1", {"power": "off"})["seen"], ("c1", {"power": "off"}))
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(2)
            loop.close()
