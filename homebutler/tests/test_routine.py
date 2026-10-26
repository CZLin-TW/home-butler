import gzip
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from homebutler.assistant.actions import Services
from homebutler.domain import households
from homebutler.jobs.backup import Backup, restore
from homebutler.jobs.daily import DailySummary
from homebutler.jobs.health import Health
from homebutler.store.models import Computer, FoodItem, Member, Todo, ApiToken
from homebutler.tests.helpers import StoreTest

ZONE = ZoneInfo("Asia/Taipei")


class Clock:
    def __init__(self, local):
        self.at = local.astimezone(timezone.utc)

    def __call__(self):
        return self.at

    def advance(self, **delta):
        self.at += timedelta(**delta)


class Push:
    def __init__(self):
        self.sent, self.fail = [], False

    def __call__(self, target, text):
        if self.fail:
            raise RuntimeError("down")
        self.sent.append((target, text))


class FakeClaude:
    def notify(self, summary, now_text, style=""):
        return "【整理】" + summary


class FakeLine:
    def __init__(self):
        self.push = Push()

    def quota_left(self):
        return (150, 200)


class FakeWeather:
    def for_notice(self, when):
        return f"{when}晴"


class FakeLink:
    def __init__(self):
        self.up = True
        self.observations = []

    def online(self):
        return self.up

    def can(self, capability):
        return self.up

    def state(self):
        return {"online": self.up, "climates": [], "environment": [], "observations": self.observations}


class Base(StoreTest):
    def setUp(self):
        super().setUp()
        with self.store.transaction() as s:
            home = households.ensure(s, "A")
            dad = Member(household_id=home.id, name="爸", line_user_id="U1", active=True)
            mom = Member(household_id=home.id, name="媽", line_user_id="U2", active=True, receives_alerts=True)
            s.add_all([dad, mom])
            s.flush()
            self.home, self.dad, self.mom = home.id, dad.id, mom.id


class DailyTests(Base):
    def make(self, local):
        self.clock, self.line = Clock(local), FakeLine()
        services = Services(self.store, FakeLink(), {}, None, ZONE, weather=FakeWeather(), clock=self.clock)
        return DailySummary(services, FakeClaude(), self.line, 21)

    def test_once_a_day_after_the_hour_with_each_members_facts(self):
        job = self.make(datetime(2026, 10, 21, 20, 0, tzinfo=ZONE))
        with self.store.transaction() as s:
            s.add(FoodItem(household_id=self.home, name="牛奶", quantity=1, unit="瓶", expires_on=date(2026, 10, 22), added_on=date(2026, 10, 1)))
            s.add(Todo(household_id=self.home, title="私事", assignee_id=self.dad, visibility="private", due_date=date(2026, 10, 22)))
            s.add(Todo(household_id=self.home, title="倒垃圾", visibility="public", due_date=date(2026, 10, 21)))
        self.assertEqual(job.run(), "not yet")
        self.clock.advance(hours=1, minutes=5)
        self.assertEqual(job.run(), "sent 2")
        self.assertEqual(job.run(), "already sent")
        dad = dict(self.line.push.sent)["U1"]
        mom = dict(self.line.push.sent)["U2"]
        self.assertIn("【重點】明日天氣預報：tomorrow晴", dad)
        self.assertIn("食品到期提醒：牛奶（2026-10-22）", dad)
        self.assertIn("明日與未完成待辦：⚠️ 未完成 倒垃圾（2026-10-21（三））", dad)
        self.assertIn("您的私人待辦：私事", dad)
        self.assertNotIn("私事", mom)
        self.assertTrue(dad.endswith("📊 本月推播額度：剩餘 150/200 則"))


class HealthTests(Base):
    def make(self):
        self.clock, self.push, self.link = Clock(datetime(2026, 10, 21, 12, tzinfo=ZONE)), Push(), FakeLink()
        self.theater_ok = True

        def theater():
            if not self.theater_ok:
                raise RuntimeError("no answer")
        return Health(self.store, self.link, self.push, ZONE, self.clock, 900, theater=theater)

    def test_computer_silence_is_announced_once_and_its_recovery_with_the_length(self):
        health = self.make()
        with self.store.transaction() as s:
            token = ApiToken(kind="telemetry", label="Mac", token_hash="h" * 64)
            s.add(token)
            s.flush()
            s.add(Computer(token_id=token.id, hostname="Mac mini", last_heartbeat_at=self.clock(), current={}))
        health.run()
        self.assertEqual(self.push.sent, [])
        self.clock.advance(minutes=20)
        health.run()
        health.run()
        self.assertEqual([t for t, _ in self.push.sent], ["U2"])          # only the member who asked
        self.assertIn("🔴 電腦監控失聯", self.push.sent[0][1])
        with self.store.transaction() as s:
            s.query(Computer).update({"last_heartbeat_at": self.clock()})
        self.clock.advance(minutes=1)
        health.run()
        self.assertIn("失聯約 1 分鐘", self.push.sent[-1][1])          # counted from when it was announced, as before

    def test_ha_absence_counts_after_15_minutes_and_a_failed_push_is_retried(self):
        health = self.make()
        self.link.up = False
        health.run()
        self.clock.advance(minutes=10)
        health.run()
        self.assertEqual(self.push.sent, [])
        self.clock.advance(minutes=6)
        self.push.fail = True
        health.run()
        self.push.fail = False
        health.run()
        self.assertEqual(len(self.push.sent), 1)
        self.assertIn("Home Assistant", self.push.sent[0][1])
        self.link.up = True
        health.run()
        self.assertIn("🟢 Home Assistant 已重新連上", self.push.sent[-1][1])

    def test_presence_unavailable_for_15_minutes_is_announced_and_its_recovery(self):
        health = self.make()
        zone = lambda name, available: {"name": name, "kind": "occupancy", "value": None, "available": available}
        self.link.observations = [zone("客廳", False), zone("廚房", True), {"name": "光照", "kind": "illuminance", "available": False}]
        health.run()
        self.clock.advance(minutes=14)
        health.run()
        self.assertEqual(self.push.sent, [])
        self.link.observations[1] = zone("廚房", False)          # a second one goes later
        self.clock.advance(minutes=2)
        health.run()
        self.assertEqual(len(self.push.sent), 1)
        self.assertIn("🔴 空間感測無法使用", self.push.sent[0][1])
        self.assertIn("客廳", self.push.sent[0][1])
        self.assertNotIn("廚房", self.push.sent[0][1])            # not yet 15 minutes
        self.assertNotIn("光照", self.push.sent[0][1])            # only presence counts
        self.link.observations = [zone("客廳", True), zone("廚房", False)]
        health.run()
        self.assertEqual(len(self.push.sent), 1)                  # still one down: no recovery yet
        self.link.observations = [zone("客廳", True), zone("廚房", True)]
        health.run()
        self.assertIn("🟢 空間感測恢復", self.push.sent[-1][1])

    def test_presence_is_not_judged_while_ha_is_away(self):
        health = self.make()
        self.link.observations = [{"name": "客廳", "kind": "occupancy", "value": None, "available": False}]
        self.link.up = False
        health.run()
        self.clock.advance(minutes=16)
        health.run()
        self.assertTrue(all("空間感測" not in text for _, text in self.push.sent))

    def test_theater_needs_three_failures(self):
        health = self.make()
        self.theater_ok = False
        health.run()
        health.run()
        self.assertEqual(self.push.sent, [])
        health.run()
        self.assertIn("劇院服務無回應", self.push.sent[-1][1])


class BackupTests(Base):
    def test_round_trip_and_once_a_day_after_three(self):
        written = []

        class Target:
            def write(self, data):
                written.append(data)
        clock = Clock(datetime(2026, 10, 21, 2, 0, tzinfo=ZONE))
        backup = Backup(self.store, Target(), ZONE, clock)
        self.assertEqual(backup.run(), "not due")
        clock.advance(hours=1, minutes=5)
        self.assertTrue(backup.run().startswith("wrote"))
        self.assertEqual(backup.run(), "not due")
        document = json.loads(gzip.decompress(written[0]))
        self.assertEqual(document["counts"]["members"], 2)
        self.assertNotIn("sensor_readings", document["tables"])
        with self.store.transaction() as s:
            s.query(Member).filter_by(name="媽").update({"name": "改掉了"})
        loaded = restore(self.store.engine, written[0])
        self.assertEqual(loaded["members"], 2)
        with self.store.transaction() as s:
            self.assertEqual(sorted(m.name for m in s.query(Member)), ["媽", "爸"])
            self.assertEqual(sorted(t.title for t in s.query(Todo)), [])
            s.add(Member(household_id=self.home, name="新來的", line_user_id="U9", active=True))     # ids still free after a restore
        self.assertFalse(backup.stale())
        clock.advance(hours=31)
        self.assertTrue(backup.stale())
        self.assertTrue(backup.run().startswith("wrote"))
        self.assertFalse(backup.stale())
