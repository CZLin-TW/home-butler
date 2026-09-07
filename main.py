from job_runner import jobs
from fastapi import FastAPI, Request, HTTPException, Depends, Body
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from gspread.exceptions import GSpreadException
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import asyncio
import httpx
import os
import re
import traceback
import threading

from config import line_bot_api, webhook_handler, LINE_CHANNEL_ACCESS_TOKEN
from sheets import RequestContext, get_sheet, ensure_columns, is_transient_error
import device_auth
from prompt import get_user_name
from conversation import save_conversation, cleanup_conversation
from assistant import process_message
from notify import router as notify_router
from auth import verify_api_key
import switchbot_api
import panasonic_api
import lg_api
import aqara_api


app = FastAPI()
app.include_router(notify_router)

# Web Dashboard REST API
from web_api import router as web_api_router
app.include_router(web_api_router)

# A separate router prevents the restricted key from entering the owner API.
from device_voice_api import router as device_voice_router
app.include_router(device_voice_router)

from homebridge_api import router as homebridge_router
app.include_router(homebridge_router)

# Local PC agent realtime channel
from agent_ws import router as agent_ws_router
app.include_router(agent_ws_router)

# Lighting control via local PC agent
from lighting_api import router as lighting_api_router
app.include_router(lighting_api_router)

# Theater control via local PC agent (relay to theater-agent on the same PC)
from theater_api import router as theater_api_router
app.include_router(theater_api_router)


# Google Sheets 撐過重試仍失敗時（Google 端整段掛掉／配額燒完），回 503 而不是讓
# 例外冒成 500 + 一整頁 ASGI traceback。語意也比較準：資料源暫時不可用、等會兒再來，
# Dashboard 可以據此顯示「暫時讀不到」並沿用上一份快取，而不是當成程式壞掉。
@app.exception_handler(GSpreadException)
async def _sheets_unavailable_handler(request: Request, exc: GSpreadException):
    transient = is_transient_error(exc)
    print(f"[SHEETS UNAVAILABLE] {request.url.path}: {exc}")
    return JSONResponse(
        status_code=503 if transient else 500,
        content={
            "error": "sheets_unavailable" if transient else "sheets_error",
            "detail": ("Google 試算表暫時無法存取（Google 端服務異常），請稍後再試"
                       if transient else f"Google 試算表存取失敗：{exc}"),
        },
    )


# 照明自動化的 Hue 指令從 sync thread（polling / webhook 衍生 thread）發出，
# 需要 FastAPI 的 running loop 才能 run_coroutine_threadsafe 到 agent WS。
# 這個 handler 要註冊在 _on_startup 之前，確保 polling thread 起跑前 loop 已就緒。
@app.on_event("startup")
async def _capture_event_loop():
    import lighting_auto
    import health_alert
    loop = asyncio.get_running_loop()
    lighting_auto.set_event_loop(loop)
    # health_alert 的劇院存活檢查同樣要從 polling thread 打 agent WS，共用同一顆 loop
    health_alert.set_event_loop(loop)


# startup 只負責 spawn polling thread，**不做任何會打 Sheets 的事**——那些全在
# polling thread 的 _warm_up()（含把歷史從 Sheet 撈回 ring buffer，解 Render free
# instance 重啟資料遺失）。理由見 _warm_up 的 docstring：startup 沒返回前 uvicorn
# 不服務任何請求，暖機擺這裡等於每次部署都製造一段全站 5xx 的空窗。
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
    import device_status
    import lighting_auto
    import notify
    from sheets import RequestContext
    import switchbot_api
    from handlers.device import apply_sensor_compensation

    def _warm_up():
        """所有會打 Google Sheets 的暖機工作，由 polling thread 起跑時在背景執行。

        **刻意不放在 startup handler 裡。** FastAPI 的 sync startup handler 是直接
        `handler()` 呼叫（不丟 threadpool），而 uvicorn 在 ASGI lifespan startup 完成前
        不會服務任何請求——四張歷史表整張 get_all_records() 在冷啟動的 free instance 上
        要好幾秒到數十秒，那整段期間所有請求都回 5xx。

        實際咬過：一次部署重啟就讓 Dashboard 的裝置配對登入整段失敗——輪詢
        /api/auth/device/status 拿到 5xx，前端只會空轉；空窗若撐過 CODE_TTL（5 分）
        那組配對碼還會直接過期。

        搬到這裡之後 startup 幾乎立刻返回、服務馬上可用，暖機在背景補。代價只有部署後
        前幾秒 Dashboard 的歷史圖是空的——登入、LINE bot、設備控制都不碰這些 ring buffer。

        每一步各自 try/except：暖機失敗不能讓 polling thread 起不來（**原本放在 startup
        時任一個 backfill 拋例外會讓整個 app 起不來**）。backfill 本來每個 tick 就會重試，
        load_rules 失敗則下一次 CRUD 會重讀。
        """
        steps = (
            ("pc_state backfill", pc_state.backfill_from_sheet),
            ("sensor_state backfill", sensor_state.backfill_from_sheet),
            ("ac_history backfill", ac_history.backfill_from_sheet),
            ("dehumidifier_history backfill", dehumidifier_history.backfill_from_sheet),
            ("dehumidifier_auto rules", dehumidifier_auto.load_rules),
            ("lighting_auto rules", lighting_auto.load_rules),
            # 防黴送風的欄位：最後開機時間（算運轉時長）+ 逐台覆寫的門檻/送風分鐘。
            # 「濕度控制規則」在感應器那列，給除濕機自動模式的自訂分時目標濕度用
            # （格式 7=55, 23=60；見 dehumidifier_auto 模組 docstring）。
            # 缺就補在表尾，不動既有欄位；失敗不擋（防黴會自動退化成不觸發）。
            ("ensure 防黴欄位", lambda: ensure_columns(
                get_sheet("智能居家"),
                ["最後開機時間", "防黴運轉門檻分鐘", "防黴送風分鐘", "濕度控制規則"])),
            # 失聯告警的收件人開關欄（health_alert.ALERT_COLUMN）。補不出來不會讓告警
            # 靜音，只是無法縮小收件範圍（沒人勾 → 發給全部啟用成員）。
            ("ensure 系統告警欄位", lambda: ensure_columns(
                get_sheet("家庭成員"), ["系統告警"])),
        )
        for label, fn in steps:
            try:
                fn()
            except Exception as e:
                print(f"[warmup] {label} failed: {e}")
        print("[warmup] done")

    # SwitchBot webhook 註冊（Hub 2 lightLevel → 自動夜燈秒級評估）。
    # Render 自帶 RENDER_EXTERNAL_URL；其他環境可用 PUBLIC_BASE_URL 覆寫。
    # 沒設就跳過——自動夜燈仍可運作，只是退化成 5min 輪詢的反應速度。
    public_base = (os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip()
    if public_base:
        def _register_webhook():
            url = public_base.rstrip("/") + "/switchbot/webhook"
            result = switchbot_api.ensure_webhook(url)
            print(f"[light-auto] webhook register {url}: {result}")
        threading.Thread(target=_register_webhook, daemon=True).start()
    else:
        print("[light-auto] PUBLIC_BASE_URL / RENDER_EXTERNAL_URL 未設定，跳過 webhook 註冊"
              "（自動夜燈退化為 5min 輪詢反應）")

    sensor_ready = False

    def _sensor_tick():
        nonlocal sensor_ready
        if not sensor_ready:
            _warm_up()
            sensor_ready = True
        try:
            # 若 startup backfill 曾失敗（cold start / gspread 5xx / quota），這裡每個
            # tick 補做一次：成功的內部 early-return no-op，失敗的下個 tick 再試，
            # 避免一次暫時性失敗就永久放棄 cold-start 還原。
            pc_state.backfill_from_sheet()
            sensor_state.backfill_from_sheet()
            ac_history.backfill_from_sheet()
            dehumidifier_history.backfill_from_sheet()
            ctx = RequestContext()
            ctx.load()
            device_status.load_catalog(ctx.get("智能居家"))
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
                    device_status.update(name, {
                        "temperature": temp,
                        "humidity": humidity,
                    })
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
                    device_status.update(name, driver.status_fields(status))
                    dehumidifier_history.record(name, location, driver.is_power_on(status))
            # 除濕機自動規則：先 sensor poll 跑完寫進 snapshot 再評估
            dehumidifier_auto.evaluate_all(ctx, sensor_state.snapshot())
        except Exception as e:
            print(f"[poll] tick error: {e}")
            raise

    def _with_context(callback, sheets=None):
        ctx = RequestContext()
        ctx.load(sheets)
        callback(ctx)

    jobs.add("sensors", 300, _sensor_tick)
    jobs.add("lighting", 300, lighting_auto.tick)
    jobs.add("schedules", 60, lambda: _with_context(notify.run_schedule_tick, ["智能居家", "排程指令"]))
    jobs.add("notion", 300, lambda: _with_context(notify.sync_external_events, ["家庭成員"]))
    jobs.add("todo-reminders", 300, lambda: _with_context(notify.run_todo_tick))
    jobs.add("daily-push", 300, lambda: _with_context(notify.run_daily_push_if_due))
    jobs.add("agent-health", 300, lambda: _with_context(notify.health_alert.run_checks, ["家庭成員"]))
    jobs.start()
    print("[startup] independent periodic jobs started")


@app.on_event("shutdown")
def _stop_jobs():
    jobs.stop_event.set()


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


# ── Aqara Cloud（Presence Sensor FP2）：授權與探索 ──
# 授權是一次性人工流程（probe 找機房 → 要授權碼 → 換權杖），之後權杖存在「系統狀態」
# 分頁、到期自動 refresh。完整步驟見 Readme「Aqara FP2」一節。

@app.get("/aqara/probe", dependencies=[Depends(verify_api_key)])
def probe_aqara_regions():
    """Debug: 六個 Aqara 機房各打一次（不帶權杖），找出帳號/App 憑證屬於哪一區。
    回權杖相關錯誤的那一區就是你的機房，把區碼填進環境變數 AQARA_REGION。
    刻意不用 getAuthCode 探測——那個會真的寄六封授權信。"""
    return aqara_api.probe_regions()


@app.get("/aqara/token", dependencies=[Depends(verify_api_key)])
def get_aqara_token_status():
    """Debug: 目前的授權狀態（權杖遮蔽過，只露頭尾）。"""
    return aqara_api.token_status()


@app.post("/aqara/auth/code", dependencies=[Depends(verify_api_key)])
def request_aqara_auth_code(account: str = ""):
    """授權第一步：請 Aqara 寄授權碼到帳號（Email / 簡訊）。
    account 不帶就用環境變數 AQARA_ACCOUNT。⚠️ 真的會寄信，別當健康檢查亂打。"""
    return aqara_api.request_auth_code(account or None)


@app.post("/aqara/auth/token", dependencies=[Depends(verify_api_key)])
def exchange_aqara_token(auth_code: str, account: str = ""):
    """授權第二步：用收到的授權碼換權杖，成功即寫入「系統狀態」分頁（跨重啟存活）。"""
    return aqara_api.exchange_token(auth_code, account or None)


@app.post("/aqara/auth/refresh", dependencies=[Depends(verify_api_key)])
def refresh_aqara_token():
    """Debug: 手動換一次權杖。正常情況不用打——到期前 10 分鐘會自動換，
    真的過期也會在下一次呼叫吃到 code 108 時自動補換。"""
    return aqara_api.refresh_now()


@app.get("/aqara/devices", dependencies=[Depends(verify_api_key)])
def list_aqara_devices():
    """Debug: 列出 Aqara 帳號下所有裝置（含 did / model / 名稱）。
    從這裡抓 FP2 的 did 填進環境變數 AQARA_FP2_DID。"""
    devices = aqara_api.get_devices()
    if isinstance(devices, dict):
        return devices
    return {"count": len(devices), "devices": devices}


@app.get("/aqara/devices/{did}/resources", dependencies=[Depends(verify_api_key)])
def get_aqara_device_resources(did: str):
    """Debug: 這台裝置的 model 開放了哪些 resource（id / 名稱 / 說明），不含當下值。"""
    device = aqara_api.get_device(did)
    if isinstance(device, dict) and "error" in device:
        return device
    model = device.get("model", "")
    return {"did": did, "model": model, "resources": aqara_api.get_resource_catalog(model)}


@app.get("/aqara/devices/{did}/values", dependencies=[Depends(verify_api_key)])
def get_aqara_device_values(did: str):
    """Debug: 這台裝置**所有**開放 resource 的當下值（先查清單再照清單讀）。
    FP2 的「有沒有人」是哪個 resource id，就是看這支的輸出對出來的——確認後填進
    環境變數 AQARA_FP2_PRESENCE_RESOURCE，語意層就不再靠名稱關鍵字猜。"""
    return aqara_api.read_device(did)


@app.get("/aqara/fp2", dependencies=[Depends(verify_api_key)])
def get_aqara_fp2_snapshot(did: str = ""):
    """FP2 當下狀態（presence + 全部原始 resource）。did 不帶就用 AQARA_FP2_DID，
    再沒有就自己去帳號裡找第一台 FP2。presence 為 null = 這次沒判斷出來，
    原始資源仍原樣附上。"""
    return aqara_api.fp2_snapshot(did or None)


@app.post("/aqara/raw", dependencies=[Depends(verify_api_key)])
def call_aqara_raw(payload: dict = Body(...)):
    """Debug: 直接送任意 intent（{"intent": "...", "data": {...}}），回原始 JSON。
    給還沒封裝的 API 探路用，例如訊息推送訂閱 config.resource.subscribe——
    先在這裡試通了再決定要不要寫成正式函式。"""
    intent = str(payload.get("intent") or "").strip()
    if not intent:
        raise HTTPException(status_code=400, detail="需要 intent 欄位")
    return aqara_api.raw_call(intent, payload.get("data"))


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


@app.get("/switchbot/webhook/status", dependencies=[Depends(verify_api_key)])
def switchbot_webhook_status():
    """Debug: 看 SwitchBot Cloud 目前註冊的 webhook URL，確認自動夜燈推播路徑活著。"""
    return switchbot_api.query_webhook()


@app.post("/switchbot/webhook")
async def switchbot_webhook(request: Request):
    """SwitchBot Cloud webhook 接收端（Hub 2 changeReport → 自動夜燈秒級評估）。

    SwitchBot 不對請求簽名，這個端點無法驗證來源；payload 只拿來跟已設定規則的
    sensor_device_id 比對，不匹配就忽略——偽造流量最多只能在啟用時段內觸發一次
    既有夜燈規則的重新評估，無法控制其他任何設備。
    評估丟背景 thread 跑（含 agent WS 往返），立刻回 200 讓 SwitchBot 不重送。"""
    try:
        body = await request.json()
    except Exception:
        return {"status": "ignored"}
    context = body.get("context") if isinstance(body, dict) else None
    if not isinstance(context, dict):
        return {"status": "ignored"}
    device_mac = context.get("deviceMac") or context.get("deviceId") or ""
    light_level = context.get("lightLevel")
    if device_mac and light_level is not None:
        import lighting_auto
        threading.Thread(
            target=lighting_auto.on_light_report,
            args=(str(device_mac), light_level),
            daemon=True,
        ).start()
    return {"status": "ok"}


# ════════════════════════════════════════════
# LINE Webhook
# ════════════════════════════════════════════

# Preserve serialized LINE handling without occupying the ASGI loop while waiting
# on Sheets, Claude or LINE. Waiting callbacks do not consume worker threads.
_line_callback_lock = asyncio.Lock()


@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    try:
        async with _line_callback_lock:
            await run_in_threadpool(webhook_handler.handle, body.decode(), signature)
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

        # Dashboard 裝置配對登入：使用者輸入「登入 123456」或「配對兒童 123456」核准一台 PWA。
        # 不經 Claude（早退、零成本）。身分來自 webhook 的 user_id（LINE 認證過）。
        # 容忍空格/分隔（畫面把碼顯示成「123 456」好讀，使用者可能照打）——抽出純數字、
        # 剛好 6 位才當登入碼；否則（如「登入很麻煩」）落到下面正常處理。
        # 「配對兒童」= 把這台核准成兒童遙控器（requested_role=kid，限制只能用裝置頁）；
        # 最終角色在 device_auth.approve 用「最嚴格者勝」決定（裝置自報 kid 也算數）。
        _stripped = text.strip()
        if _stripped.startswith("配對兒童"):
            requested_role, _code_src = "kid", _stripped[len("配對兒童"):]
        elif _stripped.startswith("登入"):
            requested_role, _code_src = "member", _stripped[2:]
        else:
            requested_role, _code_src = None, ""
        login_code = re.sub(r"\D", "", _code_src) if requested_role else ""
        if len(login_code) == 6:
            code = login_code
            members = get_sheet("家庭成員").get_all_records()
            member = next(
                (m for m in members
                 if str(m.get("Line User ID", "")) == user_id and m.get("狀態") == "啟用"),
                None,
            )
            if not member:
                reply = "❌ 你不是家庭成員，無法登入 Dashboard。"
            else:
                name = member.get("名稱", "")
                picture = ""
                try:
                    profile = line_bot_api.get_profile(user_id)
                    picture = getattr(profile, "picture_url", "") or ""
                except Exception as e:
                    print(f"[LOGIN] get_profile failed: {e}")
                final_role = device_auth.approve(code, user_id, name, picture, requested_role=requested_role)
                if final_role == "kid":
                    reply = "✅ 已授權這台為兒童遙控器（只能進裝置頁），回到網頁就會自動進入 🧒"
                elif final_role:
                    reply = f"✅ 已授權登入 Dashboard（以 {name} 的身分），回到網頁就會自動進入 🏠"
                else:
                    reply = "❌ 驗證碼錯誤或已過期，請回 Dashboard 重新取得一組。"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

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
        # 分清楚「Google 試算表抖了」跟「我們自己壞了」——前者使用者等一下重試就好，
        # 不必以為 bot 掛了（重試都吃完還失敗才會走到這）。
        reply = ("⚠️ 資料庫（Google 試算表）暫時連不上，這是 Google 端的短暫異常，"
                 "請過一兩分鐘再說一次。"
                 if is_transient_error(e) else "抱歉，系統暫時出了點問題，請稍後再試。")

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
