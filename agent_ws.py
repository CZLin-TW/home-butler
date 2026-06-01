"""WebSocket control channel for local PC agents.

Agents connect outbound from the home LAN to Render. The server keeps only
in-memory connection state for now; command delivery will be layered on top of
this registry after the connection path is proven stable.
"""

import asyncio
import secrets
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from auth import verify_api_key
from config import HOME_BUTLER_API_KEY


router = APIRouter(prefix="/api")

_agents: dict[str, dict[str, Any]] = {}
_pending_commands: dict[str, asyncio.Future] = {}
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
            "send_lock": asyncio.Lock(),
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


async def _resolve_command_result(message: dict[str, Any]) -> None:
    command_id = str(message.get("command_id") or "")
    if not command_id:
        return
    future = _pending_commands.get(command_id)
    if future and not future.done():
        future.set_result(message)


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
                await _resolve_command_result(message)
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


async def send_agent_command(
    command_type: str,
    payload: dict[str, Any] | None = None,
    *,
    agent_id: str = "",
    required_capability: str = "",
    timeout: float = 20.0,
) -> dict[str, Any]:
    now = time.time()
    selected_agent_id = ""
    selected_ws = None
    selected_send_lock = None

    async with _lock:
        candidates = []
        if agent_id:
            info = _agents.get(agent_id)
            if info:
                candidates.append((agent_id, info))
        else:
            candidates = sorted(_agents.items())

        for candidate_id, info in candidates:
            if not info.get("websocket"):
                continue
            if now - float(info.get("last_seen") or 0) > 45:
                continue
            capabilities = info.get("capabilities", [])
            if required_capability and required_capability not in capabilities:
                continue
            selected_agent_id = candidate_id
            selected_ws = info.get("websocket")
            selected_send_lock = info.get("send_lock")
            break

    if selected_ws is None:
        capability_text = f" with capability {required_capability}" if required_capability else ""
        raise RuntimeError(f"No online agent{capability_text}")

    command_id = uuid.uuid4().hex
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    _pending_commands[command_id] = future
    try:
        message = {
            "type": "command",
            "command_id": command_id,
            "command_type": command_type,
            "payload": payload or {},
            "server_time": time.time(),
        }
        if selected_send_lock is not None:
            async with selected_send_lock:
                await selected_ws.send_json(message)
        else:
            await selected_ws.send_json(message)
        result = await asyncio.wait_for(future, timeout=timeout)
        return {"agent_id": selected_agent_id, **result}
    except asyncio.TimeoutError as e:
        # asyncio.wait_for 逾時丟的 TimeoutError 不帶訊息，str(e) 會是空字串，
        # 上游 lighting_api 的 detail=str(e) 就變成 {"detail":""} 毫無線索。
        # 補上有意義訊息，並維持 TimeoutError 型別讓上游照舊對應成 504。
        raise TimeoutError(
            f"agent '{selected_agent_id}' did not respond to '{command_type}' within {timeout:.0f}s"
        ) from e
    finally:
        _pending_commands.pop(command_id, None)
