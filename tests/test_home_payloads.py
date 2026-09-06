"""Offline contract tests: load pure entrypoint bodies without production SDK startup.

Dependencies are fakes; these tests never read credentials, Sheets or real devices.
"""
import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Lock
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load_function(filename, name, dependencies):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    fn.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
    exec(compile(module, filename, "exec"), dependencies)
    return dependencies[name]


class HomePayloadTests(unittest.TestCase):
    def test_life_summary_never_calls_weather_and_reads_only_two_sheets(self):
        loads = []
        rows = {"待辦事項": [{"狀態": "待辦"}, {"狀態": "已完成"}],
                "食品庫存": [{"狀態": "有效"}, {"狀態": "已消耗"}]}
        class Context:
            def load(self, names=None): loads.append(names)
            def get(self, name): return rows.get(name, [])
        def no_weather(*args): raise AssertionError("weather must not be on the life-card path")
        fn = load_function("web_api.py", "api_dashboard", {
            "RequestContext": Context, "ThreadPoolExecutor": ThreadPoolExecutor,
            "weather_api": SimpleNamespace(get_weather_summary=no_weather),
        })
        self.assertEqual(fn(False), {"todos": [{"狀態": "待辦"}], "food": [{"狀態": "有效"}]})
        self.assertEqual(loads, [["待辦事項", "食品庫存"]])

    def test_legacy_dashboard_still_includes_weather_and_metadata(self):
        class Context:
            def load(self): pass
            def get(self, _): return []
        fn = load_function("web_api.py", "api_dashboard", {
            "RequestContext": Context, "ThreadPoolExecutor": ThreadPoolExecutor,
            "weather_api": SimpleNamespace(get_weather_summary=lambda date, _: {"date": date}),
            "api_get_device_options": lambda: {"ac": {}},
        })
        result = fn()
        self.assertEqual(result["weatherToday"], {"date": "today"})
        self.assertEqual(result["weatherTomorrow"], {"date": "tomorrow"})
        self.assertEqual(set(result), {"weatherToday", "weatherTomorrow", "devices", "options", "todos", "food"})

    def test_sensor_summary_preserves_current_and_does_not_touch_history(self):
        class UnreadableHistory(dict):
            def __iter__(self): raise AssertionError("summary must not assemble history")
        current = {"t": 1000, "temp": 26, "humidity": 58, "co2": 800}
        sensors = {"living": {"meta": {"location": "living"}, "current": current,
                              "last_polled_at": 1000, "history_dict": UnreadableHistory()}}
        fn = load_function("sensor_state.py", "snapshot", {
            "time": SimpleNamespace(time=lambda: 1000), "_lock": Lock(),
            "_sensors": sensors, "OFFLINE_THRESHOLD_S": 600,
        })
        summary = fn(False)
        self.assertEqual(summary["living"]["current"], current)
        self.assertTrue(summary["living"]["online"])
        self.assertEqual(summary["living"]["history"], [])
        self.assertEqual(fn(False, name="unknown"), {})
        sensors["living"]["history_dict"] = {i: {**current, "t": i} for i in range(288)}
        full = fn()
        self.assertEqual(len(full["living"]["history"]), 288)
        self.assertEqual(len(fn(name="living")["living"]["history"]), 288)
        self.assertLess(len(json.dumps(summary)), len(json.dumps(full)) / 20)

    def test_sensor_route_forwards_optional_controls(self):
        calls = []
        fn = load_function("web_api.py", "api_sensors_status", {
            "sensor_state": SimpleNamespace(snapshot=lambda **kwargs: calls.append(kwargs) or {}),
        })
        fn()
        fn(False, "living")
        self.assertEqual(calls, [{"include_history": True, "name": ""},
                                 {"include_history": False, "name": "living"}])


if __name__ == "__main__": unittest.main()
