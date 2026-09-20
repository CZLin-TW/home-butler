"""Reminder fan-out and persistence with fake Sheets and fake device commands."""
import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
import unittest
from unittest.mock import Mock, patch

from pydantic import BaseModel, Field
from todo_light_areas import decode_area_ids, encode_area_ids, normalize_area_ids
from todo_access import select_todo, visible
from test_callback_concurrency import endpoint

ROOT = Path(__file__).resolve().parents[1]
AREAS = {"living": {"Hue 名稱": "客廳"}, "bedroom": {"Hue 名稱": "主臥"}}


def resolve_area(name="", area_id=""):
    target = area_id or "living"
    return {"id": target, "name": AREAS.get(target, {}).get("Hue 名稱", target), "resource_type": "grouped_light"}


def helper_env():
    env = dict(DEFAULT_LIGHT_AREA_NAME="客廳", resolve_area=resolve_area,
               load_area_settings=lambda: AREAS, decode_area_ids=decode_area_ids,
               encode_area_ids=encode_area_ids, normalize_area_ids=normalize_area_ids)
    for name in ("parse_bool", "bool_cell", "resolve_light_area"):
        endpoint("handlers/todo_helpers.py", name, env)
    return env


class MultiAreaTests(unittest.TestCase):
    def setUp(self):
        self.helpers = helper_env()
        self.cell = '["living","bedroom"]'
        self.row = {"待辦ID": "todo-1", "事項": "家事", "狀態": "待辦", "負責人": "Alice", "類型": "私人",
                    "屬性": "讀寫", "日期": "2026-09-20", "時間": "19:00", "燈光提醒": "TRUE", "燈光區域ID": self.cell}
        self.sheet = Mock()
        self.ctx = SimpleNamespace(actor_name="Alice", get=lambda _: [self.row], get_worksheet=lambda _: self.sheet)
        self.write = Mock(return_value=1)
        self.env = dict(self.helpers, LIGHT_NOTIFY_COLUMN="燈光提醒", LIGHT_AREA_ID_COLUMN="燈光區域ID",
                        TODO_ID="待辦ID", new_todo_id=lambda: "new", ensure_columns=Mock(),
                        append_record=self.write, update_row_fields=self.write, select_todo=select_todo,
                        _resolve_light_area=self.helpers["resolve_light_area"], _parse_bool=self.helpers["parse_bool"],
                        _bool_cell=self.helpers["bool_cell"], _resolve_light_notify=lambda data: data.get("light_notify", False))

    def test_legacy_scalar_and_json_roundtrip_deduplication(self):
        self.assertEqual(decode_area_ids("living"), ["living"])
        self.assertEqual(encode_area_ids([" living ", "bedroom", "living"]), self.cell)
        self.assertEqual(encode_area_ids(["living"]), "living")
        self.assertEqual(decode_area_ids(self.cell), ["living", "bedroom"])
        self.assertEqual(decode_area_ids("[]"), [])
        for invalid in ['[bad', '[null]', '[""]']:
            with self.assertRaises(ValueError): decode_area_ids(invalid)

    def test_add_and_modify_store_all_targets_and_reject_invalid_before_write(self):
        add = endpoint("handlers/todo.py", "handle_add_todo", self.env)
        data = dict(item="家事", date="2026-09-20", time="19:00", light_notify=True, light_area_ids=["living", "bedroom", "living"])
        self.assertIn("客廳、主臥", add(data, "Alice", self.ctx))
        self.assertEqual(self.write.call_args.args[1]["燈光區域ID"], self.cell)
        modify = endpoint("handlers/todo.py", "handle_modify_todo", self.env)
        self.write.reset_mock()
        for ids in [[], ["unknown"], ["living", "unknown"], [None]]:
            self.assertIn("❌", modify(dict(todo_id="todo-1", item="家事", item_new="不該寫入", light_area_ids=ids), "Alice", self.ctx))
            self.write.assert_not_called()
        self.assertIn("✅", modify(dict(todo_id="todo-1", item="家事", light_area_ids=["bedroom"]), "Alice", self.ctx))
        self.assertEqual(self.write.call_args.args[2], {"燈光區域ID": "bedroom"})

    def test_unrelated_edit_preserves_multi_targets_disable_clears(self):
        modify = endpoint("handlers/todo.py", "handle_modify_todo", self.env)
        modify(dict(todo_id="todo-1", item="家事", item_new="改名"), "Alice", self.ctx)
        self.assertNotIn("燈光區域ID", self.write.call_args.args[2])
        self.assertEqual(self.row["燈光區域ID"], self.cell)
        modify(dict(todo_id="todo-1", item="改名", light_notify=False), "Alice", self.ctx)
        self.assertEqual(self.write.call_args.args[2], {"燈光提醒": "FALSE", "燈光區域ID": ""})

    def test_recurring_template_add_edit_and_materialization_preserve_targets(self):
        rule = {**self.row, "規則ID": "rule-1", "重複類型": "每天", "狀態": "啟用"}
        self.sheet.get_all_records.return_value = [rule]
        env = dict(self.env, RECUR_TYPES=["每天"], RULE_ID_COLUMN="規則ID", TODO_SHEET="待辦事項",
                   _gen_rule_id=lambda: "rule-1", now_taipei=lambda: datetime(2026, 9, 20),
                   _template_sheet=lambda: self.sheet, materialize_recurring_todos=Mock(),
                   resolve_light_notify=lambda data: data.get("light_notify", False), format_recur_summary=lambda _: "每天", visible=visible)
        endpoint("handlers/recurring_todo.py", "_build_light_data", env)
        endpoint("handlers/recurring_todo.py", "_find_active", env)
        add = endpoint("handlers/recurring_todo.py", "handle_add_recurring_todo", env)
        self.assertIn("✅", add(dict(item="家事", recur_type="每天", time="19:00", light_notify=True,
                                     light_area_ids=["living", "bedroom"]), "Alice", self.ctx))
        self.assertEqual(self.write.call_args.args[1]["燈光區域ID"], self.cell)
        modify = endpoint("handlers/recurring_todo.py", "handle_modify_recurring_todo", env)
        self.assertIn("✅", modify(dict(rule_id="rule-1", light_area_ids=["bedroom"]), "Alice", self.ctx))
        self.assertEqual(self.write.call_args.args[2], {"燈光區域ID": "bedroom"})
        generate = endpoint("handlers/recurring_todo.py", "_materialize_one", env)
        generate(self.sheet, rule, "2026-09-21", self.ctx)
        self.assertEqual(self.write.call_args.args[1]["燈光區域ID"], self.cell)

    def test_due_collection_fans_out_but_ignores_finished_future_disabled_and_corrupt(self):
        rows = [self.row, {**self.row, "燈光區域ID": "bedroom"},
                {**self.row, "狀態": "已完成"}, {**self.row, "日期": "2026-09-21"},
                {**self.row, "燈光提醒": "FALSE"}, {**self.row, "燈光區域ID": "[bad"},
                {**self.row, "燈光區域ID": "[]"}]
        env = dict(RequestContext=lambda: SimpleNamespace(load=lambda: None, get=lambda _: rows),
                   now_taipei=lambda: datetime(2026, 9, 20, 20), TZ=SimpleNamespace(localize=lambda x: x),
                   datetime=datetime, decode_area_ids=decode_area_ids, resolve_area=resolve_area,
                   DEFAULT_LIGHT_AREA_NAME="客廳", _sheet_bool=lambda value: value == "TRUE")
        collect = endpoint("web_api.py", "collect_todo_light_reminders", env)
        reminders = collect()["reminders"]
        self.assertEqual([r["light_area_id"] for r in reminders], ["living", "bedroom", "bedroom"])
        import lighting_reminders as worker
        send = Mock(side_effect=[TimeoutError("unknown"), None, None, None])
        with patch.dict("sys.modules", {"web_api": SimpleNamespace(collect_todo_light_reminders=collect),
             "home_assistant_api": SimpleNamespace(link=SimpleNamespace(snapshot=lambda: {"hue_available": True}))}), \
             patch.object(worker, "ha_enabled", return_value=True), patch.object(worker, "_attempted", {}), \
             patch("lighting_transport.send_command_sync", send), patch.object(worker.time, "time", return_value=120) as clock:
            worker.tick(); worker.tick()
            self.assertEqual(send.call_count, 2)
            self.assertEqual({c.args[1]["resource_id"] for c in send.call_args_list}, {"living", "bedroom"})
            self.assertTrue(all(c.args[0] == "hue.breathe" for c in send.call_args_list))
            clock.return_value = 180
            worker.tick()
            self.assertEqual(send.call_count, 4)
            rows.clear(); clock.return_value = 240; worker.tick()
            self.assertEqual(send.call_count, 4)

    def test_four_api_models_and_routes_forward_multi_area_list(self):
        for kind, route in [("TodoAddRequest", "api_add_todo"), ("TodoModifyRequest", "api_modify_todo"),
                            ("RecurringTodoAddRequest", "api_add_recurring_todo"), ("RecurringTodoModifyRequest", "api_modify_recurring_todo")]:
            env = dict(BaseModel=BaseModel, Field=Field, Optional=Optional)
            tree = ast.parse((ROOT / "web_api.py").read_text())
            node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == kind)
            exec(compile(ast.Module(body=[node], type_ignores=[]), "web_api.py", "exec"), env)
            req = env[kind](item="家事", date="2026-09-20", person="Alice", requester="Alice", recur_type="每天",
                            light_notify=True, time="19:00", light_area_ids=["living", "bedroom"])
            handler = Mock(return_value="✅")
            env.update(RequestContext=lambda: SimpleNamespace(load=lambda: None), _set_actor=lambda *_: "Alice")
            env[route.replace("api_", "handle_", 1)] = handler
            endpoint("web_api.py", route, env)(req)
            self.assertEqual(handler.call_args.args[0]["light_area_ids"], ["living", "bedroom"])


if __name__ == "__main__": unittest.main()
