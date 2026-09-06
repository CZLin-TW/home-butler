"""Isolated endpoint tests; SDK calls are fakes and no app startup runs."""
import ast
import asyncio
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]


def endpoint(filename, name, env):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    fn.decorator_list = []
    fn.returns = None
    for arg in fn.args.args: arg.annotation = None
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), filename, "exec"), env)
    return env[name]


class CallbackConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_line_sdk_error_stays_400_and_releases_serialization_lock(self):
        from unittest.mock import Mock
        class HttpError(Exception):
            def __init__(self, status_code, detail): self.status_code = status_code
        handler = Mock(side_effect=[ValueError("invalid signature"), None])
        fn = endpoint("main.py", "callback", {
            "webhook_handler": SimpleNamespace(handle=handler), "run_in_threadpool": asyncio.to_thread,
            "_line_callback_lock": asyncio.Lock(), "HTTPException": HttpError,
            "traceback": SimpleNamespace(format_exc=lambda: "fake invalid signature")})
        class Request:
            headers = {"X-Line-Signature": "fake"}
            async def body(self): return b"fake"
        with self.assertRaises(HttpError) as error: await fn(Request())
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(await fn(Request()), "OK")

    async def test_lighting_keeps_websocket_on_loop_and_sheets_off_loop(self):
        loop_thread = threading.get_ident()
        async def send(*args, **kwargs):
            self.assertEqual(threading.get_ident(), loop_thread)
            return {"status": "ok", "agent_id": "fake", "result": {"areas": [{"id": "a"}]}}
        def apply(areas):
            self.assertNotEqual(threading.get_ident(), loop_thread)
            return [{**a, "display_name": "測試"} for a in areas]
        fn = endpoint("lighting_api.py", "api_lighting_areas", {
            "send_agent_command": send, "apply_area_settings": apply, "run_in_threadpool": asyncio.to_thread})
        self.assertEqual((await fn())["areas"][0]["display_name"], "測試")

    async def test_sensor_read_uses_only_device_sheet_off_loop(self):
        loop_thread = threading.get_ident()
        def load(tabs):
            self.assertEqual(tabs, ["智能居家"])
            self.assertNotEqual(threading.get_ident(), loop_thread)
        ctx = SimpleNamespace(load=load, get=lambda name: [{"狀態": "啟用", "類型": "感應器", "Device ID": "fake"}])
        fn = endpoint("lighting_api.py", "api_lighting_auto_sensors", {
            "RequestContext": lambda: ctx, "run_in_threadpool": asyncio.to_thread})
        self.assertEqual(len((await fn())["sensors"]), 1)

    async def test_light_level_cloud_fallback_runs_off_loop(self):
        loop_thread = threading.get_ident()
        def status(device_id):
            self.assertNotEqual(threading.get_ident(), loop_thread)
            return {"lightLevel": 7}
        fn = endpoint("lighting_api.py", "api_lighting_auto_sensor_light_level", {
            "lighting_auto": SimpleNamespace(get_cached_light_level=lambda n: None), "time": time,
            "switchbot_api": SimpleNamespace(get_device_status=status), "run_in_threadpool": asyncio.to_thread})
        self.assertEqual((await fn("fake"))["light_level"], 7)

    async def test_line_work_runs_off_loop_and_does_not_overlap(self):
        main_thread = threading.get_ident()
        calls = []
        active = 0
        peak = 0
        def handle(body, signature):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            calls.append((body, signature, threading.get_ident()))
            time.sleep(0.03)
            active -= 1
        fn = endpoint("main.py", "callback", {
            "webhook_handler": SimpleNamespace(handle=handle),
            "run_in_threadpool": asyncio.to_thread, "_line_callback_lock": asyncio.Lock(),
        })
        class Request:
            headers = {"X-Line-Signature": "fake-signature"}
            async def body(self): return b"fake-body"
        ticks = 0
        async def heartbeat():
            nonlocal ticks
            for _ in range(4):
                await asyncio.sleep(0.005)
                if active: ticks += 1
        responses = await asyncio.gather(fn(Request()), fn(Request()), heartbeat())
        self.assertEqual(responses[:2], ["OK", "OK"])
        self.assertEqual(peak, 1)
        self.assertTrue(all(tid != main_thread for _, _, tid in calls))
        self.assertGreater(ticks, 0)
        self.assertEqual(calls[0][:2], ("fake-body", "fake-signature"))


if __name__ == "__main__": unittest.main()
