import base64
import hashlib
import hmac
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from homebutler.app import create_app
from homebutler.assistant import pipeline
from homebutler.assistant.actions import Services, Turn
from homebutler.assistant.line import LineBot, login_code
from homebutler.assistant.pipeline import Butler
from homebutler.domain import households, pairing, tokens
from homebutler.domain.device_targets import ACTarget, IRTarget
from homebutler.integrations.line import signature_ok
from homebutler.settings import Settings
from homebutler.store.models import ConversationMessage, Device, DeviceEntity, FoodItem, Member, Schedule, Todo
from homebutler.tests.helpers import StoreTest

ZONE = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=ZONE).astimezone(timezone.utc)


def act(_action, **args):
    return {"action": _action, "args": [{"key": k, "value": str(v)} for k, v in args.items()]}


class FakeClaude:
    def __init__(self):
        self.answers, self.prompts, self.phrased = [], [], []

    def parse(self, system, messages):
        self.prompts.append((system, messages))
        answer = self.answers.pop(0)
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)

    def phrase(self, question, data, actions, style, today):
        self.phrased.append(data)
        return "（整理後）" + data


class FakeLink:
    online = True

    def state(self):
        return {"online": self.online, "environment": [{"name": "感測一", "kind": "temperature", "value": 25.0, "available": True},
                                                       {"name": "感測一", "kind": "humidity", "value": 60.0, "available": True}],
                "climates": [{"id": "c1", "name": "冷氣一", "available": True, "uncertain": False, "hvac_mode": "cool",
                              "temperature": 26, "fan_mode": "auto", "source_updated_at": 1}]}


class FakeCommands:
    def __init__(self):
        self.sent = []

    def climate(self, ha_id, patch):
        self.sent.append((ha_id, patch))
        return {"status": "success", "message": "HA 已執行"}

    def ir(self, ha_id):
        self.sent.append((ha_id, None))
        return {"status": "success", "message": "ok"}


class FakeScheduler:
    reconciled = 0

    def reconcile(self):
        self.reconciled += 1


class FakeLine:
    def __init__(self):
        self.replies, self.pushes = [], []

    def reply(self, token, text):
        self.replies.append(text)

    def push(self, user, text):
        self.pushes.append((user, text))

    def loading(self, user):
        pass

    def picture(self, user):
        return "https://example.invalid/p.png"


class Base(StoreTest):
    def setUp(self):
        super().setUp()
        self.commands, self.claude, self.line = FakeCommands(), FakeClaude(), FakeLine()
        targets = {"ac": ACTarget(self.store, self.commands), "ir": IRTarget(self.store, self.commands)}
        self.services = Services(self.store, FakeLink(), targets, FakeScheduler(), ZONE, clock=lambda: NOW)
        self.butler = Butler(self.services, self.claude, lambda: "1.80.0")
        self.bot = LineBot(self.butler, self.line)
        with self.store.transaction() as s:
            home = households.ensure(s, "A")
            dad = Member(household_id=home.id, name="爸", title="爸爸", line_user_id="U1", active=True)
            mom = Member(household_id=home.id, name="媽", title="媽媽", line_user_id="U2", active=True)
            s.add_all([dad, mom, Member(household_id=home.id, name="停用", line_user_id="U3", active=False)])
            ac = Device(kind="ac", name="冷氣一", household_id=home.id)
            fan = Device(kind="ir", name="客房電風扇", household_id=home.id)
            sensor = Device(kind="sensor", name="感測一", household_id=home.id)
            s.add_all([ac, fan, sensor])
            s.flush()
            s.add_all([DeviceEntity(device_id=ac.id, ha_id="c1", entity_id="climate.one", role="climate"),
                       DeviceEntity(device_id=fan.id, ha_id="b1", entity_id="button.p", role="button", label="開")])
            self.dad, self.mom = dad.id, mom.id

    def say(self, text, user="U1"):
        self.bot.message(user, "token", text)
        return self.line.replies[-1]


class PipelineTests(Base):
    def test_arguments_come_back_typed_and_schedule_commands_become_params(self):
        data = pipeline.flatten(act("add_schedule", device_name="冷氣一", target_action="control_ac", power="on", temperature="26.5",
                                    trigger_time="2026-10-21 22:00"))
        self.assertEqual(data["params"], {"power": "on", "temperature": 26.5})
        self.assertEqual(pipeline.coerce("weekdays", "1,3,5"), [1, 3, 5])
        self.assertEqual(pipeline.coerce("light_notify", "false"), False)
        self.assertEqual(pipeline.coerce("month_day", "x"), "x")

    def test_the_prompt_carries_the_household_and_ac_state(self):
        self.claude.answers = [{"actions": [], "reply": "好的"}]
        self.assertEqual(self.say("你好"), "好的")
        system, messages = self.claude.prompts[0]
        self.assertIn("爸（稱謂：爸爸）", system)
        self.assertIn("冷氣一（類型：空調，位置：，目前狀態：開機 26°C 冷氣 風速自動）", system)
        self.assertIn("客房電風扇（類型：IR，位置：，按鈕：開）", system)
        self.assertIn("系統版本 v1.80.0", system)
        self.assertEqual(messages, [{"role": "user", "content": "你好"}])

    def test_writes_use_claudes_reply_and_failures_show_the_real_result(self):
        self.claude.answers = [{"actions": [act("add_food", name="牛奶", quantity="1", unit="瓶", expiry="2026-10-25")], "reply": "牛奶已登記 🥛"},
                               {"actions": [act("delete_food", name="蛋")], "reply": "已劃掉"}]
        self.assertEqual(self.say("買了牛奶"), "牛奶已登記 🥛")
        self.assertEqual(self.say("蛋吃完了"), "❌ 找不到 蛋")
        with self.store.transaction() as s:
            self.assertEqual(s.query(FoodItem).count(), 1)

    def test_a_completion_that_did_not_happen_is_not_covered_by_the_reply(self):
        with self.store.transaction() as s:
            s.add_all([Todo(household_id=1, title="倒垃圾", assignee_id=self.dad, due_date=NOW.date()),
                       Todo(household_id=1, title="倒垃圾", assignee_id=self.dad, due_date=NOW.date() + timedelta(days=1))])
        self.claude.answers = [{"actions": [act("delete_todo", item="倒垃圾")], "reply": "已劃掉 ✅"}]
        self.assertTrue(self.say("垃圾倒了").startswith("找到多筆待辦"))

    def test_queries_are_phrased_and_realtime_results_shown_as_is(self):
        self.claude.answers = [{"actions": [act("query_sensor", device_name="感測一")], "reply": "x"},
                               {"actions": [act("query_devices")], "reply": "x"}]
        self.assertEqual(self.say("房間熱嗎"), "（整理後）🌡️ 感測一:溫度 25.0°C，濕度 60.0%")
        self.assertTrue(self.say("有哪些設備").startswith("已設定的設備：\n• 冷氣一（空調"))

    def test_device_commands_go_through_ha_and_arm_the_shutdown(self):
        self.claude.answers = [{"actions": [act("control_ac", power="on", temperature="25.5", mode="cool")], "reply": "已開 26 度"},
                               {"actions": [act("control_ir", device_name="客房電扇", button="開")], "reply": "電扇已開"},
                               {"actions": [act("control_ir", device_name="主臥電扇", button="開")], "reply": "電扇已開"}]
        self.assertEqual(self.say("開冷氣"), "已開 26 度")
        self.assertEqual(self.commands.sent, [("c1", {"power": "on", "temperature": 26, "mode": "cool"})])
        self.assertEqual(self.services.scheduler.reconciled, 1)
        self.assertEqual(self.say("開電扇"), "電扇已開")
        self.assertIn("找不到", self.say("開主臥電扇"))
        self.assertEqual(len(self.commands.sent), 2)

    def test_schedules_from_line(self):
        self.claude.answers = [
            {"actions": [act("add_schedule", device_name="冷氣一", target_action="control_ac", power="off", trigger_time="2026-10-21 23:00")], "reply": "好"},
            {"actions": [act("modify_schedule", device_name="冷氣一", trigger_time="2026-10-21 23:00", trigger_time_new="2026-10-21 23:30")], "reply": "改好"},
            {"actions": [act("query_schedule")], "reply": "x"},
            {"actions": [act("delete_schedule", device_name="冷氣一", trigger_time="2026-10-21 23:30")], "reply": "取消了"}]
        self.assertEqual(self.say("11點關冷氣"), "好")
        self.assertEqual(self.say("改到11點半"), "改好")
        self.assertEqual(self.say("有什麼排程"), "排程列表：\n• 冷氣一｜關機｜2026-10-21 23:30｜待執行")
        self.assertEqual(self.say("取消"), "取消了")
        with self.store.transaction() as s:
            self.assertEqual([(x.status, x.created_by) for x in s.query(Schedule)], [("cancelled", "爸")])

    def test_style_and_conversation_memory(self):
        self.claude.answers = [{"actions": [act("set_style", style="說話像海盜")], "reply": "好"}, {"actions": [], "reply": "啊哈"}]
        self.say("用海盜的口氣")
        self.assertEqual(self.say("查看風格"), "📝 您目前的自訂風格：\n說話像海盜")
        self.say("早")
        system, messages = self.claude.prompts[1]
        self.assertIn("使用者自訂風格：說話像海盜", system)
        self.assertEqual(messages[:2], [{"role": "user", "content": "用海盜的口氣"}, {"role": "assistant", "content": "好"}])

    def test_prose_and_broken_answers(self):
        self.claude.answers = ["我是一段話", "{壞掉}", ""]
        self.assertEqual(self.say("a"), "我是一段話")
        self.assertEqual(self.say("b"), "抱歉，系統處理時發生了一點問題，請再試一次。")
        self.assertEqual(self.say("c"), pipeline.NOT_UNDERSTOOD)

    def test_assignment_is_pushed_to_the_other_member(self):
        self.claude.answers = [{"actions": [act("add_todo", item="繳費", date="2026-10-22", person="媽")], "reply": "好"}]
        self.say("請媽繳費")
        self.assertEqual(self.line.pushes[0][0], "U2")
        self.assertIn("繳費", self.line.pushes[0][1])

    def test_missing_dehumidifier_module_is_said_plainly(self):
        self.claude.answers = [{"actions": [act("control_dehumidifier", power="on")], "reply": "好"}]
        self.assertEqual(self.say("開除濕機"), "❌ 目前沒有除濕機功能")


class LineTests(Base):
    def test_only_active_members_get_anything(self):
        for user in ("U3", "Uunknown", None):
            self.assertEqual(self.say("你好", user), "❌ 僅限已啟用的家庭成員使用。")
        self.assertEqual(self.claude.prompts, [])
        with self.store.transaction() as s:
            self.assertEqual(s.query(ConversationMessage).count(), 0)

    def test_login_codes(self):
        self.assertEqual([login_code(t) for t in ("123456", "123 456", "登入 123-456", "１２３４５６", "登入很麻煩", "1234567")],
                         ["123456", "123456", "123456", "123456", None, None])
        with self.store.transaction() as s:
            made = pairing.create(s, NOW)
        self.assertEqual(self.say(made["user_code"]), "✅ 已授權登入 Dashboard（以 爸 的身分），回到網頁就會自動進入 🏠")
        self.assertEqual(self.say(made["user_code"]), "❌ 驗證碼錯誤或已過期，請回 Dashboard 重新取得一組。")
        with self.store.transaction() as s:
            got = pairing.collect(s, made["device_token"], NOW)
            self.assertEqual((got["status"], got["user"]["lineUserId"], got["user"]["picture"]),
                             ("approved", "U1", "https://example.invalid/p.png"))
            self.assertEqual(pairing.collect(s, made["device_token"], NOW)["status"], "consumed")
            self.assertEqual(pairing.collect(s, "guess", NOW)["status"], "not_found")
            late = pairing.create(s, NOW)
        self.bot.butler.services.clock = lambda: NOW + timedelta(minutes=6)
        self.assertIn("已過期", self.say(late["user_code"]))
        self.assertEqual(self.claude.prompts, [])

    def test_broadcast(self):
        self.assertEqual(self.say("@all 晚餐好了"), "✅ 已廣播給全體成員")
        self.assertEqual(sorted(self.line.pushes), [("U1", "📢 爸：晚餐好了"), ("U2", "📢 爸：晚餐好了")])

    def test_webhook_checks_the_signature_and_answers_at_once(self):
        settings = Settings(database_schema=self.schema, line_channel_secret="s" * 32, line_channel_token="t", anthropic_api_key="k")
        app = create_app(settings, store=self.store, start_jobs=False)
        app.state.line_bot = self.bot
        body = json.dumps({"events": [{"type": "message", "replyToken": "r", "source": {"userId": "U3"},
                                       "message": {"type": "text", "text": "hi"}}]}).encode()
        good = base64.b64encode(hmac.new(b"s" * 32, body, hashlib.sha256).digest()).decode()
        self.assertTrue(signature_ok("s" * 32, body, good))
        with TestClient(app) as client:
            self.assertEqual(client.post("/v2/line/webhook", content=body, headers={"X-Line-Signature": "bad"}).status_code, 400)
            self.assertEqual(client.post("/v2/line/webhook", content=body, headers={"X-Line-Signature": good}).status_code, 200)
            for _ in range(50):
                if self.line.replies:
                    break
                time.sleep(0.02)
        self.assertEqual(self.line.replies, ["❌ 僅限已啟用的家庭成員使用。"])

    def test_pairing_routes_need_the_dashboard_key(self):
        app = create_app(Settings(database_schema=self.schema), store=self.store, start_jobs=False)
        with TestClient(app) as client:
            with self.store.transaction() as s:
                _, key = tokens.issue(s, "dashboard", "Dashboard")
            self.assertEqual(client.post("/v2/pairing").status_code, 401)
            made = client.post("/v2/pairing", headers={"Authorization": "Bearer " + key}).json()
            self.assertEqual(len(made["user_code"]), 6)
            status = client.post("/v2/pairing/collect", json={"device_token": made["device_token"]},
                                 headers={"Authorization": "Bearer " + key}).json()
            self.assertEqual(status, {"status": "pending"})


class SettingsTests(unittest.TestCase):
    def test_line_needs_both_halves_and_claude(self):
        from homebutler.settings import SettingsError
        for env in ({"LINE_CHANNEL_SECRET": "x"}, {"LINE_CHANNEL_SECRET": "x", "LINE_CHANNEL_ACCESS_TOKEN": "y"}):
            with self.assertRaises(SettingsError):
                Settings.from_env(env)
