import time
from datetime import date, timedelta

from fastapi.testclient import TestClient

from homebutler.api.auth import sign_assertion
from homebutler.app import create_app
from homebutler.domain import households, tokens
from homebutler.domain.passwords import hash_password
from homebutler.settings import Settings
from homebutler.store.models import (Device, DeviceEntity, DeviceSetting, FoodItem, LightArea, Member, Remote, SensorReading, Todo,
                                     utcnow)
from homebutler.tests.helpers import StoreTest

KEY = "k" * 40


class FakeLink:
    def __init__(self):
        self.hue = []

    def state(self):
        return {"connected": True, "online": True, "received_at": 1.0, "age_seconds": 3.0, "stale_after_seconds": 90,
                "capabilities": ["climate_control"], "observations": [{"name": "客廳", "kind": "occupancy", "value": True}],
                "environment": [{"name": "感測一", "kind": "humidity", "value": 50.0, "available": True}],
                "climates": [{"id": "c1", "name": "冷氣一", "available": True, "uncertain": False, "hvac_mode": "off",
                              "temperature": 26, "fan_mode": "auto", "source_updated_at": 1}],
                "ir_buttons": [{"id": "b1", "available": True}]}

    def automation_log(self):
        return {"online": True, "shared": False, "received_at": None, "rules": [], "runs": []}

    async def hue_command(self, action, payload):
        self.hue.append((action, payload))
        if action == "hue.list_areas":
            return {"status": "success", "result": {"areas": [{"id": "g1", "hue_name": "Living", "resource_type": "grouped_light"}]}}
        return {"status": "success", "result": {"ok": True}}


class FakeCommands:
    def __init__(self):
        self.sent, self.outcome = [], {"status": "success", "message": "HA 已執行"}

    def climate(self, ha_id, patch):
        self.sent.append(patch)
        return self.outcome


class ApiTests(StoreTest):
    def setUp(self):
        super().setUp()
        self.app = create_app(Settings(database_schema=self.schema, dashboard_assertion_key=KEY), store=self.store, start_jobs=False)
        self.client = TestClient(self.app); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.app.state.ha = self.link = FakeLink()
        self.commands = FakeCommands()
        self.app.state.schedule_targets["ac"].commands = self.commands
        today = date.today()
        with self.store.transaction() as s:
            home = households.ensure(s, "A")
            dad = Member(household_id=home.id, name="爸", line_user_id="U1", active=True)
            s.add_all([dad, Remote(household_id=home.id, name="牆上", password_hash=hash_password("1234"))])
            ac, sensor = Device(kind="ac", name="冷氣一", household_id=home.id), Device(kind="sensor", name="感測一", household_id=home.id)
            s.add_all([ac, sensor, DeviceSetting(name="感測一", kind="sensor", location="房間一", humidity_offset=2.0)])
            s.flush()
            s.add_all([DeviceEntity(device_id=ac.id, ha_id="c1", entity_id="climate.one", role="climate"),
                       DeviceEntity(device_id=sensor.id, ha_id="h1", entity_id="sensor.h", role="humidity"),
                       SensorReading(device_id=sensor.id, t=utcnow(), humidity=52.0),
                       Todo(household_id=home.id, title="繳費", assignee_id=dad.id, due_date=today + timedelta(days=1)),
                       Todo(household_id=home.id, title="很久以後", assignee_id=dad.id, due_date=today + timedelta(days=30)),
                       FoodItem(household_id=home.id, name="牛奶", quantity=1, unit="瓶", expires_on=today, added_on=today)])
            _, self.key = tokens.issue(s, "dashboard", "Dashboard")
            self.remote_id = s.query(Remote).one().id

    def headers(self, sub="U1", role="member"):
        now = int(time.time())
        return {"Authorization": "Bearer " + self.key,
                "X-Dashboard-Assertion": sign_assertion(KEY, {"aud": "homebutler", "sub": sub, "role": role, "iat": now, "exp": now + 60})}

    def test_sign_in_questions(self):
        key = {"Authorization": "Bearer " + self.key}
        self.assertEqual(self.client.post("/v2/sign-in/member", json={"line_user_id": "U1"}, headers=key).json(), {"active": True, "name": "爸"})
        self.assertFalse(self.client.post("/v2/sign-in/member", json={"line_user_id": "U9"}, headers=key).json()["active"])
        self.assertEqual(self.client.post("/v2/sign-in/remote", json={"password": "nope"}, headers=key).status_code, 401)
        self.assertEqual(self.client.post("/v2/sign-in/remote", json={"password": "1234"}, headers=key).json()["name"], "牆上")
        self.assertTrue(self.client.get(f"/v2/sign-in/remote/{self.remote_id}", headers=key).json()["active"])
        self.assertEqual(self.client.post("/v2/sign-in/member", json={"line_user_id": "U1"}).status_code, 401)

    def test_todos_and_food_by_id_and_members_only(self):
        page = self.client.get("/v2/todos", headers=self.headers()).json()
        first = page["todos"][0]
        self.assertEqual((first["title"], first["assignee"], page["members"]), ("繳費", "爸", ["爸"]))
        self.assertEqual(self.client.get("/v2/todos", headers=self.headers(str(self.remote_id), "remote")).status_code, 403)
        made = self.client.post("/v2/todos", json={"title": "買菜", "due_date": date.today().isoformat(), "due_time": "18:00"},
                                headers=self.headers()).json()
        self.assertEqual(made["item"]["due_time"], "18:00")
        self.assertEqual(self.client.patch(f"/v2/todos/{made['item']['id']}", json={"due_time": ""}, headers=self.headers()).json()["item"]["due_time"], None)
        self.assertEqual(self.client.post(f"/v2/todos/{made['item']['id']}/complete", headers=self.headers()).status_code, 200)
        self.assertEqual(self.client.post(f"/v2/todos/{made['item']['id']}/complete", headers=self.headers()).status_code, 409)
        self.assertEqual(self.client.post("/v2/todo-templates", json={"title": "倒垃圾", "rule": "weekly", "weekdays": [1, 4]},
                                          headers=self.headers()).status_code, 200)
        self.assertEqual(self.client.get("/v2/todos", headers=self.headers()).json()["templates"][0]["weekdays"], [1, 4])
        food = self.client.get("/v2/food", headers=self.headers()).json()["food"]
        self.assertEqual(self.client.patch(f"/v2/food/{food[0]['id']}", json={"quantity": 3}, headers=self.headers()).status_code, 200)
        self.assertEqual(self.client.delete(f"/v2/food/{food[0]['id']}", headers=self.headers()).status_code, 200)
        self.assertEqual(self.client.get("/v2/food", headers=self.headers()).json()["food"], [])

    def test_home_shows_what_needs_attention(self):
        home = self.client.get("/v2/home", headers=self.headers()).json()
        self.assertEqual([t["title"] for t in home["todos"]], ["繳費"])
        self.assertEqual([f["name"] for f in home["food"]], ["牛奶"])
        self.assertEqual(self.client.get("/v2/weather", headers=self.headers()).status_code, 503)

    def test_devices_page_history_and_commands(self):
        page = self.client.get("/v2/devices", headers=self.headers(str(self.remote_id), "remote")).json()
        self.assertEqual((page["acs"][0]["name"], page["acs"][0]["power"]), ("冷氣一", "off"))
        self.assertEqual(page["sensors"], [{"name": "感測一", "location": "房間一", "available": True, "humidity": 52.0}])
        self.assertEqual(page["home_assistant"]["age_seconds"], 3.0)
        self.assertIn("dehumidifier", page["options"]["schedule_targets"])
        self.assertEqual(page["dehumidifiers"], [])
        history = self.client.get("/v2/history?hours=6", headers=self.headers()).json()
        self.assertEqual(history["sensors"]["感測一"][0]["humidity"], 52.0)
        self.assertEqual(self.client.post("/v2/devices/ac/冷氣一", json={"power": "on", "temperature": 25.5}, headers=self.headers()).status_code, 200)
        self.assertEqual(self.commands.sent, [{"power": "on", "temperature": 26}])
        self.assertEqual(self.client.post("/v2/devices/ac/冷氣一", json={"temperature": 40}, headers=self.headers()).status_code, 409)
        self.commands.outcome = {"status": "unknown", "message": "未確認"}
        self.assertEqual(self.client.post("/v2/devices/ac/冷氣一", json={"power": "off"}, headers=self.headers()).status_code, 504)
        self.assertEqual(self.client.post("/v2/devices/ac/沒有", json={"power": "off"}, headers=self.headers()).status_code, 409)

    def test_lighting_keeps_the_area_table_and_names(self):
        listed = self.client.get("/v2/lighting", headers=self.headers()).json()["areas"]
        area_id = listed[0]["area_id"]
        self.assertEqual(self.client.patch(f"/v2/lighting/areas/{area_id}", json={"display_name": "客廳燈", "is_default": True},
                                           headers=self.headers()).json()["display_name"], "客廳燈")
        self.assertEqual(self.client.patch(f"/v2/lighting/areas/{area_id}", json={"display_name": "x"},
                                           headers=self.headers(str(self.remote_id), "remote")).status_code, 403)
        self.assertEqual(self.client.patch("/v2/lighting/g1/state", json={"on": True, "brightness": 40}, headers=self.headers()).status_code, 200)
        self.assertEqual(self.client.patch("/v2/lighting/g1/state", json={}, headers=self.headers()).status_code, 422)
        self.assertEqual(self.link.hue[-1], ("hue.set_state", {"area_id": "g1", "on": True, "brightness": 40.0, "resource_type": "grouped_light"}))
        with self.store.transaction() as s:
            area = s.query(LightArea).one()
            self.assertEqual((area.name, area.is_default, area.usable), ("客廳燈", True, True))
