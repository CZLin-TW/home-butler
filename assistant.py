"""
共用的「自然語言 → action 分派 → 回覆」核心。

LINE webhook（main.py）跟 REST 自然語言端點（web_api.py:/api/assistant，給
Siri 捷徑用）共用同一條 pipeline：兩邊都只負責拿到一句話，剩下交給
process_message——避免把這套 Claude 解析 + action 分派邏輯複製兩份。
"""

import json
import re
import traceback

from conversation import ask_claude, ask_claude_semantic
from handlers.food import handle_add, handle_delete, handle_modify, handle_query
from handlers.todo import handle_add_todo, handle_modify_todo, handle_delete_todo, handle_query_todo
from handlers.recurring_todo import (
    handle_add_recurring_todo, handle_modify_recurring_todo,
    handle_stop_recurring_todo, handle_query_recurring_todo,
)
from handlers.device import (
    handle_control_ac, handle_control_ir, handle_query_sensor,
    handle_query_devices, handle_control_dehumidifier, handle_query_dehumidifier,
    handle_set_dehumidifier_auto, handle_query_weather,
)
from handlers.schedule import handle_add_schedule, handle_modify_schedule, handle_delete_schedule, handle_query_schedule
from handlers.style import handle_set_style


# 把每個 action 統一成 (data, user_name, ctx) -> str 的簽名，
# 用 lambda adapter 吸收掉各 handler 真實簽名的差異。新增 action 時只要在這註冊即可。
# unclear 是「Claude 沒搞懂、要使用者再說」的特殊 action，不產生使用者可見的結果。
ACTION_HANDLERS = {
    "add_food":             lambda d, u, c: handle_add(d, u, c),
    "delete_food":          lambda d, u, c: handle_delete(d, c),
    "modify_food":          lambda d, u, c: handle_modify(d, c),
    "query_food":           lambda d, u, c: handle_query(c),
    "add_todo":             lambda d, u, c: handle_add_todo(d, u, c),
    "modify_todo":          lambda d, u, c: handle_modify_todo(d, u, c),
    "delete_todo":          lambda d, u, c: handle_delete_todo(d, c),
    "query_todo":           lambda d, u, c: handle_query_todo(u, c),
    "add_recurring_todo":   lambda d, u, c: handle_add_recurring_todo(d, u, c),
    "modify_recurring_todo": lambda d, u, c: handle_modify_recurring_todo(d, u, c),
    "stop_recurring_todo":  lambda d, u, c: handle_stop_recurring_todo(d, u, c),
    "query_recurring_todo": lambda d, u, c: handle_query_recurring_todo(c),
    "control_ac":           lambda d, u, c: handle_control_ac(d, c),
    "control_ir":           lambda d, u, c: handle_control_ir(d, c),
    "query_sensor":         lambda d, u, c: handle_query_sensor(d, c),
    "control_dehumidifier": lambda d, u, c: handle_control_dehumidifier(d, c),
    "query_dehumidifier":   lambda d, u, c: handle_query_dehumidifier(d, c),
    "set_dehumidifier_auto": lambda d, u, c: handle_set_dehumidifier_auto(d, c),
    "query_devices":        lambda d, u, c: handle_query_devices(c),
    "query_weather":        lambda d, u, c: handle_query_weather(d),
    "add_schedule":         lambda d, u, c: handle_add_schedule(d, u, c),
    "modify_schedule":      lambda d, u, c: handle_modify_schedule(d, u, c),
    "delete_schedule":      lambda d, u, c: handle_delete_schedule(d, c),
    "query_schedule":       lambda d, u, c: handle_query_schedule(c),
    "set_style":            lambda d, u, c: handle_set_style(d, u, c),
    "unclear":              lambda d, u, c: None,
}

# 三類 action 對應的後處理路徑：
# - SEMANTIC：把 raw 結果再丟回 Claude 包裝成自然句子（query_food 排序、query_todo 分組等）
# - REALTIME：直接回 raw 結果，避免 Claude 重新組句把即時資訊改寫掉
# 沒列在這兩組的 action 是純寫入，reply 走 Claude 第一輪生成的 claude_reply。
SEMANTIC_ACTIONS = {"query_weather", "query_sensor", "query_food", "query_todo"}
REALTIME_ACTIONS = {"query_devices", "query_dehumidifier", "set_dehumidifier_auto", "query_schedule", "query_recurring_todo"}


def process_message(user_id, text, user_name, ctx):
    """一句話 → Claude 解析 → 分派 actions → 組出使用者可見的回覆字串。

    純函式：不碰 LINE / HTTP，也不負責存對話歷史（由 caller 自行決定）。
    回傳值一定是非空字串，失敗時回 fallback 語句而非拋例外。
    """
    result = ask_claude(user_id, text, user_name, ctx)
    print(f"[3] result={repr(result)}")

    if not result or not result.strip():
        print("[WARN] Claude returned empty response")
        return "抱歉，我沒有理解您的意思，可以再說一次嗎？"

    try:
        parsed = json.loads(result)
    except json.JSONDecodeError as je:
        print(f"[WARN] JSON parse failed: {je}, raw: {repr(result)}")
        json_match = re.search(r'\{.*\}', result, re.DOTALL)
        if json_match:
            extracted = json_match.group()
            try:
                parsed = json.loads(extracted)
                print(f"[WARN] JSON extracted from partial response: {repr(extracted)}")
            except json.JSONDecodeError as je2:
                print(f"[WARN] regex fallback also failed: {je2}, extracted: {repr(extracted)}")
                return "抱歉，系統處理時發生了一點問題，請再試一次。"
        else:
            print(f"[WARN] no JSON object found in response, returning raw text to user")
            return result

    print(f"[4] parsed type={type(parsed)}, value={parsed}")

    if isinstance(parsed, list):
        actions = parsed
        claude_reply = ""
    else:
        actions = parsed.get("actions", [])
        claude_reply = parsed.get("reply", "")

    print(f"[5] actions={actions}, claude_reply={claude_reply}")

    results = []
    for data in actions:
        handler = ACTION_HANDLERS.get(data.get("action"))
        if handler is None:
            continue  # 未知 action：跳過，避免 Claude 偶爾捏造的 action 讓整個 request 壞掉
        action_result = handler(data, user_name, ctx)
        if action_result is not None:
            results.append(action_result)

    has_error = any("❌" in r for r in results if r)
    action_types = {d.get("action") for d in actions}
    has_realtime = bool(action_types & REALTIME_ACTIONS)
    has_semantic = bool(action_types & SEMANTIC_ACTIONS)

    if has_error:
        return "\n".join(results)
    if has_semantic and not has_realtime:
        raw_data = "\n".join(r for r in results if r and "❌" not in r)
        if raw_data:
            try:
                return ask_claude_semantic(text, raw_data, user_name, ctx, action_types)
            except Exception as e:
                print(f"[SEMANTIC CLAUDE ERROR] {e}")
                return raw_data
        return claude_reply or "\n".join(results)
    if has_realtime:
        return "\n".join(results)
    if claude_reply:
        return claude_reply
    return "\n".join(results)
