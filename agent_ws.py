"""WebSocket control channel for local PC agents.

Agents connect outbound from the home LAN to Render. The server keeps only
in-memory connection state for now; command delivery will be layered on top of
this registry after the connection path is proven stable.
"""

import asyncio
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from auth import verify_api_key
from config import HOME_BUTLER_API_KEY


router = APIRouter(prefix="/api")

_agents: dict[str, dict[str, Any]] = {}
_lock = asyncio.Lock()


def _public_agent(agent_id: str, info: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    last_seen = float(info.get("last_seen") or 0)
    return {
        "agent_id": agent_id,
        "online": bool(info.get("websocket")) and now - last_seen <= 45,
        "hostname": info.get("hostname", ""),
        "ip": info.get("ip", ""),
        "capabilities": info.get("capabilities", []),
        "connected_at": info.get("connected_at"),
        "last_seen": last_seen,
        "last_message": info.get("last_message", ""),
        "agent_sha": info.get("agent_sha", ""),
    }


async def _register_agent(agent_id: str, websocket: WebSocket, hello: dict[str, Any]) -> None:
    now = time.time()
    previous_ws = None
    async with _lock:
        previous = _agents.get(agent_id)
        previous_ws = previous.get("websocket") if previous else None
        _agents[agent_id] = {
            "websocket": websocket,
            "hostname": str(hello.get("hostname") or ""),
            "ip": str(hello.get("ip") or ""),
            "capabilities": hello.get("capabilities") if isinstance(hello.get("capabilities"), list) else [],
            "connected_at": now,
            "last_seen": now,
            "last_message": "hello",
            "agent_sha": str(hello.get("agent_sha") or ""),
        }
    if previous_ws is not None and previous_ws is not websocket:
        try:
            await previous_ws.close(code=1012)
        except Exception:
            pass


async def _mark_seen(agent_id: str, message_type: str) -> None:
    async with _lock:
        info = _agents.get(agent_id)
        if not info:
            return
        info["last_seen"] = time.time()
        info["last_message"] = message_type


async def _unregister_agent(agent_id: str, websocket: WebSocket) -> None:
    async with _lock:
        info = _agents.get(agent_id)
        if info and info.get("websocket") is websocket:
            info["websocket"] = None
            info["last_message"] = "disconnected"


@router.websocket("/agent/ws")
async def agent_websocket(websocket: WebSocket):
    await websocket.accept()
    agent_id = ""
    try:
        hello = await websocket.receive_json()
        if not isinstance(hello, dict) or hello.get("type") != "hello":
            await websocket.send_json({"type": "error", "error": "hello_required"})
            await websocket.close(code=1008)
            return

        token = str(hello.get("token") or "")
        if not HOME_BUTLER_API_KEY or not secrets.compare_digest(token, HOME_BUTLER_API_KEY):
            await websocket.send_json({"type": "error", "error": "unauthorized"})
            await websocket.close(code=1008)
            return

        agent_id = str(hello.get("agent_id") or hello.get("hostname") or "").strip()
        if not agent_id:
            await websocket.send_json({"type": "error", "error": "agent_id_required"})
            await websocket.close(code=1008)
            return

        await _register_agent(agent_id, websocket, hello)
        await websocket.send_json({
            "type": "hello_ack",
            "agent_id": agent_id,
            "server_time": time.time(),
        })

        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                continue
            message_type = str(message.get("type") or "unknown")
            await _mark_seen(agent_id, message_type)
            if message_type == "heartbeat":
                await websocket.send_json({
                    "type": "heartbeat_ack",
                    "server_time": time.time(),
                })
            elif message_type == "command_result":
                # Placeholder for the next phase. Keeping this accepted now
                # lets agents safely report unsupported future command trials.
                pass
            else:
                await websocket.send_json({
                    "type": "info",
                    "message": f"ignored message type: {message_type}",
                })
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "error": str(e)})
        except Exception:
            pass
    finally:
        if agent_id:
            await _unregister_agent(agent_id, websocket)


@router.get("/agent/status", dependencies=[Depends(verify_api_key)])
async def agent_status():
    now = time.time()
    async with _lock:
        agents = [_public_agent(agent_id, info, now) for agent_id, info in sorted(_agents.items())]
    return {"count": len(agents), "agents": agents}
