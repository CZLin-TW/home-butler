"""HA owns migrated ACs. Configuration, never link health, selects the driver.

No fallback to direct IR, no Sheets state writes, no legacy AC automations.
The HA snapshot is the integration's reported state, not physical IR readback.
"""
import asyncio
import json
import math
from datetime import datetime, timezone, timedelta

import os
from command_result import CommandResult

MODE_LABELS = {"cool": "冷氣", "heat": "暖氣", "dry": "除濕", "fan_only": "送風", "heat_cool": "自動", "auto": "自動"}
FAN_LABELS = {"auto": "自動", "low": "低", "medium": "中", "high": "高"}


def names():
    value = json.loads(os.environ.get("HOME_ASSISTANT_AC_NAMES", "[]"))
    if (not isinstance(value, list) or len(value) > 20
            or any(not isinstance(n, str) or not n.strip() or n != n.strip() for n in value)
            or len(set(value)) != len(value)):
        raise ValueError("Invalid HOME_ASSISTANT_AC_NAMES")
    return value


def managed(name):
    try:
        return name in names()
    except (ValueError, TypeError):
        # A configuration typo must not silently restore direct IR control.
        return True


def status(name):
    from home_assistant_api import link
    state = link.climate_state(name)
    available = bool(state and state["available"])
    certain = available and not state.get("uncertain")
    mode = state.get("hvac_mode") if certain else None
    return {
        "controlProvider": "home_assistant", "available": available,
        "stateSource": "ha_last_command", "stateUncertain": not certain,
        "lastPower": ("off" if mode == "off" else "on") if mode else "",
        "lastTemperature": state.get("temperature") if certain else None,
        "lastMode": MODE_LABELS.get(mode, ""),
        "lastFanSpeed": FAN_LABELS.get(state.get("fan_mode"), "") if certain else "",
        "lastUpdatedAt": datetime.fromtimestamp(state["source_updated_at"], timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S") if state and state.get("source_updated_at") else "",
        "temperatureStep": 1,
    }


def overlay_row(row):
    if row.get("類型") != "空調" or not managed(row.get("名稱", "")):
        return row
    state = status(row["名稱"])
    return {**row, **{sheet: state[key] for sheet, key in (
        ("最後電源", "lastPower"), ("最後溫度", "lastTemperature"),
        ("最後模式", "lastMode"), ("最後風速", "lastFanSpeed"),
        ("最後更新時間", "lastUpdatedAt"))}}


def control(data, ctx):
    from home_assistant_api import link
    name = data.get("device_name", "")
    rows = [r for r in ctx.get("智能居家") if r.get("名稱") == name and r.get("狀態") == "啟用"]
    if len(rows) != 1 or rows[0].get("類型") != "空調":
        return CommandResult.failed("找不到唯一啟用的空調")
    if data.get("antimold_final"):
        return CommandResult.failed("空調已移轉 HA，舊防黴排程不再執行")
    patch = {k: data[k] for k in ("power", "temperature", "mode", "fan_speed") if k in data and data[k] is not None}
    patch.setdefault("power", "on")
    if patch["power"] not in ("on", "off"):
        return CommandResult.failed("空調電源設定無效")
    if patch["power"] == "off":
        patch = {"power": "off"}
    else:
        if "temperature" in patch:
            try:
                value = float(patch["temperature"])
                if type(patch["temperature"]) is bool or not math.isfinite(value) or not 16 <= value <= 30:
                    raise ValueError()
                patch["temperature"] = math.floor(value + 0.5)
            except (ValueError, TypeError):
                return CommandResult.failed("空調溫度需介於 16 至 30 度")
        if "mode" in patch:
            if patch["mode"] not in ("cool", "heat", "dry", "fan", "auto"):
                return CommandResult.failed("空調模式無效")
            patch["mode"] = {"fan": "fan_only", "auto": "heat_cool"}.get(patch["mode"], patch["mode"])
        if "fan_speed" in patch and patch["fan_speed"] not in FAN_LABELS:
            return CommandResult.failed("空調風速無效")
    if link.loop is None:
        return CommandResult.failed("HA 空調連線尚未就緒")
    future = asyncio.run_coroutine_threadsafe(link.command(name, patch), link.loop)
    try:
        result = future.result(timeout=28)
    except Exception:
        future.cancel()
        return CommandResult.unknown("HA 指令結果未確認，請查看設備；不會自動重送")
    if result["status"] != "success":
        return CommandResult(result["status"], result["message"])
    state = status(name)
    ctx._ac_saved_state = state
    ctx._ac_state_saved = True  # API compatibility: confirmed HA state, not a Sheet write.
    rows[0].update(overlay_row(rows[0]))
    return CommandResult.success(f"{name} 指令已由 HA 執行")
