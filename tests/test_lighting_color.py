"""Real request validation and forwarding without startup, secrets or hardware."""
import ast
from pathlib import Path
from typing import Optional
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, ValidationError, model_validator
from test_callback_concurrency import endpoint

tree = ast.parse(Path("lighting_api.py").read_text(encoding="utf-8"))
node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HueAreaStateRequest")
env = dict(globals())
exec(compile(ast.Module(body=[node], type_ignores=[]), "lighting_api.py", "exec"), env)
Request = env["HueAreaStateRequest"]


class LightingColorTests(unittest.IsolatedAsyncioTestCase):
    def test_schema_rejects_invalid_or_conflicting_color(self):
        for body in ({"hs_color": [True, 30]}, {"hs_color": [400, 30]}, {"hs_color": [30, 101]},
                     {"hs_color": [30]}, {"hs_color": [float("nan"), 30]}, {"hs_color": [30, 30], "color_temp_kelvin": 3000},
                     {"color_temp_kelvin": 3000.5}, {"hs_color": [30, 30], "url": "not-allowed"}):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                Request(**body)

    async def test_route_forwards_color_once_and_legacy_fails_closed(self):
        send = AsyncMock(return_value={"status": "ok", "agent_id": "home_assistant", "result": {}})
        fn = endpoint("lighting_api.py", "api_set_lighting_area_state", {
            "HTTPException": HTTPException, "send_agent_command": send,
            "_agent_error": lambda status, error: HTTPException(status, str(error)),
        })
        with patch("lighting_transport.ha_enabled", return_value=True):
            await fn("living", Request(hs_color=[280, 40]))
            self.assertEqual(send.await_args.args[1], {"area_id": "living", "on": None,
                "brightness": None, "resource_type": "grouped_light", "hs_color": [280, 40]})
            send.return_value = {"status": "unknown", "error": "unknown"}
            with self.assertRaises(HTTPException):
                await fn("living", Request(color_temp_kelvin=3000))
            self.assertEqual(send.await_count, 2)
        with patch("lighting_transport.ha_enabled", return_value=False):
            with self.assertRaises(HTTPException) as error:
                await fn("living", Request(hs_color=[30, 40]))
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(send.await_count, 2)
