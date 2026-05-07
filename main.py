from fastapi import FastAPI, Request, HTTPException, Depends
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import json
import re
import httpx
import traceback
import threading

from config import line_bot_api, webhook_handler, LINE_CHANNEL_ACCESS_TOKEN
from sheets import RequestContext, get_sheet
from prompt import get_user_name
from conversation import save_conversation, cleanup_conversation, ask_claude, ask_claude_semantic
from handlers.food import handle_add, handle_delete, handle_modify, handle_query
from handlers.todo import handle_add_todo, handle_modify_todo, handle_delete_todo, handle_query_todo
from handlers.device import (
    handle_control_ac, handle_control_ir, handle_query_sensor,
    handle_query_devices, handle_control_dehumidifier, handle_query_dehumidifier, handle_query_weather,
)
from handlers.schedule import handle_add_schedule, handle_modify_schedule, handle_delete_schedule, handle_query_schedule
from handlers.style import handle_set_style
from notify import router as notify_router
from auth import verify_api_key
import switchbot_api


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
    "control_ac":           lambda d, u, c: handle_control_ac(d, c),
    "control_ir":           lambda d, u, c: handle_control_ir(d, c),
    "query_sensor":         lambda d, u, c: handle_query_sensor(d, c),
    "control_dehumidifier": lambda d, u, c: handle_control_dehumidifier(d, c),
    "query_dehumidifier":   lambda d, u, c: handle_query_dehumidifier(d, c),
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
REALTIME_ACTIONS = {"query_devices", "query_dehumidifier", "query_schedule"}

app = FastAPI()
app.include_router(notify_router)

# Web Dashboard REST API
from web_api import router as web_api_router
app.include_router(web_api_router)


# 啟動時把 PC 監控歷史從 Sheet 撈回 in-memory ring buffer（解 Render free instance
# 重啟資料遺失問題）。同步跑——backfill 速度由 Sheet read 決定，2880 row 等級幾秒內。
@app.on_event("startup")
def _on_startup():
    import pc_state
    pc_state.backfill_from_sheet()


# ════════════════════════════════════════════
# HTTP 端點
# ════════════════════════════════════════════

@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"status": "ok"}


@app.get("/switchbot/devices", dependencies=[Depends(verify_api_key)])
def list_switchbot_devices():
    result = switchbot_api.get_devices()
    if "error" in result:
        return {"status": "error", "message": result["error"]}
    devices = []
    for d in result.get("physical", []):
        devices.append({
            "名稱": d.get("deviceName", ""),
            "類型": d.get("deviceType", ""),
            "Device ID": d.get("deviceId", ""),
            "分類": "物理設備",
        })
    for d in result.get("infrared", []):
        devices.append({
            "名稱": d.get("deviceName", ""),
            "類型": d.get("remoteType", ""),
            "Device ID": d.get("deviceId", ""),
            "Hub ID": d.get("hubDeviceId", ""),
            "分類": "紅外線虛擬設備（IR）",
        })
    return {"status": "ok", "設備數量": len(devices), "設備列表": devices}


@app.get("/switchbot/test/{device_id}/{button_name}", dependencies=[Depends(verify_api_key)])
def test_switchbot_command(device_id: str, button_name: str):
    print(f"[TEST] device_id={device_id}, button={button_name}")
    result = switchbot_api.send_command(device_id, button_name, "default", "customize")
    print(f"[TEST] customize result: {result}")
    return {
        "status": "ok" if result.get("success") else "error",
        "device_id": device_id,
        "button": button_name,
        "command_type": "customize",
        "result": result
    }


@app.get("/switchbot/test_turnon/{device_id}", dependencies=[Depends(verify_api_key)])
def test_switchbot_turnon(device_id: str):
    result = switchbot_api.send_command(device_id, "turnOn", "default", "command")
    print(f"[TEST] turnOn result: {result}")
    return {"status": "ok" if result.get("success") else "error", "result": result}


# ════════════════════════════════════════════
# LINE Webhook
# ════════════════════════════════════════════

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    try:
        webhook_handler.handle(body.decode(), signature)
    except Exception as e:
        # 把完整 traceback 印出來，不然 FastAPI 只顯示 "400 Bad Request"、
        # reply_message 之類底層失敗的原因會整個消失
        print(f"[CALLBACK ERROR] {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=str(e))
    return "OK"


@webhook_handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text
    reply = "抱歉，發生未知錯誤。"

    try:
        print(f"[1] user_id={user_id}, text={text}")

        # 廣播功能
        if text.strip().startswith("@all"):
            broadcast_msg = text.strip()[4:].strip()
            if broadcast_msg:
                members_sheet = get_sheet("家庭成員")
                members = members_sheet.get_all_records()
                sender_name = user_id
                for m in members:
                    if m.get("Line User ID") == user_id and m.get("狀態") == "啟用":
                        sender_name = m.get("名稱", user_id)
                        break
                push_text = f"📢 {sender_name}：{broadcast_msg}"
                for member in members:
                    if member.get("狀態") == "啟用":
                        mid = member.get("Line User ID")
                        if mid:
                            line_bot_api.push_message(mid, TextSendMessage(text=push_text))
                            save_conversation(mid, "assistant", push_text)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已廣播給全體成員"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請在 @all 後面輸入廣播內容"))
            return

        # 查看自訂風格（不經 Claude，直接讀 Sheet 原值）
        if text.strip() in ["查看風格", "我的風格", "目前風格"]:
            ctx = RequestContext()
            ctx.load()
            user_name = get_user_name(user_id, ctx)
            style_text = ""
            for row in ctx.get("家庭成員"):
                if row.get("名稱") == user_name and row.get("狀態") == "啟用":
                    style_text = str(row.get("管家風格", "")).strip()
                    break
            if style_text:
                reply = f"📝 您目前的自訂風格：\n{style_text}"
            else:
                reply = "📝 您目前沒有自訂風格，使用預設管家風格。"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        try:
            loading_resp = httpx.post(
                "https://api.line.me/v2/bot/chat/loading/start",
                headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"},
                json={"chatId": user_id, "loadingSeconds": 60}
            )
            print(f"[LOADING] status={loading_resp.status_code}, body={loading_resp.text}")
        except Exception as e:
            print(f"[LOADING ERROR] {e}")

        ctx = RequestContext()
        ctx.load()

        user_name = get_user_name(user_id, ctx)
        print(f"[2] user_name={user_name}")
        result = ask_claude(user_id, text, user_name, ctx)
        print(f"[3] result={repr(result)}")

        if not result or not result.strip():
            print("[WARN] Claude returned empty response")
            reply = "抱歉，我沒有理解您的意思，可以再說一次嗎？"
        else:
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
                        parsed = None
                        reply = "抱歉，系統處理時發生了一點問題，請再試一次。"
                else:
                    print(f"[WARN] no JSON object found in response, returning raw text to user")
                    reply = result
                    parsed = None

            if parsed is not None:
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
                    result = handler(data, user_name, ctx)
                    if result is not None:
                        results.append(result)

                has_error = any("❌" in r for r in results if r)
                action_types = {d.get("action") for d in actions}
                has_realtime = bool(action_types & REALTIME_ACTIONS)
                has_semantic = bool(action_types & SEMANTIC_ACTIONS)

                if has_error:
                    reply = "\n".join(results)
                elif has_semantic and not has_realtime:
                    raw_data = "\n".join(r for r in results if r and "❌" not in r)
                    if raw_data:
                        try:
                            reply = ask_claude_semantic(text, raw_data, user_name, ctx, action_types)
                        except Exception as e:
                            print(f"[SEMANTIC CLAUDE ERROR] {e}")
                            reply = raw_data
                    else:
                        reply = claude_reply or "\n".join(results)
                elif has_realtime:
                    reply = "\n".join(results)
                elif claude_reply:
                    reply = claude_reply
                else:
                    reply = "\n".join(results)

        print(f"[6] reply={reply}")

    except Exception as e:
        print(f"[ERROR] {traceback.format_exc()}")
        reply = "抱歉，系統暫時出了點問題，請稍後再試。"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

    def _save():
        try:
            save_conversation(user_id, "user", text)
            save_conversation(user_id, "assistant", reply)
            cleanup_conversation(user_id)
        except Exception as e:
            print(f"[SAVE ERROR] {e}")
    threading.Thread(target=_save, daemon=True).start()
