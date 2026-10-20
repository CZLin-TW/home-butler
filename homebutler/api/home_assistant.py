"""Where Home Assistant connects: one WebSocket, authenticated with HA's own key (checked again on every
frame, so revoking the key cuts the connection). Nothing about frames, keys or errors is logged."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from homebutler.domain import tokens
from homebutler.integrations.ha.messages import (CAPABILITIES, MAX_BYTES, PROTOCOLS, ClimateResult, HueResult, IRResult,
                                                 Snapshot, TheaterResult)
from homebutler.integrations.ha.link import STALE_SECONDS

router = APIRouter()
RESULTS = {"climate_result": ("climate_control", ClimateResult, "climate_result"),
           "ir_result": ("ir_control", IRResult, "ir_result"),
           "hue_result": ("hue_control", HueResult, "hue_result"),
           "theater_result": ("theater_relay", TheaterResult, "theater_result")}


class Refused(Exception):
    pass


async def _frame(websocket: WebSocket, timeout: float) -> dict:
    raw = await asyncio.wait_for(websocket.receive_text(), timeout)
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise Refused()
    frame = json.loads(raw)
    if not isinstance(frame, dict):
        raise Refused()
    return frame


def _key_ok(store, token) -> bool:
    if not isinstance(token, str) or len(token) > 512:
        return False
    with store.transaction() as session:
        return tokens.verify(session, token, "home_assistant") is not None


@router.websocket("/v2/home-assistant/ws")
async def home_assistant(websocket: WebSocket):
    store, link = websocket.app.state.store, websocket.app.state.ha
    await websocket.accept()
    registered = False
    try:
        hello = await _frame(websocket, 10)
        token = hello.get("token")
        if hello.get("type") != "hello" or hello.get("protocol") not in PROTOCOLS or not _key_ok(store, token):
            raise Refused()
        offered = {c for c in CAPABILITIES if hello.get(c) is True} | {"observations", "environment", "automation_log"}
        previous = link.connect(websocket, offered)
        registered = True
        if previous is not None and previous is not websocket:
            try:
                await previous.close(code=1012)
            except (RuntimeError, WebSocketDisconnect):
                pass
        await websocket.send_json({"type": "hello_ack", "protocol": hello["protocol"], "capabilities": CAPABILITIES})
        while True:
            frame = await _frame(websocket, STALE_SECONDS)
            if not _key_ok(store, token):
                raise Refused()
            kind = frame.get("type")
            if kind in RESULTS:
                capability, model, handler = RESULTS[kind]
                if capability in link.capabilities:
                    getattr(link, handler)(websocket, model.model_validate(frame))
                continue
            snapshot = Snapshot.model_validate(frame)
            accepted = link.receive(websocket, snapshot)
            await websocket.send_json({"type": "snapshot_ack", "sequence": snapshot.sequence, "accepted": accepted})
    except WebSocketDisconnect:
        pass
    except (Refused, ValueError, ValidationError, asyncio.TimeoutError):
        await _close(websocket, 1008)
    except Exception:
        await _close(websocket, 1011)
    finally:
        if registered:
            link.disconnect(websocket)


async def _close(websocket, code):
    try:
        await websocket.close(code=code)
    except Exception:
        pass
