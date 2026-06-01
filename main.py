from fastapi import FastAPI, Request, HTTPException, Depends
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import httpx
import traceback
import threading

from config import line_bot_api, webhook_handler, LINE_CHANNEL_ACCESS_TOKEN
from sheets import RequestContext, get_sheet
from prompt import get_user_name
from conversation import save_conversation, cleanup_conversation
from assistant import process_message
from notify import router as notify_router
from auth import verify_api_key
import switchbot_api
import panasonic_api
import lg_api


app = FastAPI()
app.include_router(notify_router)

# Web Dashboard REST API
from web_api import router as web_api_router
app.include_router(web_api_router)

# Local PC agent realtime channel
from agent_ws import router as agent_ws_router
app.include_router(agent_ws_router)


# 啟動時把 PC 監控歷史 + 感測器歷史 + 空調狀態歷史從 Sheet 撈回 in-memory ring
# buffer（解 Render free instance 重啟資料遺失）+ spawn polling thread。
@app.on_event("startup")
def _on_startup():
    import threading
    import time as _time
    import pc_state
    import sensor_state
    import ac_history
    import dehumidifier_auto
    import dehumidifier_history
    import dehumidifier_driver
    from sheets import RequestContext
    import switchbot_api
    from handlers.device import apply_sensor_compensation

    pc_state.backfill_from_sheet()
    sensor_state.backfill_from_sheet()
    ac_history.backfill_from_sheet()
    dehumidifier_history.backfill_from_sheet()
    dehumidifier_auto.load_rules()

    def _polling_loop():
        """每 5 分鐘掃一次「智能居家」分頁：
        - 感應器：打 SwitchBot API 拉當下溫濕度，寫進 sensor_state
        - 空調：snapshot「最後電源/溫度/模式/風速」進 ac_history
          （AC 是 IR write-only 不能 readback，只能用 home-butler 自己記的最後狀態）
        - 除濕機（手動模式）：打 API 拉電源狀態進 dehumidifier_history，給感測器圖
          背景斜紋用；自動模式的由下方 evaluate_all 記，這裡跳過避免重複
        """
        while True:
            try:
                ctx = RequestContext()
                ctx.load()
                for d in ctx.get("智能居家"):
                    if d.get("狀態") != "啟用":
                        continue
                    name = d.get("名稱", "")
                    location = d.get("位置", "")
                    if not name:
                        continue
                    dtype = d.get("類型")
                    if dtype == "感應器":
                        device_id = d.get("Device ID", "")
                        if not device_id:
                            continue
                        result = switchbot_api.get_hub_sensor(device_id)
                        if "error" in result:
                            print(f"[sensor poll] {name}: {result.get('error')}")
                            continue
                        temp = result.get("temperature")
                        humidity = result.get("humidity")
                        co2 = result.get("co2")
                        temp, humidity = apply_sensor_compensation(temp, humidity, d)
                        sensor_state.record(name, location, temp, humidity, co2)
                    elif dtype == "空調":
                        power = str(d.get("最後電源", "")).strip()
                        if not power:
                            continue  # 從未操作過、skip 不 record
                        ac_history.record(
                            name, location, power,
                            d.get("最後溫度"), d.get("最後模式"), d.get("最後風速"),
                        )
                    elif dtype == "除濕機":
                        # 自動模式的除濕機由下方 evaluate_all 抓狀態 + record，
                        # 這裡只補「手動模式」的，避免對同一台重複打 API / 重複記錄。
                        if dehumidifier_auto.is_locked(name):
                            continue
                        driver = dehumidifier_driver.make_driver(d)
                        if driver is None:
                            continue
                        status = driver.get_status()
                        if not isinstance(status, dict) or "error" in status:
                            err = status.get("error") if isinstance(status, dict) else status
                            print(f"[dehum poll] {name}: {err}")
                            continue
                        dehumidifier_history.record(name, location, driver.is_power_on(status))
                # 除濕機自動規則：先 sensor poll 跑完寫進 snapshot 再評估
                dehumidifier_auto.evaluate_all(ctx, sensor_state.snapshot())
            except Exception as e:
                print(f"[poll] tick error: {e}")
            _time.sleep(300)

    threading.Thread(target=_polling_loop, daemon=True).start()
    print("[startup] polling thread started (sensor + ac + dehumidifier auto)")


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


@app.get("/switchbot/devices/{device_id}/raw_status", dependencies=[Depends(verify_api_key)])
def get_switchbot_raw_status(device_id: str):
    """Debug: 回 SwitchBot Cloud API 對該裝置原始 status body。
    用來看新感測器（Meter Pro CO2 等）實際回什麼欄位，決定後續解析邏輯。"""
    return switchbot_api.get_device_status(device_id)


@app.get("/panasonic/devices", dependencies=[Depends(verify_api_key)])
def list_panasonic_devices():
    """Debug: 列出 Panasonic Smart App 帳號下所有設備的原始 GwList。
    新增除濕機時用來抓 GWID（Device ID）與 Auth 填進「智能居家」分頁。
    Panasonic 各機型欄位名稱可能不同，故回傳整包原始 entry 讓你直接看。"""
    gw_list = panasonic_api.get_devices()
    return {"count": len(gw_list), "devices": gw_list}


@app.get("/lg/probe", dependencies=[Depends(verify_api_key)])
def probe_lg_regions():
    """Debug: 三個區域 endpoint 都試打 /devices，找出帳號對應區域。
    哪區回 200 就把它的 base 填到環境變數 LG_API_BASE。"""
    return lg_api.probe_regions()


@app.get("/lg/devices", dependencies=[Depends(verify_api_key)])
def list_lg_devices():
    """Debug: 列出 LG ThinQ 帳號下所有裝置。
    新增 LG 除濕機時用來抓 deviceId 填進「智能居家」分頁的 Device ID（品牌欄填 LG）。"""
    return {"devices": lg_api.get_devices()}


@app.get("/lg/devices/{device_id}/profile", dependencies=[Depends(verify_api_key)])
def get_lg_device_profile(device_id: str):
    """Debug: 某 LG 裝置的能力 profile，用來校準 lg_api.py 的除濕機 property 欄位名/值。"""
    return lg_api.get_device_profile(device_id)


@app.get("/lg/devices/{device_id}/state", dependencies=[Depends(verify_api_key)])
def get_lg_device_state(device_id: str):
    """Debug: 某 LG 裝置目前狀態（巢狀 property 結構），對照 profile 校準解析。"""
    return lg_api.get_device_state(device_id)


@app.get("/panasonic/dehumidifier/{device_name}/full_status", dependencies=[Depends(verify_api_key)])
def get_panasonic_dehumidifier_full_status(device_name: str):
    """Debug: 掃除濕機 CommandType 0x00 ~ 0x1F 全部欄位。
    用來找未知欄位（風量、風向、定時器等）對應哪個 CommandType——baseline
    一次、改設定一次、diff 兩次結果。"""
    from sheets import RequestContext
    ctx = RequestContext()
    ctx.load()
    auth = ""
    gwid = ""
    for d in ctx.get("智能居家"):
        if (d.get("狀態") == "啟用"
                and d.get("名稱") == device_name
                and d.get("類型") == "除濕機"):
            auth = d.get("Auth", "")
            gwid = d.get("Device ID", "")
            break
    if not auth or not gwid:
        return {"error": f"找不到除濕機 {device_name}（檢查「智能居家」分頁名稱、類型、Auth、Device ID）"}
    return panasonic_api.get_dehumidifier_full_status(auth, gwid)


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
        reply = process_message(user_id, text, user_name, ctx)
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
