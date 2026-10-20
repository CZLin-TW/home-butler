from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from homebutler import migration, modules
from homebutler.domain import system_state
from homebutler.store.models import (AcReading, AutoOffCycle, ConversationMessage, Device, FoodItem, LightArea, Member, Remote,
                                     Schedule, SensorReading, Todo, TodoTemplate)
from homebutler.tests.helpers import StoreTest

ZONE = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 25, 4, 0, tzinfo=timezone.utc)

TABS = {
    "家庭成員": [{"名稱": "爸", "稱謂": "爸爸", "Line User ID": "U1", "狀態": "啟用", "管家風格": "像海盜"},
              {"名稱": "媽", "Line User ID": "U2", "狀態": "啟用"}],
    "遙控器": [{"名稱": "牆上", "密碼": "1234", "啟用": "TRUE"}],
    "智能居家": [{"名稱": "冷氣一", "類型": "空調", "位置": "房間一", "Device ID": "x", "Auth": "never-copied", "自動關機小時數": "1"},
              {"名稱": "感測一", "類型": "感應器", "位置": "房間一", "濕度補償": "-2"},
              {"名稱": "除濕一", "類型": "除濕機", "位置": "房間一", "品牌": "LG"}],
    "Hue 照明區域": [{"Hue ID": "g1", "資源類型": "grouped_light", "Hue 名稱": "Living", "顯示名稱": "區一", "狀態": "啟用"},
                 {"Hue ID": "g2", "Hue 名稱": "Bed", "狀態": "停用"}],
    "週期待辦模板": [{"規則ID": "r1", "事項": "倒垃圾", "重複類型": "每週", "星期": "1,4", "時間": "20:00", "負責人": "爸", "類型": "公開",
                 "燈光提醒": "TRUE", "燈光區域ID": "g1", "起始日期": "2026-10-01", "狀態": "啟用", "最後生成日期": "2026-10-23"}],
    "待辦事項": [
        {"事項": "倒垃圾", "日期": "2026-10-27", "時間": "20:00", "負責人": "爸", "狀態": "待辦", "類型": "公開", "來源": "本地",
         "燈光提醒": "TRUE", "燈光區域ID": '["g1","gone"]', "規則ID": "r1"},
        {"事項": "開會", "日期": "2026-10-26", "時間": "9:30", "負責人": "媽", "狀態": "待辦", "類型": "私人", "來源": "Notion",
         "屬性": "唯讀", "外部ID": "page-1"},
        {"事項": "已做的會", "日期": "2026-10-20", "負責人": "媽", "狀態": "已完成", "來源": "Notion", "外部ID": "page-2"},
        {"事項": "已做完的本地事", "日期": "2026-10-20", "負責人": "爸", "狀態": "已完成", "來源": "本地"},
        {"事項": "", "日期": "2026-10-20", "狀態": "待辦"}],
    "食品庫存": [{"品名": "牛奶", "數量": "2", "單位": "瓶", "過期日": "2026-10-28", "新增日": "2026-10-20", "新增者": "爸", "狀態": "有效"},
             {"品名": "吃完的", "數量": "1", "狀態": "已消耗"}],
    "排程指令": [
        {"設備名稱": "冷氣一", "動作": "control_ac", "參數": '{"power":"off","_auto_hours":1}', "觸發時間": "2026-10-25 7:00",
         "建立者": "系統", "建立時間": "2026-10-24 23:00", "狀態": "待執行", "來源": "自動（HA）"},
        {"設備名稱": "冷氣一", "動作": "control_ac", "參數": '{"power":"on","temperature":27}', "觸發時間": "2026-10-26 22:00",
         "建立者": "爸", "狀態": "待執行", "來源": "使用者（HA）"},
        {"設備名稱": "除濕一", "動作": "control_dehumidifier", "參數": '{"power":"on"}', "觸發時間": "2026-10-24 22:00",
         "狀態": "待確認", "來源": "使用者", "執行結果": "結果未確認"},
        {"設備名稱": "冷氣一", "動作": "control_ac", "參數": '{"power":"off"}', "觸發時間": "2026-10-20 22:00", "狀態": "已執行", "來源": "使用者"},
        {"設備名稱": "冷氣一", "動作": "control_ac", "參數": "{壞", "觸發時間": "2026-10-26 22:00", "狀態": "待執行", "來源": "使用者"}],
    "對話暫存": [{"Line User ID": "U1", "角色": "user", "內容": "早", "時間": "2026-10-25 08:00:00"},
             {"Line User ID": "Ux", "角色": "user", "內容": "陌生人", "時間": "2026-10-25 08:00:00"}],
    "除濕機自動規則": [{"device_name": "除濕一", "auto_mode": "TRUE", "sensor_name": "感測一", "duration_min": "0", "threshold": "55",
                  "threshold_source": "自訂"}],
    "系統狀態": [{"鍵": "最後每日推播日期", "值": "2026-10-24"}],
}
HISTORY = [("sensor", "感測一", NOW.timestamp() - 600, {"timestamp": 1, "temp": "25.5", "humidity": "60", "co2": ""}),
           ("ac", "冷氣一", NOW.timestamp() - 600, {"power": "on", "temperature": "26", "mode": "冷氣", "fan_speed": "自動"}),
           ("dehumidifier", "除濕一", NOW.timestamp() - 600, {"power": "off"}),
           ("pc", "10.0.0.5", NOW.timestamp() - 600, {"cpu_pct": 3}),
           ("sensor", "沒這台", NOW.timestamp() - 600, {"temp": 1})]


class MigrationTests(StoreTest):
    def migrate(self, replace=False):
        with self.store.transaction() as s:
            return migration.run(s, TABS, HISTORY, ZONE, "A", default_light_area="區一", replace=replace, now=NOW)

    def test_everything_the_family_uses_comes_across(self):
        report = self.migrate()
        with self.store.transaction() as s:
            dad = s.query(Member).filter_by(name="爸").one()
            self.assertEqual((dad.butler_style, s.query(Remote).count()), ("像海盜", 1))
            areas = {a.ha_target: a for a in s.query(LightArea)}
            self.assertEqual((areas["g1"].name, areas["g1"].is_default, areas["g2"].enabled), ("區一", True, False))
            template = s.query(TodoTemplate).one()
            self.assertEqual((template.rule, template.weekdays, template.visibility, [a.ha_target for a in template.light_areas]),
                             ("weekly", [1, 4], "public", ["g1"]))
            todos = {t.title: t for t in s.query(Todo)}
            self.assertEqual(sorted(todos), ["倒垃圾", "已做的會", "開會"])
            self.assertEqual((todos["倒垃圾"].template_id, [a.ha_target for a in todos["倒垃圾"].light_areas]), (template.id, ["g1"]))
            self.assertEqual((todos["開會"].read_only, todos["開會"].external_id, str(todos["開會"].due_time)), (True, "page-1", "09:30:00"))
            self.assertEqual(todos["已做的會"].status, "done")         # keeps the Notion task from coming back
            self.assertEqual([f.name for f in s.query(FoodItem)], ["牛奶"])
            schedules = {(x.target_kind, x.params.get("power"), x.source): x for x in s.query(Schedule)}
            auto = schedules[("ac", "off", "auto_off")]
            self.assertEqual((auto.params, auto.status, auto.due_at.astimezone(ZONE).strftime("%H:%M")), ({"power": "off"}, "pending", "07:00"))
            cycle = s.query(AutoOffCycle).one()
            self.assertEqual((cycle.id, cycle.hours, cycle.closed_at), (auto.cycle_id, 1, None))
            self.assertEqual(schedules[("dehumidifier", "on", "user")].status, "unknown")
            self.assertEqual(len(schedules), 3)
            self.assertEqual(s.query(ConversationMessage).count(), 1)
            self.assertEqual(system_state.get(s, "daily_push_date"), "2026-10-24")
            rule = s.execute(modules_table("dehumidifiers").select()).one()
            self.assertEqual((rule.auto_mode, rule.duration_min, rule.threshold_source), (True, 0, "custom"))
            self.assertEqual((s.query(SensorReading).one().humidity, s.query(AcReading).one().power), (60.0, "on"))
            self.assertEqual({(d.kind, d.name) for d in s.query(Device)}, {("ac", "冷氣一"), ("sensor", "感測一")})
            self.assertNotIn("never-copied", str([r for r in s.execute(modules_table("dehumidifiers").select())]))
        self.assertEqual(report.read["待辦事項"], 4)
        self.assertEqual(report.written["history:sensor"], 1)
        self.assertEqual(report.read["history:pc"], 1)
        self.assertTrue(any("無法辨識" in x for x in report.skipped))

    def test_a_second_run_refuses_unless_asked_to_replace(self):
        self.migrate()
        with self.assertRaises(migration.NotEmpty):
            self.migrate()
        self.migrate(replace=True)
        with self.store.transaction() as s:
            self.assertEqual((s.query(Todo).count(), s.query(Schedule).count(), s.query(AutoOffCycle).count()), (3, 3, 1))


def modules_table(name):
    modules.load()
    return migration.modules_table(name)
