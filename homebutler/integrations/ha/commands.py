"""Home Assistant commands for code that runs in a job thread.

The link lives on the server's event loop; this hands each command to that loop and waits for its
answer. No loop yet (startup), or no answer in time: the result is "failed" (nothing was sent) or
"unknown" (it may have happened), never retried here.
"""
from __future__ import annotations

import asyncio
import concurrent.futures

WAIT_SECONDS = 40


class Commands:
    def __init__(self, link, loop):
        """`loop` returns the server's event loop, or None before startup."""
        self.link, self.loop = link, loop

    def _run(self, make) -> dict:
        loop = self.loop()
        if loop is None or loop.is_closed():
            return {"status": "failed", "message": "服務尚未就緒，未送出指令"}
        future = asyncio.run_coroutine_threadsafe(make(), loop)
        try:
            return future.result(WAIT_SECONDS)
        except concurrent.futures.TimeoutError:
            return {"status": "unknown", "message": "HA 指令結果未確認；不會自動重送"}

    def climate(self, ha_id: str, patch: dict) -> dict:
        return self._run(lambda: self.link.climate_command(ha_id, patch))

    def ir(self, ha_id: str) -> dict:
        return self._run(lambda: self.link.ir_command(ha_id))

    def theater(self, action: str, payload: dict | None = None) -> dict:
        return self._run(lambda: self.link.theater_command(action, payload or {}))

    def breathe(self, target: str) -> None:
        """The Lights port: raises unless HA confirmed it."""
        result = self._run(lambda: self.link.hue_command("hue.breathe", {"resource_id": target, "resource_type": "grouped_light"}))
        if result.get("status") != "success":
            raise RuntimeError("light reminder not confirmed")
