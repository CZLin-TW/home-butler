from fastapi import FastAPI, Request, HTTPException
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import json
import re
import httpx
import traceback
import threading

from config import line_bot_api, webhook_handler, claude, LINE_CHANNEL_ACCESS_TOKEN, now_taipei
from sheets import RequestContext, get_sheet
from prompt import get_user_name, get_style_instruction
from conversation import save_conversation, cleanup_conversation, ask_claude
from handlers.food import handle_add, handle_delete, handle_modify, handle_query
from handlers.todo import handle_add_todo, handle_modify_todo, handle_delete_todo, handle_query_todo
from handlers.device import (
    handle_control_ac, handle_control_ir, handle_query_sensor,
    handle_query_devices, handle_control_dehumidifier, handle_query_dehumidifier, handle_query_weather,
)
from handlers.schedule import handle_add_schedule, handle_delete_schedule, handle_query_schedule
from handlers.style import handle_set_style
from notify import router as notify_router
import switchbot_api

app = FastAPI()
app.include_router(notify_router)

# Web Dashboard REST API
from web_api import router as web_api_router
app.include_router(web_api_router)


# ════════════════════════════════════════════
# HTTP 端點
# ════════════════════════════════════════════

@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"status": "ok"}


@app.get("/switchbot/devices")
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


@app.get("/switchbot/test/{device_id}/{button_name}")
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


@app.get("/switchbot/test_turnon/{device_id}")
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
                    try:
                        parsed = json.loads(json_match.group())
                        print(f"[WARN] JSON extracted from partial response")
                    except json.JSONDecodeError:
                        parsed = None
                        reply = "抱歉，系統處理時發生了一點問題，請再試一次。"
                else:
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
                    action = data.get("action")
                    if action == "add_food":
                        results.append(handle_add(data, user_name, ctx))
                    elif action == "delete_food":
                        results.append(handle_delete(data, ctx))
                    elif action == "modify_food":
                        results.append(handle_modify(data, ctx))
                    elif action == "query_food":
                        results.append(handle_query(ctx))
                    elif action == "add_todo":
                        results.append(handle_add_todo(data, user_name, ctx))
                    elif action == "modify_todo":
                        results.append(handle_modify_todo(data, user_name, ctx))
                    elif action == "delete_todo":
                        results.append(handle_delete_todo(data, ctx))
                    elif action == "query_todo":
                        results.append(handle_query_todo(user_name, ctx))
                    elif action == "control_ac":
                        results.append(handle_control_ac(data, ctx))
                    elif action == "control_ir":
                        results.append(handle_control_ir(data, ctx))
                    elif action == "query_sensor":
                        results.append(handle_query_sensor(data, ctx))
                    elif action == "control_dehumidifier":
                        results.append(handle_control_dehumidifier(data, ctx))
                    elif action == "query_dehumidifier":
                        results.append(handle_query_dehumidifier(data, ctx))
                    elif action == "query_devices":
                        results.append(handle_query_devices(ctx))
                    elif action == "query_weather":
                        results.append(handle_query_weather(data))
                    elif action == "add_schedule":
                        results.append(handle_add_schedule(data, user_name, ctx))
                    elif action == "delete_schedule":
                        results.append(handle_delete_schedule(data, ctx))
                    elif action == "query_schedule":
                        results.append(handle_query_schedule(ctx))
                    elif action == "set_style":
                        results.append(handle_set_style(data, user_name, ctx))
                    elif action == "unclear":
                        pass

                has_error = any("❌" in r for r in results if r)
                raw_actions = {"query_devices", "query_dehumidifier", "query_schedule"}
                has_realtime = any(d.get("action") in raw_actions for d in actions)
                semantic_actions = {"query_weather", "query_sensor", "query_food", "query_todo"}
                has_semantic = any(d.get("action") in semantic_actions for d in actions)

                if has_error:
                    reply = "\n".join(results)
                elif has_semantic and not has_realtime:
                    raw_data = "\n".join(r for r in results if r and "❌" not in r)
                    if raw_data:
                        # 風格注入（統一由 get_style_instruction 處理，已含預設/自訂邏輯）
                        style_block = get_style_instruction(user_name, ctx)
                        action_types = {d.get("action") for d in actions}
                        if action_types & {"query_todo"}:
                            semantic_system = f"你負責管理家庭的食品庫存、待辦事項和智能居家設備。今天是 {now_taipei().strftime('%Y-%m-%d')}。根據以下待辦事項數據回覆。依日期分組，格式如下：\n2026-03-18（三）\nemoji 事項1\nemoji 事項2（HH:MM）\n\n日期標題：若該日期與今天在同一週（週一到週日），請在日期後加上中文星期，格式為「YYYY-MM-DD（一/二/三/四/五/六/日）」；不同週則只顯示日期。不要用 markdown 標題、粗體或分隔線。有時間的事項在後面括號註明時間。只在今天或過期的事項補一句簡短提醒，其餘不加評語。最後可用一句話總結。" + style_block
                            semantic_max_tokens = 500
                        elif action_types & {"query_food"}:
                            semantic_system = f"你負責管理家庭的食品庫存、待辦事項和智能居家設備。今天是 {now_taipei().strftime('%Y-%m-%d')}。根據以下庫存數據回覆。依過期日由近到遠排序，每項一行，格式為「emoji 品名 數量單位（過期日）」。不要用 markdown 標題或分隔線。只在快過期（3天內）或已過期的品項後面補簡短提醒，其餘不加評語。" + style_block
                            semantic_max_tokens = 500
                        else:
                            semantic_system = "你負責管理家庭的食品庫存、待辦事項和智能居家設備。根據以下數據，用自然、簡潔的語氣回覆使用者的問題。不要重複列出所有數據，挑重點回答。如果使用者問的是「冷嗎」「會下雨嗎」「濕度高嗎」這類問題，直接回答並給建議。" + style_block
                            semantic_max_tokens = 300
                        try:
                            semantic_reply = claude.messages.create(
                                model="claude-sonnet-4-6",
                                max_tokens=semantic_max_tokens,
                                system=semantic_system,
                                messages=[
                                    {"role": "user", "content": f"使用者問：{text}\n\n數據：\n{raw_data}"}
                                ]
                            )
                            reply = semantic_reply.content[0].text.strip()
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
