"""Relay a fixed set of theater-agent calls; never a general HTTP proxy.

The theater agent is a separate local service (its own device loops, its own
Marantz telnet ownership). HomeButler cannot reach it from Render, so this
module forwards exactly the two calls the Dashboard already used through the
PC agent — nothing else. The URL and key are local configuration and never
travel over the link.

Two deliberate narrowings compared with a proxy:
  * the action names map to hard-coded method/path pairs, so a compromised or
    buggy backend cannot ask for an arbitrary URL on the local network;
  * flag writes accept only the three known boolean flags, so unknown keys are
    rejected here as well as at the backend.
"""
import asyncio
import json
import re
import time
from urllib.parse import urlsplit

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError

# action -> (method, path). Adding an entry is a deliberate capability change.
ACTIONS = {"summary": ("GET", "/summary"), "set_flags": ("POST", "/flags")}
FLAGS = {"kef_link", "tv_screen_auto", "tv_avr_sync"}
# Shorter than the backend's 25s wait so a slow theater agent surfaces as this
# relay's failure, with a reason, rather than as a blank timeout upstream.
REQUEST_TIMEOUT = 15


class LocalTheaterRelay:
    """Delegate to the selected local integration's switch controller.

    Resolve the entry on every call so reload, removal and address changes do
    not leave a second client with stale credentials. Never use the legacy
    HTTP relay as a fallback when the local integration is unavailable.
    """

    def __init__(self, hass, entry_id):
        self.hass, self.entry_id = hass, entry_id
        self.lock = asyncio.Lock()

    async def execute(self, frame):
        request_id = frame.get("request_id", "")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise ValueError("Invalid request ID")
        result = {"type": "theater_result", "request_id": request_id, "status": "failed"}
        if self.lock.locked():
            return {**result, "message": "劇院中繼忙碌中"}
        async with self.lock:
            if set(frame) != {"type", "request_id", "action", "payload", "expires_at"} or frame["type"] != "theater_command":
                return result
            expiry = frame["expires_at"]
            if type(expiry) not in (int, float) or not time.time() < expiry <= time.time() + 60:
                return {**result, "message": "指令已逾期"}
            action = frame["action"]
            if not isinstance(action, str) or action not in ACTIONS:
                return {**result, "message": "不支援的劇院動作"}
            if action == "set_flags":
                try:
                    validate_flags(frame["payload"])
                except ValueError:
                    return {**result, "message": "只接受三個已知布林功能開關"}
            elif frame["payload"] not in (None, {}):
                return result
            entry = self.hass.config_entries.async_get_entry(self.entry_id)
            if entry is None or entry.domain != "theater_agent" or entry.state != ConfigEntryState.LOADED:
                return {**result, "message": "HA Theater Agent 整合尚未載入"}
            coordinator = entry.runtime_data
            try:
                if action == "summary":
                    data = await coordinator.async_get_summary()
                else:
                    data = await coordinator.async_set_flags(frame["payload"], expires_at=expiry)
            except HomeAssistantError as exc:
                # Local integration exceptions contain safe, fixed messages.
                status = "unknown" if getattr(exc, "status", "failed") == "unknown" else "failed"
                return {**result, "status": status, "message": str(exc)}
            return {**result, "status": "success", "result": data}


def normalize_url(value):
    """Accept only a plain http(s) origin for a local address."""
    parts = urlsplit(str(value).strip())
    if (parts.scheme not in ("http", "https") or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in ("", "/")):
        raise ValueError("Use the theater agent origin, for example http://192.168.1.10:8080")
    return str(value).strip().rstrip("/")


def validate_flags(payload):
    if not isinstance(payload, dict) or not payload or set(payload) - FLAGS:
        raise ValueError("Unknown theater flag")
    if any(type(value) is not bool for value in payload.values()):
        raise ValueError("Theater flags are booleans")
    return payload


class TheaterRelay:
    """One in-flight relay call, independent of the climate command lane."""

    def __init__(self, session, url, key):
        self.session, self.url, self.key = session, normalize_url(url), key
        self.lock = asyncio.Lock()

    async def execute(self, frame):
        request_id = frame.get("request_id", "")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise ValueError("Invalid request ID")
        result = {"type": "theater_result", "request_id": request_id, "status": "failed"}
        # Reads are idempotent and writes are absolute flag values, but a second
        # concurrent call still gets refused rather than queued behind the first.
        if self.lock.locked():
            result["message"] = "劇院中繼忙碌中"
            return result
        async with self.lock:
            if set(frame) != {"type", "request_id", "action", "payload", "expires_at"} or frame["type"] != "theater_command":
                return result
            expiry = frame["expires_at"]
            if type(expiry) not in (int, float) or not time.time() < expiry <= time.time() + 60:
                result["message"] = "指令已逾期"
                return result
            action = frame["action"]
            if action not in ACTIONS:
                result["message"] = "不支援的劇院動作"
                return result
            method, path = ACTIONS[action]
            body = None
            if action == "set_flags":
                try:
                    body = validate_flags(frame["payload"])
                except ValueError as err:
                    result["message"] = str(err)
                    return result
            elif frame["payload"] not in (None, {}):
                return result
            try:
                async with self.session.request(
                        method, self.url + path, json=body,
                        headers={"x-api-key": self.key} if self.key else {},
                        timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                        allow_redirects=False) as response:
                    text = await response.text()
                    if response.status >= 300:
                        result["message"] = f"theater-agent HTTP {response.status}: {text[:200]}"
                        return result
                    await response.json()  # reject a non-JSON body before reporting success
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                # A write may or may not have landed; the backend must not replay it.
                result["status"] = "unknown" if method != "GET" else "failed"
                result["message"] = "劇院中繼未取得結果"
                return result
            result.update(status="success", result=json.loads(text))
            return result
