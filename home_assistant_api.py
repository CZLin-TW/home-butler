"""Dedicated outbound HA link, phase 1: selected observations only.

The HA key never grants owner, PC-agent, assistant or device-control access.
One authenticated HA connection owns the snapshot. Reconnects start unknown;
old sockets cannot overwrite or disconnect their replacement. No Sheets I/O.
"""
import asyncio
import json
import secrets
import threading
import time
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

import config
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
    return {"protocol": PROTOCOL, "capabilities": ["observations"]}


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    entity_id: str = Field(pattern=r"^(binary_sensor|sensor)\.[a-z0-9_]+$", max_length=160)
    name: str = Field(min_length=1, max_length=160)
    area: str = Field(default="", max_length=100)
    kind: Literal["occupancy", "illuminance"]
    value: bool | float | None
    available: bool = Field(strict=True)
    source_updated_at: float | None = Field(default=None, ge=0, allow_inf_nan=False)

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


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["snapshot"]
    sequence: int = Field(strict=True, ge=1, le=2**53 - 1)
    observations: list[Observation] = Field(max_length=128)

    @model_validator(mode="after")
    def unique_entities(self):
        for key in ("id", "entity_id"):
            values = [getattr(item, key) for item in self.observations]
            if len(values) != len(set(values)):
                raise ValueError("Duplicate observations")
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

    def connect(self, socket):
        with self.lock:
            previous = self.socket
            self.socket, self.sequence = socket, 0
            self.received_at = self.received_mono = None
            # Retain names for the unavailable UI, never their live values.
            return previous

    def disconnect(self, socket):
        with self.lock:
            if self.socket is socket:
                self.socket = None

    def receive(self, socket, snapshot):
        with self.lock:
            if self.socket is not socket or snapshot.sequence <= self.sequence:
                return False
            self.sequence = snapshot.sequence
            self.received_at, self.received_mono = time.time(), time.monotonic()
            self.observations = [item.model_dump() for item in snapshot.observations]
            return True

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
        previous = link.connect(websocket)
        registered = True
        if previous is not None and previous is not websocket:
            try:
                await previous.close(code=1012)
            except (RuntimeError, WebSocketDisconnect):
                pass  # The previous connection may already have closed.
        await websocket.send_json({"type": "hello_ack", "protocol": PROTOCOL,
                                   "capabilities": ["observations"]})
        while True:
            frame = await receive_frame(websocket, STALE_SECONDS)
            # Recheck revoked keys on every application frame.
            verify_ha_key(token)
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
