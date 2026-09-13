"""Dedicated HA link: observations and explicitly selected native AC control.

The HA key never grants owner, PC-agent or assistant access. AC commands originate
from authorized HB callers and target an independently configured name allowlist.
One authenticated HA connection owns the snapshot. Reconnects start unknown;
old sockets cannot overwrite or disconnect their replacement. No Sheets I/O.
"""
import asyncio
import json
import secrets
import re
import threading
import time
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

import config
from ha_hub_events import HubRelay
from auth import verify_api_key

PROTOCOL = 1
MAX_BYTES = 131072
STALE_SECONDS = 90


def configured_key():
    key = getattr(config, "HOME_ASSISTANT_API_KEY", "")
    others = [getattr(config, name, "") for name in (
        "HOME_BUTLER_API_KEY", "DEVICE_VOICE_API_KEY", "HOMEBRIDGE_API_KEY")]
    if len(key) < 32 or any(key == other for other in others if other):
        raise HTTPException(503, "Home Assistant access is not configured")
    return key


def verify_ha_key(x_api_key: str = Header(default="")):
    key = configured_key()
    if not x_api_key or not secrets.compare_digest(x_api_key.encode(), key.encode()):
        raise HTTPException(401, "Invalid or missing key")


router = APIRouter(prefix="/api/home-assistant")
owner_router = APIRouter(prefix="/api/home-assistant", dependencies=[Depends(verify_api_key)])


@router.get("/health", dependencies=[Depends(verify_ha_key)])
def health():
    return {"protocol": PROTOCOL, "capabilities": ["observations", "climate_control", "ir_control", "hub_updates"]}


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    entity_id: str = Field(pattern=r"^(binary_sensor|sensor)\.[a-z0-9_]+$", max_length=160)
    name: str = Field(min_length=1, max_length=160)
    area: str = Field(default="", max_length=100)
    kind: Literal["occupancy", "illuminance"]
    value: bool | float | None
    available: bool = Field(strict=True)
    source_updated_at: float | None = Field(default=None, ge=0, le=253402214400, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def check_value(cls, data):
        if not isinstance(data, dict):
            return data
        value, kind = data.get("value"), data.get("kind")
        entity_id = data.get("entity_id", "")
        if not isinstance(entity_id, str):
            raise ValueError("Invalid entity ID")
        if kind == "occupancy":
            if not entity_id.startswith("binary_sensor.") or (value is not None and type(value) is not bool):
                raise ValueError("Occupancy requires a boolean binary sensor")
        elif kind == "illuminance":
            if (not entity_id.startswith("sensor.") or
                    (value is not None and (type(value) not in (int, float) or not 0 <= value <= 1_000_000))):
                raise ValueError("Illuminance requires finite nonnegative lux")
        if data.get("available") is True and value is None:
            raise ValueError("Available observations require a value")
        if data.get("available") is False and value is not None:
            raise ValueError("Unavailable observations must not carry a value")
        return data


class ClimateState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    entity_id: str = Field(pattern=r"^climate\.[a-z0-9_]+$", max_length=160)
    name: str = Field(min_length=1, max_length=160)
    available: bool = Field(strict=True)
    hvac_mode: Literal["off", "cool", "heat", "dry", "fan_only", "auto", "heat_cool"] | None = None
    temperature: float | None = Field(default=None, ge=5, le=40, allow_inf_nan=False)
    fan_mode: str | None = Field(default=None, max_length=40)
    source_updated_at: float | None = Field(default=None, ge=0, le=253402214400, allow_inf_nan=False)


class ClimateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["climate_result"]
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    status: Literal["success", "failed", "unknown"]
    state: ClimateState | None = None


class IRButtonState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    entity_id: str = Field(pattern=r"^button\.[a-z0-9_]+$", max_length=160)
    name: str = Field(min_length=1, max_length=160)
    button: str = Field(min_length=1, max_length=60)
    available: bool = Field(strict=True)


class IRResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ir_result"]
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    status: Literal["success", "failed", "unknown"]


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["snapshot"]
    sequence: int = Field(strict=True, ge=1, le=2**53 - 1)
    observations: list[Observation] = Field(max_length=128)
    climates: list[ClimateState] = Field(default_factory=list, max_length=20)
    ir_buttons: list[IRButtonState] = Field(default_factory=list, max_length=120)

    hub_devices: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_entities(self):
        if len(set(self.hub_devices)) != len(self.hub_devices) or any(
                not re.fullmatch(r"[0-9A-F]{12}", device) for device in self.hub_devices):
            raise ValueError("Invalid Hub 2 subscriptions")
        for key in ("id", "entity_id"):
            values = [getattr(item, key) for item in self.observations]
            if len(values) != len(set(values)):
                raise ValueError("Duplicate observations")
        for key in ("id", "entity_id", "name"):
            values = [getattr(item, key) for item in self.climates]
            if len(values) != len(set(values)):
                raise ValueError("Duplicate climates")
        for key in ("id", "entity_id"):
            values = [getattr(item, key) for item in self.ir_buttons]
            if len(set(values)) != len(values):
                raise ValueError("Duplicate IR buttons")
        if len({(s.name, s.button) for s in self.ir_buttons}) != len(self.ir_buttons):
            raise ValueError("Ambiguous IR buttons")
        return self


class LinkState:
    """Server receipt age is separate from sensor measurement age."""
    def __init__(self):
        self.lock = threading.RLock()
        self.socket = None
        self.sequence = 0
        self.received_at = None
        self.received_mono = None
        self.observations = []
        self.climates = []
        self.loop = None
        self.pending = {}
        self.climate_capable = False
        self.ir_capable = False
        self.ir_buttons = []
        self.uncertain = set()
        self.hub_updates = HubRelay(self)

    def connect(self, socket, climate_capable=False, ir_capable=False, hub_capable=False):
        with self.lock:
            self._fail_pending()
            previous = self.socket
            self.socket, self.sequence = socket, 0
            self.received_at = self.received_mono = None
            self.climate_capable = climate_capable
            self.ir_capable = ir_capable
            self.hub_updates.reset(hub_capable)
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                self.loop = None
            # Retain names for the unavailable UI, never their live values.
            return previous

    def disconnect(self, socket):
        with self.lock:
            if self.socket is socket:
                self.socket = None
                self.hub_updates.reset(False)
                self._fail_pending()

    def _fail_pending(self):
        for _, _, future in self.pending.values():
            if not future.done():
                future.set_result({"status": "unknown", "message": "HA 連線中斷，指令結果未確認；不會自動重送"})
        self.pending.clear()

    def receive(self, socket, snapshot):
        with self.lock:
            if self.socket is not socket or snapshot.sequence <= self.sequence:
                return False
            self.sequence = snapshot.sequence
            self.hub_updates.select(snapshot.hub_devices)
            self.received_at, self.received_mono = time.time(), time.monotonic()
            self.observations = [item.model_dump() for item in snapshot.observations]
            previous = {s["id"]: s for s in self.climates}
            fresh = []
            for item in snapshot.climates:
                state = item.model_dump()
                old = previous.get(item.id)
                # A poll sampled before command completion cannot overwrite the
                # newer state included with that command's acknowledgement.
                if old and state["available"] and old.get("source_updated_at", 0) and (
                        (state.get("source_updated_at") or 0) < old["source_updated_at"]):
                    state = old
                if old and state.get("source_updated_at") != old.get("source_updated_at") and not any(
                        pending[1]["name"] == item.name for pending in self.pending.values()):
                    self.uncertain.discard(item.name)
                fresh.append(state)
            self.climates = fresh
            self.ir_buttons = [s.model_dump() for s in snapshot.ir_buttons]
            return True

    def climate_state(self, name):
        with self.lock:
            found = [s for s in self.climates if s["name"] == name]
            if len(found) != 1:
                return None
            state = dict(found[0])
            state["available"] = bool(self.snapshot()["online"] and self.climate_capable
                                      and state["available"]
                                      and state["hvac_mode"] is not None)
            state["uncertain"] = name in self.uncertain
            return state

    async def command(self, name, patch):
        import ha_climate
        with self.lock:
            # Repeat validation at dispatch, never execute an offline queue later.
            try:
                allowed = name in ha_climate.names()
                configured_key()
            except (ValueError, TypeError, HTTPException):
                allowed = False
            state = self.climate_state(name)
            if not allowed or not state or not self.snapshot()["online"] or not self.climate_capable:
                return {"status": "failed", "message": "HA 空調未就緒，未送出指令"}
            if not any(s["id"] == state["id"] and s["available"] for s in self.climates):
                return {"status": "failed", "message": "HA 空調目前無法使用"}
            if self.pending:
                return {"status": "failed", "message": "HA 正在處理空調指令，請稍後操作"}
            socket = self.socket
            request_id = uuid4().hex
            future = asyncio.get_running_loop().create_future()
            self.pending[request_id] = (socket, state, future)
            self.uncertain.add(name)
        try:
            await socket.send_json({"type": "climate_command", "request_id": request_id,
                                    "id": state["id"], "name": name, "patch": patch,
                                    "expires_at": time.time() + 15})
            return await asyncio.wait_for(future, 25)
        except (Exception, asyncio.CancelledError):
            return {"status": "unknown", "message": "HA 指令結果未確認；不會自動重送"}
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    async def ir_command(self, name, button):
        import ha_ir
        with self.lock:
            try:
                allowed = name in ha_ir.names()
                configured_key()
            except (ValueError, TypeError, HTTPException):
                allowed = False
            matches = [s for s in self.ir_buttons if s["name"] == name and s["button"] == button and s["available"]]
            if not allowed or not self.ir_capable or not self.snapshot()["online"] or len(matches) != 1:
                return {"status": "failed", "message": "HA 遙控按鈕未就緒，未送出指令"}
            if self.pending:
                return {"status": "failed", "message": "HA 指令處理中，請稍後操作"}
            socket, state = self.socket, matches[0]
            request_id = uuid4().hex
            future = asyncio.get_running_loop().create_future()
            self.pending[request_id] = (socket, state, future)
        try:
            await socket.send_json({"type": "ir_command", "request_id": request_id, "id": state["id"],
                                    "name": name, "button": button, "expires_at": time.time() + 15})
            return await asyncio.wait_for(future, 25)
        except (Exception, asyncio.CancelledError):
            return {"status": "unknown", "message": "HA 遙控指令結果未確認，不會自動重送"}
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def ir_result(self, socket, result):
        with self.lock:
            pending = self.pending.get(result.request_id)
            if socket is not self.socket or not pending or pending[0] is not socket or "button" not in pending[1]:
                return
            future = pending[2]
            if not future.done():
                future.set_result({"status": result.status, "message": {
                    "success": "HA 已送出遙控指令", "failed": "HA 拒絕遙控指令，未送出",
                    "unknown": "HA 遙控指令結果未確認，不會自動重送"}[result.status]})

    def command_result(self, socket, result):
        with self.lock:
            pending = self.pending.get(result.request_id)
            if socket is not self.socket or not pending or pending[0] is not socket or "button" in pending[1]:
                return
            _, original, future = pending
            if future.done():
                return
            state = result.state
            success = (result.status == "success" and state is not None and state.available and state.hvac_mode is not None
                       and state.id == original["id"] and state.name == original["name"])
            if success:
                self.climates = [state.model_dump() if s["id"] == state.id else s for s in self.climates]
                self.uncertain.discard(state.name)
            elif result.status == "failed" and not original.get("uncertain"):
                self.uncertain.discard(original["name"])  # HA rejected before any side effect.
            status = "success" if success else "failed" if result.status == "failed" else "unknown"
            future.set_result({"status": status, "message": {
                "success": "HA 已執行", "failed": "HA 拒絕空調指令，請確認模式與設備設定",
                "unknown": "HA 指令結果未確認；不會自動重送"}[status]})

    def snapshot(self):
        with self.lock:
            try:
                configured_key()
                configured = True
            except HTTPException:
                configured = False
            age = max(0, time.monotonic() - self.received_mono) if self.received_mono is not None else None
            online = bool(configured and self.socket is not None and age is not None and age <= STALE_SECONDS)
            return {"configured": configured, "connected": self.socket is not None, "online": online,
                    "received_at": self.received_at, "age_seconds": age, "stale_after_seconds": STALE_SECONDS,
                    "observations": [{**item, "available": online and item["available"],
                                      "value": item["value"] if online and item["available"] else None}
                                     for item in self.observations]}


link = LinkState()


async def receive_frame(websocket, timeout):
    raw = await asyncio.wait_for(websocket.receive_text(), timeout)
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Oversized frame")
    frame = json.loads(raw)
    if not isinstance(frame, dict):
        raise ValueError("Invalid frame")
    return frame


@router.websocket("/ws")
async def ha_websocket(websocket: WebSocket):
    await websocket.accept()
    registered = False
    try:
        hello = await receive_frame(websocket, 10)
        token = hello.get("token")
        if (hello.get("type") != "hello" or hello.get("protocol") != PROTOCOL
                or not isinstance(token, str) or len(token) > 512):
            raise ValueError("Invalid hello")
        verify_ha_key(token)
        previous = link.connect(websocket, hello.get("climate_control") is True, hello.get("ir_control") is True, hello.get("hub_updates") is True)
        registered = True
        if previous is not None and previous is not websocket:
            try:
                await previous.close(code=1012)
            except (RuntimeError, WebSocketDisconnect):
                pass  # The previous connection may already have closed.
        await websocket.send_json({"type": "hello_ack", "protocol": PROTOCOL,
                                   "capabilities": ["observations", "climate_control", "ir_control", "hub_updates"]})
        while True:
            frame = await receive_frame(websocket, STALE_SECONDS)
            # Recheck revoked keys on every application frame.
            verify_ha_key(token)
            if frame.get("type") == "climate_result" and link.climate_capable:
                link.command_result(websocket, ClimateResult.model_validate(frame))
                continue
            if frame.get("type") == "ir_result" and link.ir_capable:
                link.ir_result(websocket, IRResult.model_validate(frame))
                continue
            snapshot = Snapshot.model_validate(frame)
            accepted = link.receive(websocket, snapshot)
            await websocket.send_json({"type": "snapshot_ack", "sequence": snapshot.sequence,
                                       "accepted": accepted})
    except WebSocketDisconnect:
        pass
    except (HTTPException, ValueError, ValidationError, asyncio.TimeoutError):
        await websocket.close(code=1008)
    except Exception:
        # No frames, credentials or raw exception text in logs/responses.
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        if registered:
            link.disconnect(websocket)


@owner_router.get("/observations")
def read_observations():
    return link.snapshot()
