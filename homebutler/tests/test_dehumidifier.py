import time
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi.testclient import TestClient

from homebutler.api.auth import sign_assertion
from homebutler.app import create_app
from homebutler.dehumidifier import drivers, lg, panasonic, rules
from homebutler.dehumidifier.service import Dehumidifiers, Refused
from homebutler.dehumidifier.tables import Dehumidifier, DehumidifierReading
from homebutler.domain import households, tokens
from homebutler.settings import Settings
from homebutler.store.models import DeviceSetting, Member
from homebutler.tests.helpers import StoreTest

KEY = "k" * 40
ZONE = ZoneInfo("Asia/Taipei")
START = datetime(2026, 10, 21, 12, 0, tzinfo=ZONE).astimezone(timezone.utc)


class Clock:
    def __init__(self, at=START):
        self.at = at

    def __call__(self):
        return self.at

    def advance(self, minutes):
        self.at += timedelta(minutes=minutes)


class FakeLink:
    def __init__(self):
        self.online, self.humidity, self.temperature = True, {}, {}

    def state(self):
        readings = [("humidity", self.humidity), ("temperature", self.temperature)]
        return {"online": self.online, "environment": [{"name": n, "kind": kind, "value": v, "available": v is not None}
                                                        for kind, values in readings for n, v in values.items()]}


class FakePanasonic:
    """Behaves like one Panasonic unit; `answers` overrides the next results (e.g. an unknown one)."""
    configured = True

    def __init__(self):
        self.on, self.mode, self.calls, self.answers, self.readable = False, "6", [], [], True

    def status(self, auth, gwid):
        if not self.readable:
            return {"error": "無法取得除濕機狀態"}
        return {"0x00": "1" if self.on else "0", "0x01": self.mode, "0x04": "3"}

    def _answer(self, apply):
        if self.answers:
            return self.answers.pop(0)
        apply()
        return {"success": True}

    def power(self, auth, gwid, on):
        self.calls.append(("power", on))
        return self._answer(lambda: setattr(self, "on", on))

    def set_mode(self, auth, gwid, mode):
        self.calls.append(("mode", mode))
        return self._answer(lambda: setattr(self, "mode", str(panasonic.MODES[mode])))

    def set_humidity(self, auth, gwid, humidity):
        self.calls.append(("humidity", humidity))
        return self._answer(lambda: None)


class RuleTests(unittest.TestCase):
    def test_night_hours_wrap_past_midnight(self):
        self.assertEqual([rules.is_night(h, 23, 7) for h in (22, 23, 0, 6, 7)], [False, True, True, True, False])
        self.assertEqual([rules.is_night(h, 13, 15) for h in (12, 13, 14, 15)], [False, True, True, False])
        self.assertFalse(rules.is_night(5, 7, 7))

    def test_hysteresis_band(self):
        now = START
        self.assertEqual(rules.steady(62, 59, 30, 62, False, now, None, None).phase, "armed_above")
        self.assertEqual(rules.steady(62, 59, 30, 62, False, now, now - timedelta(minutes=30), None).fire, "on")
        self.assertEqual(rules.steady(62, 59, 30, 61.9, False, now, now - timedelta(hours=2), None).fire, None)
        self.assertEqual(rules.steady(62, 59, 30, 59, True, now, None, now - timedelta(minutes=30)).fire, "off")
        self.assertEqual(rules.steady(62, 59, 0, 62, False, now, None, None).fire, "on")
        self.assertEqual((rules.toggle_on(60, 60, False), rules.toggle_on(60, 59.9, True), rules.toggle_on(60, 61, None)), ("on", "off", None))

    def test_dew_point_target(self):
        self.assertAlmostEqual(rules.dew_point(25, 61), 17.0, delta=0.1)
        self.assertAlmostEqual(rules.dew_point(28, 63), 20.3, delta=0.1)
        self.assertAlmostEqual(rules.humidity_at(25, 17), 61.0, delta=0.5)
        self.assertAlmostEqual(rules.humidity_at(27, 18), 57.9, delta=0.1)
        self.assertAlmostEqual(rules.dew_point(26, rules.humidity_at(26, 17)), 17.0, delta=0.05)
        dew = rules.DewRule(18, 19, 23, 7, 65, 65)
        day = rules.goal("dewpoint", 55, dew, 27, 14)
        self.assertEqual((day.basis, day.dew, day.machine), ("day", 18, 50))
        self.assertAlmostEqual(day.humidity, rules.humidity_at(27, 18))
        night = rules.goal("dewpoint", 55, dew, 27, 1)
        self.assertEqual((night.basis, night.dew), ("night", 19))
        # Cool room: the dew point would allow more than the ceiling, so the ceiling sets it.
        self.assertEqual(rules.goal("dewpoint", 55, dew, 20, 14), rules.Goal(65.0, "cap", 18, 50))
        # No dew-point line at night, or no temperature: only the ceiling.
        self.assertEqual(rules.goal("dewpoint", 55, rules.DewRule(18, None, 23, 7, 65, 65), 27, 1), rules.Goal(65.0, "cap", None, 50))
        self.assertEqual(rules.goal("dewpoint", 55, dew, None, 14), rules.Goal(65.0, "cap", 18, 50, no_temperature=True))
        self.assertEqual(rules.goal("fixed", 55, dew, 27, 14), rules.Goal(55.0, "fixed", None, 55))
        # The ceiling also has a day and a night value.
        split = rules.DewRule(18, 19, 23, 7, 60, 65)
        self.assertEqual((rules.goal("dewpoint", 55, split, 22, 14).humidity, rules.goal("dewpoint", 55, split, 22, 1).humidity), (60.0, 65.0))
        # The machine's own target stays below anything the dew-point mode can ask for up to 30°C.
        self.assertLess(drivers.LGDriver(None, "x").auto_target(rules.DEW_MACHINE_THRESHOLD), rules.humidity_at(30, rules.DEW_MIN))

    def test_takeover_is_power_or_mode_or_our_target(self):
        expected = {"power": True, "mode": "0", "target": None}
        self.assertFalse(drivers.diverged(expected, {"power": True, "mode": "0", "target": 55}))
        self.assertTrue(drivers.diverged(expected, {"power": False, "mode": "0", "target": None}))
        self.assertTrue(drivers.diverged(expected, {"power": True, "mode": "6", "target": None}))
        self.assertFalse(drivers.diverged({"power": True, "mode": "SMART_HUMIDITY", "target": 50}, {"power": True, "mode": None, "target": None}))
        self.assertTrue(drivers.diverged({"power": True, "mode": "SMART_HUMIDITY", "target": 50}, {"power": True, "mode": "SMART_HUMIDITY", "target": 45}))

    def test_lg_targets_snap_to_the_machines_steps(self):
        self.assertEqual((lg.snap(48), lg.snap(20), lg.snap(90)), (50, 30, 70))
        self.assertEqual(drivers.LGDriver(None, "x").expected_on(60), {"power": True, "mode": "SMART_HUMIDITY", "target": 50})
        self.assertEqual(lg.dig({"humidity": {"targetHumidity": 45}}, lg.TARGET), 45)
        self.assertEqual(lg.dig({"targetHumidity": 45}, lg.TARGET), 45)


class PanasonicClientTests(unittest.TestCase):
    def client(self, handler):
        return panasonic.PanasonicClient("me", "pw", httpx.Client(transport=httpx.MockTransport(handler)), clock=time.time)

    def test_expired_token_is_renewed_once_for_a_read(self):
        seen = []
        def handle(request):
            path = request.url.path.rsplit("/", 1)[-1]
            seen.append((path, request.headers.get("cptoken")))
            if path == "userlogin1":
                return httpx.Response(200, json={"CPToken": "t1", "RefreshToken": "r1"})
            if path == "RefreshToken1":
                return httpx.Response(200, json={"CPToken": "t2", "RefreshToken": "r2"})
            if request.headers["cptoken"] == "t1":
                return httpx.Response(417, json={"StateMsg": "expired"})
            return httpx.Response(200, json={"devices": [{"Info": [{"CommandType": "0x00", "status": "1"}]}]})
        self.assertEqual(self.client(handle).status("a", "g"), {"0x00": "1"})
        self.assertEqual([p for p, _ in seen], ["userlogin1", "DeviceGetInfo", "RefreshToken1", "DeviceGetInfo"])

    def test_a_command_without_an_answer_is_unknown_and_sent_once(self):
        sent = []
        def handle(request):
            path = request.url.path.rsplit("/", 1)[-1]
            if path == "userlogin1":
                return httpx.Response(200, json={"CPToken": "t1", "RefreshToken": "r1"})
            sent.append(path)
            return httpx.Response(200, text="")
        result = self.client(handle).power("a", "g", True)
        self.assertEqual(result, {"success": False, "error": "指令結果未確認", "uncertain": True})
        self.assertEqual(sent, ["DeviceSetCommand"])

    def test_sign_in_pauses_after_three_failures(self):
        attempts = []
        def handle(request):
            attempts.append(1)
            return httpx.Response(500, text="no")
        client = self.client(handle)
        for _ in range(4):
            self.assertIn("error", client.status("a", "g"))
        self.assertEqual(len(attempts), 6)          # 3 sign-ins of 2 tries each, then nothing


class ServiceTests(StoreTest):
    def setUp(self):
        super().setUp()
        self.clock, self.link, self.unit = Clock(), FakeLink(), FakePanasonic()
        lg_client = lg.LGClient("", "TW", "x", None)
        self.service = Dehumidifiers(self.store, self.link, self.unit, lg_client, {"G1": "secret"}, ZONE, clock=self.clock)
        with self.store.transaction() as s:
            s.add(DeviceSetting(name="除濕一", kind="dehumidifier", location="房間一", external_id="G1", brand="Panasonic"))
            s.add(DeviceSetting(name="感測一", kind="sensor", location="房間一", humidity_offset=1.0))
        self.link.humidity["感測一"] = 52.0

    def row(self):
        with self.store.transaction() as s:
            row = s.get(Dehumidifier, "除濕一")
            s.expunge(row)
            return row

    def every(self, minutes, checks):
        for _ in range(checks):
            self.clock.advance(minutes)
            self.service.check()

    def test_switching_on_decides_at_once_and_picks_the_room_sensor(self):
        self.link.humidity["感測一"] = 60.0      # 61 with the offset
        view = self.service.set_rule("除濕一", True, threshold=60, duration_min=30)
        self.assertEqual(view["rule"]["sensor_name"], "感測一")
        self.assertEqual(view["rule"]["last_event"], "toggled_immediate_on")
        self.assertTrue(self.unit.on)
        self.assertEqual(self.unit.mode, "0")             # 連續除濕
        self.assertEqual(self.unit.calls, [("power", True), ("mode", "連續除濕")])

    def test_hysteresis_over_checks_then_off(self):
        self.service.set_rule("除濕一", True, threshold=60, duration_min=30)
        self.assertFalse(self.unit.on)                    # 53 < 60: already off
        self.link.humidity["感測一"] = 61.0          # 62
        self.every(5, 6)
        self.assertFalse(self.unit.on)
        self.assertEqual(self.row().phase, "armed_above")
        self.every(5, 1)
        self.assertTrue(self.unit.on)
        self.assertEqual(self.row().last_event, "triggered_on")
        self.link.humidity["感測一"] = 59.5          # 60.5, in the band
        self.every(5, 10)
        self.assertTrue(self.unit.on)
        self.link.humidity["感測一"] = 58.0          # 59
        self.every(5, 7)
        self.assertFalse(self.unit.on)
        self.assertEqual(self.row().last_event, "triggered_off")
        with self.store.transaction() as s:
            self.assertEqual(s.query(DehumidifierReading).count(), 25)      # the rule change read it once too

    def test_a_person_taking_over_ends_auto_mode_and_leaves_the_machine(self):
        self.link.humidity["感測一"] = 65.0
        self.service.set_rule("除濕一", True, threshold=60, duration_min=30)
        self.every(5, 1)
        self.unit.mode = "6"                             # changed on the machine
        calls = len(self.unit.calls)
        self.every(5, 1)
        row = self.row()
        self.assertEqual((row.auto_mode, row.last_event), (False, "auto_disabled_manual"))
        self.assertEqual(len(self.unit.calls), calls)
        self.assertTrue(self.unit.on)

    def test_commands_are_refused_while_auto_mode_is_on(self):
        self.service.set_rule("除濕一", True, threshold=60)
        with self.assertRaises(Refused):
            self.service.control("除濕一", power="on")
        self.service.set_rule("除濕一", False)
        self.assertEqual(self.service.control("除濕一", power="on", mode="目標濕度", humidity=55)["status"], "success")

    def test_an_unknown_result_is_reported_and_not_resent(self):
        self.unit.answers = [{"success": False, "error": "指令結果未確認", "uncertain": True}]
        result = self.service.control("除濕一", power="on", mode="目標濕度")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(self.unit.calls, [("power", True)])

    def test_a_partial_switch_on_is_aligned_next_time_not_taken_for_a_person(self):
        self.link.humidity["感測一"] = 65.0
        self.unit.answers = [{"success": True}, {"success": False, "error": "x"}]     # power taken, mode not
        self.unit.on = False
        self.service.set_rule("除濕一", True, threshold=60)
        self.unit.on = True                              # the power command did take effect
        self.every(5, 1)
        self.assertTrue(self.row().auto_mode)
        self.assertEqual(self.unit.mode, "0")
        self.every(5, 1)
        self.assertTrue(self.row().auto_mode)

    def test_silent_sensor_warns_then_ends_auto_mode_and_turns_off(self):
        self.link.humidity["感測一"] = 65.0
        self.service.set_rule("除濕一", True, threshold=60)
        self.link.online = False
        self.every(5, 6)
        self.assertEqual((self.row().phase, self.row().auto_mode), ("sensor_lost_warning", True))
        self.every(5, 6)
        self.assertEqual((self.row().auto_mode, self.row().last_event), (False, "auto_disabled_sensor_lost"))
        self.assertFalse(self.unit.on)

    def test_a_new_fixed_target_restarts_the_wait(self):
        self.link.humidity["感測一"] = None          # silent while switching on: no immediate decision
        self.service.set_rule("除濕一", True, threshold=55, duration_min=60)
        self.link.humidity["感測一"] = 56.0          # 57 ≥ 55+2
        self.every(5, 1)
        self.assertEqual((self.row().phase, self.row().applied_threshold), ("armed_above", 55))
        self.service.set_rule("除濕一", True, threshold=60)
        self.every(5, 1)
        row = self.row()
        self.assertEqual((row.applied_threshold, row.phase, row.above_since), (60, "idle_dry", None))

    def test_dew_point_mode_follows_the_room_temperature_and_the_night(self):
        self.link.temperature["感測一"] = 27.0
        self.link.humidity["感測一"] = 58.0          # 59 at 27°C: above 18°C's 57.9
        view = self.service.set_rule("除濕一", True, threshold_source="dewpoint", duration_min=10)
        rule = view["rule"]
        self.assertEqual(rule["last_event"], "toggled_immediate_on")
        self.assertEqual((rule["dew_day"], rule["dew_night"], rule["night_start"], rule["night_end"], rule["rh_cap_day"], rule["rh_cap_night"]),
                         (18, 19, 23, 7, 60, 65))
        self.assertEqual((rule["basis"], rule["dew_in_effect"]), ("day", 18))
        self.assertAlmostEqual(rule["effective_threshold"], 57.9, delta=0.1)
        self.assertEqual(self.row().applied_threshold, 50)
        # Cooler room, same air: the humidity reads higher, and so does the target; nothing changes.
        self.link.temperature["感測一"] = 26.0
        self.link.humidity["感測一"] = 61.0          # 62 at 26°C; 18°C means 61.4 there, so the day's 60 % ceiling sets it
        self.every(5, 4)
        self.assertTrue(self.unit.on)
        self.link.humidity["感測一"] = 57.0          # 58 ≤ 60−1
        self.every(5, 3)
        self.assertFalse(self.unit.on)
        self.assertEqual(self.row().last_event, "triggered_off")
        # Night (23:00): 19°C at 26°C is 65.4%, above the 65% ceiling, so the ceiling rules.
        self.clock.at = datetime(2026, 10, 21, 23, 30, tzinfo=ZONE).astimezone(timezone.utc)
        view = self.service.views()[0]
        self.assertEqual((view["rule"]["basis"], view["rule"]["effective_threshold"], view["rule"]["dew_in_effect"]), ("cap", 65.0, 19))
        # No temperature: only the ceiling, and it keeps running rather than counting the sensor as silent.
        self.link.temperature["感測一"] = None
        self.link.humidity["感測一"] = 66.0          # 67 ≥ 65+2
        self.every(5, 3)
        self.assertTrue(self.unit.on)
        self.assertTrue(self.service.views()[0]["rule"]["no_temperature"])

    def test_dew_point_settings_are_checked(self):
        view = self.service.set_rule("除濕一", False, threshold_source="dewpoint",
                                     dew={"dew_day": 17, "dew_night": None, "night_start": 22, "night_end": 6, "rh_cap_day": 55,
                                          "rh_cap_night": 70})
        rule = view["rule"]
        self.assertEqual((rule["dew_day"], rule["dew_night"], rule["night_start"], rule["night_end"], rule["rh_cap_day"], rule["rh_cap_night"]),
                         (17, None, 22, 6, 55, 70))
        for bad in ({"dew_day": 30}, {"dew_day": None}, {"night_start": 24}, {"rh_cap_day": 90}, {"rh_cap_night": 40}, {"rh_cap": 60}):
            with self.assertRaises(Refused):
                self.service.set_rule("除濕一", False, dew=bad)
        with self.assertRaises(Refused):
            self.service.set_rule("除濕一", False, threshold_source="custom")

    def test_unreadable_unit_changes_nothing(self):
        self.service.set_rule("除濕一", True, threshold=60)
        self.unit.readable = False
        self.link.online = False
        self.every(5, 20)
        self.assertTrue(self.row().auto_mode)
        self.assertIn("無法取得", self.service.views()[0]["status"]["error"])

    def test_without_a_credential_nothing_can_be_controlled(self):
        service = Dehumidifiers(self.store, self.link, self.unit, lg.LGClient("", "TW", "x", None), {}, ZONE, clock=self.clock)
        with self.assertRaises(Refused):
            service.set_rule("除濕一", True)
        self.assertFalse(service.views()[0]["controllable"])

    def test_a_schedule_is_refused_while_auto_mode_is_on(self):
        from homebutler.dehumidifier.service import ScheduleTarget
        from homebutler.domain.schedules import Invalid
        target = ScheduleTarget(self.service, "A")
        with self.store.transaction() as s:
            self.assertEqual(target.check(s, "除濕一", {"power": "on", "humidity": 55, "extra": 1})[1], {"power": "on", "humidity": 55})
            for bad in ({}, {"power": "maybe"}, {"humidity": 90}):
                with self.assertRaises(Invalid):
                    target.check(s, "除濕一", bad)
        self.service.set_rule("除濕一", True, threshold=60)
        calls = len(self.unit.calls)
        self.assertEqual(target.run("除濕一", {"power": "on"})["status"], "failed")
        self.assertEqual(len(self.unit.calls), calls)

    def test_butler_actions(self):
        from homebutler.dehumidifier.assistant import actions
        act = actions(self.service)
        self.assertEqual(act["_devices"](), [{"name": "除濕一", "kind": "dehumidifier", "location": "房間一"}])
        self.assertTrue(act["set_dehumidifier_auto"](None, {"scope": "all", "auto_mode": "on", "threshold": 55}).startswith(
            "✅ 除濕一 已開啟自動除濕模式，目標 55%，使用 感測一，持續 30 分鐘，目前濕度 53.0%"))
        self.assertIn("自動模式", act["control_dehumidifier"](None, {"power": "on"}))
        self.assertEqual(act["set_dehumidifier_auto"](None, {"device_name": "房間一", "auto_mode": "off"}), "✅ 除濕一 已關閉自動除濕模式")
        self.service.set_rule("除濕一", False, threshold_source="dewpoint")
        self.assertIn("目標 露點 日間 18°C／夜間 19°C（23–7 時），濕度上限 日間 60%／夜間 65%，", act["set_dehumidifier_auto"](None, {"scope": "all", "auto_mode": "on"}))
        self.service.set_rule("除濕一", False)
        self.assertEqual(act["control_dehumidifier"](None, {"mode": "目標濕度"}), "✅ 除濕一 指令已送出")
        self.assertTrue(act["query_dehumidifier"](None, {}).startswith("💧 除濕一：開啟｜模式：目標濕度｜目標濕度：55%"))
        self.assertIn("找不到", act["query_dehumidifier"](None, {"device_name": "車庫"}))


class RouteTests(StoreTest):
    def setUp(self):
        super().setUp()
        app = create_app(Settings(database_schema=self.schema, dashboard_assertion_key=KEY), store=self.store, start_jobs=False)
        self.client = TestClient(app); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.unit = FakePanasonic()
        app.state.modules["dehumidifier"] = Dehumidifiers(self.store, FakeLink(), self.unit, lg.LGClient("", "TW", "x", None),
                                                          {"G1": "secret"}, ZONE)
        with self.store.transaction() as s:
            home = households.ensure(s, "A")
            s.add(Member(household_id=home.id, name="爸", line_user_id="U1", active=True))
            s.add(DeviceSetting(name="除濕機", kind="dehumidifier", location="房間二", external_id="G1"))
            _, self.dashboard = tokens.issue(s, "dashboard", "Dashboard")

    def headers(self):
        now = int(time.time())
        return {"Authorization": "Bearer " + self.dashboard,
                "X-Dashboard-Assertion": sign_assertion(KEY, {"aud": "homebutler", "sub": "U1", "role": "member", "iat": now, "exp": now + 60})}

    def test_list_control_and_refusals(self):
        self.assertEqual(self.client.get("/v2/dehumidifiers").status_code, 401)
        units = self.client.get("/v2/dehumidifiers", headers=self.headers()).json()["dehumidifiers"]
        self.assertEqual([u["name"] for u in units], ["除濕機"])
        self.assertNotIn("secret", str(units))
        self.assertEqual(self.client.post("/v2/dehumidifiers/除濕機/control", json={"power": "on"}, headers=self.headers()).status_code, 200)
        self.unit.answers = [{"success": False, "error": "指令結果未確認", "uncertain": True}]
        self.assertEqual(self.client.post("/v2/dehumidifiers/除濕機/control", json={"power": "off"}, headers=self.headers()).status_code, 504)
        self.assertEqual(self.client.post("/v2/dehumidifiers/沒有/control", json={"power": "on"}, headers=self.headers()).status_code, 404)
        self.assertEqual(self.client.put("/v2/dehumidifiers/除濕機/rule", json={"auto_mode": True}, headers=self.headers()).status_code, 409)
        self.assertEqual(self.client.put("/v2/dehumidifiers/除濕機/rule", json={"auto_mode": True, "threshold": 99},
                                         headers=self.headers()).status_code, 422)
        self.assertEqual(self.client.get("/v2/dehumidifiers/history", headers=self.headers()).status_code, 200)
        rule = self.client.put("/v2/dehumidifiers/除濕機/rule", json={"auto_mode": False, "threshold_source": "dewpoint", "dew_night": None,
                                                                    "rh_cap_night": 60}, headers=self.headers()).json()["rule"]
        self.assertEqual((rule["threshold_source"], rule["dew_day"], rule["dew_night"], rule["rh_cap_day"], rule["rh_cap_night"]),
                         ("dewpoint", 18, None, 60, 60))
        self.assertEqual(self.client.put("/v2/dehumidifiers/除濕機/rule", json={"auto_mode": False, "dew_day": 40},
                                         headers=self.headers()).status_code, 409)
