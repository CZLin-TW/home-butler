"""The one live connection from Home Assistant, and what it last said.

Carried over from the old system on purpose:
- One connection owns the state. A newer one replaces it; an old socket can neither overwrite the
  state nor resolve a command sent on the new one. A reconnect starts unknown.
- State older than STALE_SECONDS is not shown as current: online turns false and values read as unknown.
- One command at a time on the device lane (air conditioners, IR buttons, Hue); the theater relay has
  its own lane, so a page load asking the theater for its status never makes an AC command "busy".
- A command is checked again when it is sent, never queued for later, and an unanswered or interrupted
  one is reported "unknown" and never resent.
- A periodic snapshot sampled before a command finished cannot overwrite the newer state that came
  back with that command's result. An AC whose last command is unconfirmed is "uncertain" until HA
  reports a newer state.
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from uuid import uuid4

from homebutler.integrations.ha.messages import ClimateResult, HueResult, IRResult, Snapshot, TheaterResult

STALE_SECONDS = 90
HUE_ACTIONS = {"hue.list_areas", "hue.set_state", "hue.recall_scene", "hue.set_effect", "hue.notify", "hue.breathe"}


@dataclass
class Pending:
    socket: object
    kind: str
    target: dict
    future: asyncio.Future


@dataclass
class Link:
    clock: callable = time.monotonic
    wall: callable = time.time
    lock: threading.RLock = field(default_factory=threading.RLock)
    socket: object = None
    capabilities: frozenset = frozenset()
    sequence: int = 0
    received_at: float | None = None
    received_mono: float | None = None
    observations: list = field(default_factory=list)
    climates: list = field(default_factory=list)
    ir_buttons: list = field(default_factory=list)
    environment: list = field(default_factory=list)
    automations: dict | None = None
    uncertain: set = field(default_factory=set)
    device_lane: dict = field(default_factory=dict)
    theater_lane: dict = field(default_factory=dict)
    listeners: list = field(default_factory=list)       # called (outside the lock) after each accepted snapshot

    # ── connection ──────────────────────────────────────────────────────────

    def connect(self, socket, capabilities) -> object:
        with self.lock:
            self._fail_pending()
            previous, self.socket = self.socket, socket
            self.capabilities = frozenset(capabilities)
            self.sequence, self.received_at, self.received_mono = 0, None, None
            return previous

    def disconnect(self, socket) -> None:
        with self.lock:
            if self.socket is socket:
                self.socket = None
                self.capabilities = frozenset()
                self._fail_pending()

    def _fail_pending(self):
        for lane, message in ((self.device_lane, "HA 連線中斷，指令結果未確認；不會自動重送"),
                              (self.theater_lane, "HA 連線中斷，劇院指令結果未確認")):
            for pending in lane.values():
                if not pending.future.done():
                    pending.future.set_result({"status": "unknown", "message": message})
            lane.clear()

    def online(self) -> bool:
        with self.lock:
            return (self.socket is not None and self.received_mono is not None
                    and self.clock() - self.received_mono <= STALE_SECONDS)

    def can(self, capability: str) -> bool:
        with self.lock:
            return self.online() and capability in self.capabilities

    # ── snapshots ───────────────────────────────────────────────────────────

    def receive(self, socket, snapshot: Snapshot) -> bool:
        with self.lock:
            if self.socket is not socket or snapshot.sequence <= self.sequence:
                return False
            self.sequence = snapshot.sequence
            self.received_at, self.received_mono = self.wall(), self.clock()
            self.observations = [i.model_dump() for i in snapshot.observations]
            previous = {c["id"]: c for c in self.climates}
            busy = {p.target.get("id") for p in self.device_lane.values() if p.kind == "climate"}
            fresh = []
            for item in snapshot.climates:
                state, old = item.model_dump(), previous.get(item.id)
                if old and state["available"] and (old.get("source_updated_at") or 0) > (state.get("source_updated_at") or 0):
                    state = old        # sampled before the command that produced the newer state
                if old and state.get("source_updated_at") != old.get("source_updated_at") and item.id not in busy:
                    self.uncertain.discard(item.id)
                fresh.append(state)
            self.climates = fresh
            self.ir_buttons = [i.model_dump() for i in snapshot.ir_buttons]
            self.environment = [i.model_dump() for i in snapshot.environment]
            # Every snapshot carries the whole report; one without it means sharing was turned off.
            self.automations = snapshot.automations.model_dump() if snapshot.automations else None
            listeners = list(self.listeners)
        for listener in listeners:
            listener(self)
        return True

    def state(self) -> dict:
        """What is known now; values read as unknown while offline."""
        with self.lock:
            online = self.online()
            age = self.clock() - self.received_mono if self.received_mono is not None else None

            def live(items):
                return [{**i, "available": online and i["available"],
                         "value": i.get("value") if online and i["available"] else None} for i in items]
            return {"connected": self.socket is not None, "online": online, "received_at": self.received_at,
                    "age_seconds": age, "stale_after_seconds": STALE_SECONDS,
                    "capabilities": sorted(self.capabilities) if online else [],
                    "observations": live(self.observations), "environment": live(self.environment),
                    "climates": [{**c, "available": online and "climate_control" in self.capabilities and c["available"]
                                  and c["hvac_mode"] is not None, "uncertain": c["id"] in self.uncertain}
                                 for c in self.climates],
                    "ir_buttons": [{**b, "available": online and "ir_control" in self.capabilities and b["available"]}
                                   for b in self.ir_buttons]}

    def climate(self, device_id: str) -> dict | None:
        found = [c for c in self.state()["climates"] if c["id"] == device_id]
        return found[0] if len(found) == 1 else None

    def automation_log(self) -> dict:
        with self.lock:
            online, report = self.online(), self.automations
            return {"online": online, "shared": report is not None, "received_at": self.received_at,
                    "rules": [{**r, "enabled": r["enabled"] if online else None} for r in (report or {}).get("rules", [])],
                    "runs": list((report or {}).get("runs", []))}

    # ── commands ────────────────────────────────────────────────────────────

    def _register(self, lane: dict, kind: str, target: dict) -> tuple[str, Pending]:
        """Call with the lock held, right after the checks, so no second command slips in between."""
        request_id = uuid4().hex
        pending = Pending(self.socket, kind, target, asyncio.get_running_loop().create_future())
        lane[request_id] = pending
        if kind == "climate":
            self.uncertain.add(target["id"])
        return request_id, pending

    async def _dispatch(self, lane: dict, request_id: str, pending: Pending, frame: dict, expires: float, wait: float, unknown: str):
        try:
            await pending.socket.send_json({**frame, "request_id": request_id, "expires_at": self.wall() + expires})
            return await asyncio.wait_for(pending.future, wait)
        except (Exception, asyncio.CancelledError):
            return {"status": "unknown", "message": unknown}
        finally:
            with self.lock:
                lane.pop(request_id, None)

    async def climate_command(self, device_id: str, patch: dict) -> dict:
        with self.lock:
            state = self.climate(device_id)
            if not self.can("climate_control") or state is None or not state["available"]:
                return {"status": "failed", "message": "HA 空調未就緒，未送出指令"}
            if self.device_lane:
                return {"status": "failed", "message": "HA 正在處理空調指令，請稍後操作"}
            request_id, pending = self._register(self.device_lane, "climate", state)
        return await self._dispatch(self.device_lane, request_id, pending,
                                    {"type": "climate_command", "id": state["id"], "name": state["name"], "patch": patch},
                                    15, 25, "HA 指令結果未確認；不會自動重送")

    async def ir_command(self, button_id: str) -> dict:
        with self.lock:
            found = [b for b in self.state()["ir_buttons"] if b["id"] == button_id and b["available"]]
            if not self.can("ir_control") or len(found) != 1:
                return {"status": "failed", "message": "HA 遙控按鈕未就緒，未送出指令"}
            if self.device_lane:
                return {"status": "failed", "message": "HA 指令處理中，請稍後操作"}
            button = found[0]
            request_id, pending = self._register(self.device_lane, "ir", button)
        return await self._dispatch(self.device_lane, request_id, pending,
                                    {"type": "ir_command", "id": button["id"], "name": button["name"], "button": button["button"]},
                                    15, 25, "HA 遙控指令結果未確認，不會自動重送")

    async def hue_command(self, action: str, payload: dict) -> dict:
        with self.lock:
            if action not in HUE_ACTIONS or not isinstance(payload, dict) or not self.can("hue_control") or self.device_lane:
                return {"status": "failed", "message": "HA 照明未就緒或指令處理中，未送出"}
            request_id, pending = self._register(self.device_lane, "hue", {})
        return await self._dispatch(self.device_lane, request_id, pending, {"type": "hue_command", "action": action, "payload": payload},
                                    15, 25, "HA 照明結果未確認，可能已部分執行；不會自動重送")

    async def theater_command(self, action: str, payload: dict | None = None) -> dict:
        """One theater call through HA. Never queued, never replayed."""
        with self.lock:
            if not self.can("theater_relay"):
                return {"status": "failed", "message": "HA 劇院中繼未就緒，未送出指令"}
            if self.theater_lane:
                return {"status": "failed", "message": "HA 正在處理劇院指令，請稍後再試"}
            request_id, pending = self._register(self.theater_lane, "theater", {})
        return await self._dispatch(self.theater_lane, request_id, pending,
                                    {"type": "theater_command", "action": action, "payload": payload or {}},
                                    20, 30, "HA 劇院指令結果未確認；不會自動重送")

    # ── results ─────────────────────────────────────────────────────────────

    def _pending(self, lane: dict, socket, request_id: str, kind: str) -> Pending | None:
        pending = lane.get(request_id)
        if socket is not self.socket or pending is None or pending.socket is not socket or pending.kind != kind or pending.future.done():
            return None
        return pending

    def climate_result(self, socket, result: ClimateResult) -> None:
        with self.lock:
            pending = self._pending(self.device_lane, socket, result.request_id, "climate")
            if pending is None:
                return
            state, original = result.state, pending.target
            success = (result.status == "success" and state is not None and state.available and state.hvac_mode is not None
                       and state.id == original["id"])
            if success:
                self.climates = [state.model_dump() if c["id"] == state.id else c for c in self.climates]
                self.uncertain.discard(state.id)
            elif result.status == "failed" and not original.get("uncertain"):
                self.uncertain.discard(original["id"])      # refused before any effect
            status = "success" if success else "failed" if result.status == "failed" else "unknown"
            pending.future.set_result({"status": status, "message": {
                "success": "HA 已執行", "failed": "HA 拒絕空調指令，請確認模式與設備設定",
                "unknown": "HA 指令結果未確認；不會自動重送"}[status]})

    def ir_result(self, socket, result: IRResult) -> None:
        with self.lock:
            pending = self._pending(self.device_lane, socket, result.request_id, "ir")
            if pending is not None:
                pending.future.set_result({"status": result.status, "message": {
                    "success": "HA 已送出遙控指令", "failed": "HA 拒絕遙控指令，未送出",
                    "unknown": "HA 遙控指令結果未確認，不會自動重送"}[result.status]})

    def hue_result(self, socket, result: HueResult) -> None:
        with self.lock:
            pending = self._pending(self.device_lane, socket, result.request_id, "hue")
            if pending is not None:
                pending.future.set_result({"status": result.status, "result": result.result})

    def theater_result(self, socket, result: TheaterResult) -> None:
        with self.lock:
            pending = self._pending(self.theater_lane, socket, result.request_id, "theater")
            if pending is not None:
                pending.future.set_result({"status": result.status, "result": result.result, "message": result.message})
