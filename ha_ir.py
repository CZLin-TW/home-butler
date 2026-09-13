"""Explicit IR migration; HA offline never restores direct SwitchBot commands."""
import asyncio
import json
import os
from command_result import CommandResult


def names():
    values = json.loads(os.environ.get("HOME_ASSISTANT_IR_NAMES", "[]"))
    if (not isinstance(values, list) or len(values) > 20
            or any(not isinstance(n, str) or not n.strip() or n != n.strip() for n in values)
            or len(set(values)) != len(values)):
        raise ValueError("Invalid HOME_ASSISTANT_IR_NAMES")
    return values


def managed(name):
    try:
        return name in names()
    except (ValueError, TypeError):
        return True


def control(device, button):
    from home_assistant_api import link
    if not isinstance(button, str) or not button or len(button) > 60:
        return CommandResult.failed("遙控按鈕無效")
    if link.loop is None:
        return CommandResult.failed("HA 電扇連線尚未就緒，未送出指令")
    future = asyncio.run_coroutine_threadsafe(link.ir_command(device["名稱"], button), link.loop)
    try:
        result = future.result(timeout=28)
    except Exception:
        future.cancel()
        return CommandResult.unknown("❌ HA 遙控指令結果未確認，不會自動重送")
    if result["status"] == "success":
        return CommandResult.success(f"✅ {device['名稱']}「{button}」已由 HA 送出")
    return CommandResult(result["status"], "❌ " + result["message"])
