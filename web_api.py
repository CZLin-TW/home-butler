"""
Web Dashboard REST API
提供給 Smart Home Dashboard 前端使用的 REST API endpoints。
所有業務邏輯重用現有 handlers，不重複實作。
"""

import threading
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import SIRI_USER_ID, TZ, now_taipei
from assistant import process_message
from prompt import get_user_name
from conversation import save_conversation, cleanup_conversation
from sheets import RequestContext, get_all_devices_by_type, get_device_id_by_name, get_device_auth_by_name
from handlers.food import handle_add, handle_delete, handle_modify, handle_query
from handlers.todo import handle_add_todo, handle_modify_todo, handle_delete_todo
from handlers.recurring_todo import (
    handle_add_recurring_todo, handle_modify_recurring_todo,
    handle_stop_recurring_todo, list_recurring_rules,
)
from handlers.device import (
    handle_control_ac, handle_control_ir, handle_query_sensor,
    handle_control_dehumidifier, handle_query_dehumidifier,
    apply_sensor_compensation,
)
from handlers.schedule import handle_add_schedule, handle_modify_schedule, handle_delete_schedule, handle_query_schedule
from auth import verify_api_key
import device_auth
import switchbot_api
import panasonic_api
import lg_api
import weather_api
import pc_state
import sensor_state
import ac_history
import dehumidifier_auto
import dehumidifier_auto_service
import dehumidifier_driver
import dehumidifier_history
from hue_area_settings import DEFAULT_LIGHT_AREA_NAME, resolve_area

router = APIRouter(prefix="/api", dependencies=[Depends(verify_api_key)])


# ── 自然語言入口（Siri 捷徑用）──

class AssistantRequest(BaseModel):
    text: str
    user_id: Optional[str] = None


@router.post("/assistant")
def api_assistant(req: AssistantRequest):
    """語音助理入口：吃一句自然語言，走跟 LINE bot 完全相同的 Claude pipeline。

    Siri 捷徑只負責聽寫成文字 POST 過來，後端 process_message 解析 + 分派 + 組句，
    回 {"reply": ...} 給捷徑朗讀。user_id 不帶則用 config.SIRI_USER_ID。
    對話歷史在背景存檔，讓多輪對話（「再低一度」）能延續。
    """
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不可為空")

    user_id = req.user_id or SIRI_USER_ID
    ctx = RequestContext()
    ctx.load()
    user_name = get_user_name(user_id, ctx)
    reply = process_message(user_id, text, user_name, ctx)

    def _save():
        try:
            save_conversation(user_id, "user", text)
            save_conversation(user_id, "assistant", reply)
            cleanup_conversation(user_id)
        except Exception as e:
            print(f"[ASSISTANT SAVE ERROR] {e}")
    threading.Thread(target=_save, daemon=True).start()

    return {"reply": reply}


# ── 首頁彙整 ──

@router.get("/dashboard")
def api_dashboard():
    """首頁彙整 API：一次回傳天氣、裝置、待辦、庫存（減少往返次數）
    不含感測器/除濕機即時狀態——前端另呼叫 /api/devices/status 補齊。

    註：ThreadPoolExecutor 內同時跑兩個天氣 future，主緒程並行跑 ctx.load()
    （同步）拉 Sheet。進入 with 區塊後才 .result()，讓這三件事重疊起來。
    """
    results = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        weather_today_future = executor.submit(weather_api.get_weather_summary, "today", None)
        weather_tomorrow_future = executor.submit(weather_api.get_weather_summary, "tomorrow", None)

        ctx = RequestContext()
        ctx.load()

        device_list = [
            {
                "name": d.get("名稱"),
                "type": d.get("類型"),
                "brand": d.get("品牌", ""),
                "location": d.get("位置", ""),
                "deviceId": d.get("Device ID", ""),
                "buttons": d.get("按鈕", ""),
                "lastPower": d.get("最後電源", ""),
                "lastTemperature": d.get("最後溫度", ""),
                "lastMode": d.get("最後模式", ""),
                "lastFanSpeed": d.get("最後風速", ""),
                "lastUpdatedAt": d.get("最後更新時間", ""),
            }
            for d in ctx.get("智能居家") if d.get("狀態") == "啟用"
        ]

        try:
            results["weatherToday"] = weather_today_future.result(timeout=15)
        except Exception:
            results["weatherToday"] = None
        try:
            results["weatherTomorrow"] = weather_tomorrow_future.result(timeout=15)
        except Exception:
            results["weatherTomorrow"] = None

    results["devices"] = device_list
    results["todos"] = [r for r in ctx.get("待辦事項") if r.get("狀態") == "待辦"]
    results["food"] = [r for r in ctx.get("食品庫存") if r.get("狀態") == "有效"]
    results["options"] = api_get_device_options()

    return results


# ── 裝置 ──

def _fetch_sensor_status(device_id):
    """查詢感測器狀態（供平行執行）"""
    try:
        status = switchbot_api.get_hub_sensor(device_id)
        if "error" not in status:
            return {"temperature": status.get("temperature"), "humidity": status.get("humidity")}
    except Exception as e:
        print(f"[WEB API] Sensor error: {e}")
    return {}


def _fetch_dehumidifier_status(device_row):
    """查詢除濕機狀態（供平行執行）。品牌分流（含 status → fields 正規化）
    交給 dehumidifier_driver，未來加品牌只動 driver、不動 web_api。"""
    driver = dehumidifier_driver.make_driver(device_row)
    if driver is None:
        return {}
    try:
        return driver.status_fields(driver.get_status())
    except Exception as e:
        print(f"[WEB API] Dehumidifier error: {e}")
        return {}


@router.get("/devices")
def api_get_devices():
    """列出所有啟用裝置（Sheet 資料，不含即時狀態）"""
    ctx = RequestContext()
    ctx.load()
    return [
        {
            "name": d.get("名稱"),
            "type": d.get("類型"),
            "brand": d.get("品牌", ""),
            "location": d.get("位置", ""),
            "deviceId": d.get("Device ID", ""),
            "buttons": d.get("按鈕", ""),
            "lastPower": d.get("最後電源", ""),
            "lastTemperature": d.get("最後溫度", ""),
            "lastMode": d.get("最後模式", ""),
            "lastFanSpeed": d.get("最後風速", ""),
            "lastUpdatedAt": d.get("最後更新時間", ""),
        }
        for d in ctx.get("智能居家") if d.get("狀態") == "啟用"
    ]


@router.get("/devices/status")
def api_get_device_status(name: str = ""):
    """查詢感測器/除濕機即時狀態，回傳 {裝置名稱: 狀態}。

    - 無 `name` query：所有啟用裝置並行查（給 Dashboard 60s 全局 polling 用）
    - 帶 `name=X` query：只查該裝置，避開 ThreadPoolExecutor 的 max(all latency)
      行為（給命令送出後樂觀更新 polling 用，不被其他雲端慢的裝置拖累）。
    """
    ctx = RequestContext()
    ctx.load()
    devices = [r for r in ctx.get("智能居家") if r.get("狀態") == "啟用"]
    if name:
        devices = [d for d in devices if d.get("名稱") == name]

    status_map = {}
    futures = {}

    with ThreadPoolExecutor(max_workers=4) as executor:
        for d in devices:
            name = d.get("名稱", "")
            if d.get("類型") == "感應器" and d.get("Device ID"):
                future = executor.submit(_fetch_sensor_status, d["Device ID"])
                futures[future] = (name, d)
            if d.get("類型") == "除濕機" and d.get("Device ID"):
                future = executor.submit(_fetch_dehumidifier_status, d)
                futures[future] = (name, d)

        for future in as_completed(futures):
            name, device_row = futures[future]
            try:
                status = future.result(timeout=15)
                if "temperature" in status or "humidity" in status:
                    t, h = apply_sensor_compensation(status.get("temperature"), status.get("humidity"), device_row)
                    status["temperature"] = t
                    status["humidity"] = h
                if status:
                    status_map[name] = status
            except Exception as e:
                print(f"[DEVICE STATUS] {name} query error: {e}")

    return status_map


class AcControlRequest(BaseModel):
    device_name: Optional[str] = ""
    power: str = "on"
    temperature: Optional[int] = None
    mode: Optional[str] = None
    fan_speed: Optional[str] = None


@router.post("/devices/control/ac")
def api_control_ac(req: AcControlRequest):
    ctx = RequestContext()
    ctx.load()
    data = {"device_name": req.device_name, "power": req.power}
    if req.temperature is not None: data["temperature"] = req.temperature
    if req.mode is not None: data["mode"] = req.mode
    if req.fan_speed is not None: data["fan_speed"] = req.fan_speed
    result = handle_control_ac(data, ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


class IrControlRequest(BaseModel):
    device_name: str
    button: str


@router.post("/devices/control/ir")
def api_control_ir(req: IrControlRequest):
    ctx = RequestContext()
    ctx.load()
    result = handle_control_ir({"device_name": req.device_name, "button": req.button}, ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


class DehumidifierControlRequest(BaseModel):
    device_name: Optional[str] = ""
    power: Optional[str] = None
    mode: Optional[str] = None
    humidity: Optional[int] = None


@router.post("/devices/control/dehumidifier")
def api_control_dehumidifier(req: DehumidifierControlRequest):
    ctx = RequestContext()
    ctx.load()
    data = {"device_name": req.device_name}
    if req.power is not None: data["power"] = req.power
    if req.mode is not None: data["mode"] = req.mode
    if req.humidity is not None: data["humidity"] = req.humidity
    result = handle_control_dehumidifier(data, ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


# ── 除濕機自動規則 (條件式 ON/OFF) ──

class DehumAutoRuleRequest(BaseModel):
    device_name: str
    auto_mode: bool
    sensor_name: Optional[str] = None
    duration_min: Optional[int] = Field(default=None, ge=0)
    threshold: Optional[int] = None        # = UI 目標濕度 segment 當下值
    on_mode: Optional[str] = None          # = UI 模式 segment 當下值


@router.get("/dehumidifier/auto-rule")
def api_get_dehum_auto_rules():
    """回傳所有除濕機的自動規則 + runtime state（含 above_since 等）。"""
    return dehumidifier_auto.get_all_rules()


@router.post("/dehumidifier/auto-rule")
def api_set_dehum_auto_rule(req: DehumAutoRuleRequest):
    """設定/更新一台除濕機的自動規則。

    auto_mode=False → True 且 sensor + 設備資訊都備齊時，set_rule 內會立即
    依「對稱單一門檻」規則 fire ON 或 OFF。"""
    ctx = RequestContext()
    ctx.load()
    result = dehumidifier_auto_service.set_auto_rule(
        ctx,
        req.device_name,
        req.auto_mode,
        sensor_name=req.sensor_name,
        duration_min=req.duration_min,
        threshold=req.threshold,
        on_mode=req.on_mode,
    )
    return result["rule"]


@router.get("/devices/sensor")
def api_query_sensor(device_name: str = ""):
    ctx = RequestContext()
    ctx.load()
    result = handle_query_sensor({"device_name": device_name}, ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


# ── Dashboard 裝置配對登入（device-code）──
# 流程與安全細節見 device_auth.py。Dashboard BFF（帶 X-API-Key）呼叫這兩個端點；
# 核准動作在 LINE Bot（main.py）那邊做。PWA 全程不直接打 home-butler、也不離開容器。

@router.post("/auth/device/create")
def api_device_create():
    """PWA 取得一組 user_code + device_token。"""
    return device_auth.create_pairing()


@router.get("/auth/device/status")
def api_device_status(token: str):
    """PWA 輪詢配對狀態（帶自己保管的 device_token）。"""
    return device_auth.get_status(token)


# ── 待辦事項 ──

@router.get("/todos")
def api_get_todos():
    ctx = RequestContext()
    ctx.load()
    return [r for r in ctx.get("待辦事項") if r.get("狀態") == "待辦"]


def _sheet_bool(value):
    return str(value or "").strip().upper() in ("TRUE", "1", "YES", "Y", "ON", "是", "要")


@router.get("/todos/light-reminders")
def api_get_todo_light_reminders():
    """Return due unfinished todos that should trigger Hue light reminders.

    The local PC agent polls this endpoint every minute and performs at most
    one Hue breathe action per poll, so multiple due todos do not overlap.
    """
    ctx = RequestContext()
    ctx.load()
    now = now_taipei()
    reminders = []
    for r in ctx.get("待辦事項"):
        if r.get("狀態") != "待辦":
            continue
        if not _sheet_bool(r.get("燈光提醒")):
            continue
        date_str = str(r.get("日期", "")).strip()
        time_str = str(r.get("時間", "")).strip()
        if not date_str or not time_str:
            continue
        try:
            due_at = TZ.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        except (ValueError, TypeError) as e:
            print(f"[WARN] 無法解析燈光提醒待辦時間 {date_str!r} {time_str!r}: {e}")
            continue
        if due_at > now:
            continue
        light_area_id = str(r.get("燈光區域ID", "") or "").strip()
        light_area = resolve_area(area_id=light_area_id) if light_area_id else resolve_area(DEFAULT_LIGHT_AREA_NAME)
        reminders.append({
            "item": r.get("事項", ""),
            "date": date_str,
            "time": time_str,
            "person": r.get("負責人", ""),
            "type": r.get("類型", "公開"),
            "light_area_id": light_area.get("id", ""),
            "light_area_name": light_area.get("name", "") or DEFAULT_LIGHT_AREA_NAME,
            "light_area_resource_type": light_area.get("resource_type", "grouped_light"),
            "due_at": due_at.isoformat(),
        })
    return {"count": len(reminders), "reminders": reminders}


class TodoAddRequest(BaseModel):
    item: str
    date: str
    time: Optional[str] = ""
    person: str
    type: Optional[str] = "私人"
    light_notify: Optional[bool] = None
    light_area_id: Optional[str] = None
    light_area: Optional[str] = None


@router.post("/todos")
def api_add_todo(req: TodoAddRequest):
    ctx = RequestContext()
    ctx.load()
    data = {
        "item": req.item,
        "date": req.date,
        "time": req.time or "",
        "person": req.person,
        "type": req.type or "私人",
    }
    if req.light_notify is not None:
        data["light_notify"] = req.light_notify
    if req.light_area_id is not None:
        data["light_area_id"] = req.light_area_id
    if req.light_area is not None:
        data["light_area"] = req.light_area
    result = handle_add_todo(data, req.person, ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


class TodoModifyRequest(BaseModel):
    item: str
    # date_orig / time_orig：定位舊 row（同名待辦多筆時用三元組精確找）。
    # 沒帶或空字串時 fallback 為「找事項名稱第一筆」舊行為，保留 LINE bot 路徑相容。
    date_orig: Optional[str] = None
    time_orig: Optional[str] = None
    item_new: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    person: Optional[str] = None
    type: Optional[str] = None
    light_notify: Optional[bool] = None
    light_area_id: Optional[str] = None
    light_area: Optional[str] = None
    requester: str


@router.patch("/todos")
def api_modify_todo(req: TodoModifyRequest):
    ctx = RequestContext()
    ctx.load()
    data = {"item": req.item}
    if req.date_orig is not None: data["date_orig"] = req.date_orig
    if req.time_orig is not None: data["time_orig"] = req.time_orig
    if req.item_new is not None: data["item_new"] = req.item_new
    if req.date is not None: data["date"] = req.date
    if req.time is not None: data["time"] = req.time
    if req.person is not None: data["person"] = req.person
    if req.type is not None: data["type"] = req.type
    if req.light_notify is not None: data["light_notify"] = req.light_notify
    if req.light_area_id is not None: data["light_area_id"] = req.light_area_id
    if req.light_area is not None: data["light_area"] = req.light_area
    result = handle_modify_todo(data, req.requester, ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


class TodoDeleteRequest(BaseModel):
    item: str
    # date_orig / time_orig：定位 row。同 modify，沒帶就 fallback 找第一筆。
    date_orig: Optional[str] = None
    time_orig: Optional[str] = None


@router.delete("/todos")
def api_delete_todo(req: TodoDeleteRequest):
    ctx = RequestContext()
    ctx.load()
    data = {"item": req.item}
    if req.date_orig is not None: data["date_orig"] = req.date_orig
    if req.time_orig is not None: data["time_orig"] = req.time_orig
    result = handle_delete_todo(data, ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


# ── 週期性待辦（recurring todo）──
# 模板 CRUD 純 proxy 到 handlers/recurring_todo.py。實際「生成」由 notify.py 的
# /notify_realtime tick 跑，受 config.recurring_todo_enabled() 總開關控制。

@router.get("/recurring-todos")
def api_get_recurring_todos():
    """列出啟用中的週期模板（每筆附『摘要』人類可讀字串給前端直接顯示）。"""
    return list_recurring_rules(active_only=True)


class RecurringTodoAddRequest(BaseModel):
    item: str
    recur_type: str                          # 每天 / 每週 / 每月 / 間隔天
    weekdays: Optional[list[int]] = None     # 每週：isoweekday [1,3,5]
    month_day: Optional[int] = None          # 每月：1~31
    interval_days: Optional[int] = None      # 間隔天：>=1
    time: Optional[str] = ""
    person: Optional[str] = None
    type: Optional[str] = "私人"
    light_notify: Optional[bool] = None
    light_area: Optional[str] = None
    light_area_id: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


@router.post("/recurring-todos")
def api_add_recurring_todo(req: RecurringTodoAddRequest):
    ctx = RequestContext()
    ctx.load()
    data = {"item": req.item, "recur_type": req.recur_type}
    for field in ("weekdays", "month_day", "interval_days", "time", "person",
                  "type", "light_notify", "light_area", "light_area_id",
                  "start_date", "end_date"):
        value = getattr(req, field)
        if value is not None:
            data[field] = value
    result = handle_add_recurring_todo(data, req.person or "", ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


class RecurringTodoModifyRequest(BaseModel):
    rule_id: Optional[str] = None            # Dashboard 走精準 ID
    item: Optional[str] = None               # 或用事項名（+ recur_type 消歧）
    recur_type: Optional[str] = None
    item_new: Optional[str] = None
    recur_type_new: Optional[str] = None
    weekdays: Optional[list[int]] = None
    month_day: Optional[int] = None
    interval_days: Optional[int] = None
    time: Optional[str] = None
    person: Optional[str] = None
    type: Optional[str] = None
    light_notify: Optional[bool] = None
    light_area: Optional[str] = None
    light_area_id: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    requester: Optional[str] = None


@router.patch("/recurring-todos")
def api_modify_recurring_todo(req: RecurringTodoModifyRequest):
    ctx = RequestContext()
    ctx.load()
    data = {}
    for field in ("rule_id", "item", "recur_type", "item_new", "recur_type_new",
                  "weekdays", "month_day", "interval_days", "time", "person",
                  "type", "light_notify", "light_area", "light_area_id",
                  "start_date", "end_date"):
        value = getattr(req, field)
        if value is not None:
            data[field] = value
    result = handle_modify_recurring_todo(data, req.requester or "", ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


class RecurringTodoStopRequest(BaseModel):
    rule_id: Optional[str] = None
    item: Optional[str] = None
    recur_type: Optional[str] = None


@router.delete("/recurring-todos")
def api_stop_recurring_todo(req: RecurringTodoStopRequest):
    """停整個週期（模板狀態 → 停用，不刪除）。"""
    ctx = RequestContext()
    ctx.load()
    data = {}
    for field in ("rule_id", "item", "recur_type"):
        value = getattr(req, field)
        if value is not None:
            data[field] = value
    result = handle_stop_recurring_todo(data, "", ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


# ── 食品庫存 ──

@router.get("/food")
def api_get_food():
    ctx = RequestContext()
    ctx.load()
    return [r for r in ctx.get("食品庫存") if r.get("狀態") == "有效"]


class FoodAddRequest(BaseModel):
    name: str
    quantity: Optional[int] = 1
    unit: Optional[str] = "個"
    expiry: str
    person: str


@router.post("/food")
def api_add_food(req: FoodAddRequest):
    ctx = RequestContext()
    ctx.load()
    data = {"name": req.name, "quantity": req.quantity, "unit": req.unit or "個", "expiry": req.expiry}
    result = handle_add(data, req.person, ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


class FoodModifyRequest(BaseModel):
    name: str
    name_new: Optional[str] = None
    quantity: Optional[int] = None
    unit: Optional[str] = None
    expiry: Optional[str] = None


@router.patch("/food")
def api_modify_food(req: FoodModifyRequest):
    ctx = RequestContext()
    ctx.load()
    data = {"name": req.name}
    if req.name_new is not None: data["name_new"] = req.name_new
    if req.quantity is not None: data["quantity"] = req.quantity
    if req.unit is not None: data["unit"] = req.unit
    if req.expiry is not None: data["expiry"] = req.expiry
    result = handle_modify(data, ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


class FoodDeleteRequest(BaseModel):
    name: str


@router.delete("/food")
def api_delete_food(req: FoodDeleteRequest):
    ctx = RequestContext()
    ctx.load()
    result = handle_delete({"name": req.name}, ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


# ── 排程 ──

@router.get("/schedules")
def api_get_schedules():
    ctx = RequestContext()
    ctx.load()
    return [r for r in ctx.get("排程指令") if r.get("狀態") == "待執行"]


class ScheduleAddRequest(BaseModel):
    device_name: str
    target_action: str
    params: dict
    trigger_time: str
    person: str


@router.post("/schedules")
def api_add_schedule(req: ScheduleAddRequest):
    ctx = RequestContext()
    ctx.load()
    data = {"device_name": req.device_name, "target_action": req.target_action, "params": req.params, "trigger_time": req.trigger_time}
    result = handle_add_schedule(data, req.person, ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


class ScheduleModifyRequest(BaseModel):
    # 找目標：原值
    device_name: str
    trigger_time: str
    # 新值（全部選填，至少要有一個）
    device_name_new: Optional[str] = None
    target_action_new: Optional[str] = None
    params_new: Optional[dict] = None
    trigger_time_new: Optional[str] = None
    person: str


@router.patch("/schedules")
def api_modify_schedule(req: ScheduleModifyRequest):
    ctx = RequestContext()
    ctx.load()
    data: dict = {"device_name": req.device_name, "trigger_time": req.trigger_time}
    if req.device_name_new is not None: data["device_name_new"] = req.device_name_new
    if req.target_action_new is not None: data["target_action_new"] = req.target_action_new
    if req.params_new is not None: data["params_new"] = req.params_new
    if req.trigger_time_new is not None: data["trigger_time_new"] = req.trigger_time_new
    result = handle_modify_schedule(data, req.person, ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


class ScheduleDeleteRequest(BaseModel):
    device_name: str
    trigger_time: Optional[str] = None
    all: Optional[bool] = False


@router.delete("/schedules")
def api_delete_schedule(req: ScheduleDeleteRequest):
    ctx = RequestContext()
    ctx.load()
    data = {"device_name": req.device_name}
    if req.trigger_time: data["trigger_time"] = req.trigger_time
    if req.all: data["all"] = True
    result = handle_delete_schedule(data, ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


# ── 天氣 ──

@router.get("/weather")
def api_get_weather(date: str = "today", location: Optional[str] = None):
    summary = weather_api.get_weather_summary(date, location)
    if isinstance(summary, dict) and "error" in summary:
        raise HTTPException(status_code=400, detail=summary["error"])
    return summary


# ── 家庭成員 ──

@router.get("/members")
def api_get_members():
    ctx = RequestContext()
    ctx.load()
    return [
        {"name": r.get("名稱"), "lineUserId": r.get("Line User ID")}
        for r in ctx.get("家庭成員")
        if r.get("狀態") == "啟用"
    ]


# ── 裝置選項 ──

@router.get("/devices/options")
def api_get_device_options():
    ac_modes = {}
    for k, v in switchbot_api.AC_MODE_MAP.items():
        if v not in ac_modes: ac_modes[v] = k
    ac_fans = {}
    for k, v in switchbot_api.AC_FAN_MAP.items():
        if v not in ac_fans: ac_fans[v] = k
    # Panasonic 模式：DEHUMIDIFIER_MODE_MAP 多個別名指向同一 int，dedupe 後依 int 排序
    dh_modes = {}
    for k, v in panasonic_api.DEHUMIDIFIER_MODE_MAP.items():
        if v not in dh_modes: dh_modes[v] = k
    pana_modes = [{"value": label, "label": label} for _, label in sorted(dh_modes.items())]
    pana_humidity = sorted(panasonic_api.HUMIDITY_VALUE_MAP.keys())

    # LG 模式：用 MODE_DISPLAY 的中文標籤（與 set_mode 的別名 key、狀態回傳的 mode 字串一致）
    lg_modes = [{"value": label, "label": label} for label in lg_api.MODE_DISPLAY.values()]
    lg_humidity = list(range(lg_api.TARGET_HUMIDITY_MIN, lg_api.TARGET_HUMIDITY_MAX + 1, lg_api.TARGET_HUMIDITY_STEP))

    return {
        "ac": {
            "modes": [{"value": label, "label": label} for _, label in sorted(ac_modes.items())],
            "fan_speeds": [{"value": label, "label": label} for _, label in sorted(ac_fans.items())],
            "temperature": {"min": 16, "max": 30},
        },
        "dehumidifier": {
            # 頂層維持 Panasonic（向後相容沒帶品牌的前端）；前端依 device.brand 從 byBrand 取對的一組
            "modes": pana_modes,
            "humidity": pana_humidity,
            "byBrand": {
                "Panasonic": {"modes": pana_modes, "humidity": pana_humidity},
                "LG": {"modes": lg_modes, "humidity": lg_humidity},
            },
        },
    }


# ── PC 監控 ──
# 第一版設計：agent 每 60s POST 一次當前指標，後端 in-memory ring buffer
# 累積最多 1440 點（24h）。Dashboard 拉 /computers/status 拿所有 PC 的 raw
# history + current snapshot。詳細設計見 pc_state.py 模組註解。

class FAHStatus(BaseModel):
    paused: Optional[bool] = None
    finish: Optional[bool] = None
    units_count: Optional[int] = None
    progress_pct: Optional[float] = None


class PCHeartbeatRequest(BaseModel):
    ip: str
    hostname: Optional[str] = ""
    cpu_model: Optional[str] = ""
    gpu_model: Optional[str] = ""
    cpu_pct: float
    ram_pct: float
    gpu_pct: Optional[float] = None
    gpu_temp_c: Optional[float] = None
    cpu_temp_c: Optional[float] = None  # Windows 上要靠 LibreHardwareMonitor，沒裝就 None
    fah: Optional[FAHStatus] = None


@router.post("/computers/heartbeat")
def api_pc_heartbeat(req: PCHeartbeatRequest):
    pc_state.record_heartbeat(req.model_dump())
    return {"ok": True}


@router.get("/computers/status")
def api_pc_status():
    """回傳所有已 heartbeat 過的 PC 當前狀態 + 最近 24h raw 歷史（每 60s 一點）。"""
    return pc_state.snapshot()


# ── 感測器歷史（home-butler 內部 polling SwitchBot API 累積） ──

@router.get("/sensors/status")
def api_sensors_status():
    """回傳所有 polling 過的感測器當前讀值 + 最近 24h history。
    polling 由 home-butler startup 時 spawn 的 thread 處理（main.py），不靠 PC agent。"""
    return sensor_state.snapshot()


@router.get("/ac/status")
def api_ac_status():
    """回傳所有空調的 24h 狀態歷史 snapshot。每 5 分鐘從「智能居家」分頁的
    「最後電源/溫度/模式/風速」欄位 snapshot。前端按 location 拼 segments
    在 sensor chart 背景畫色塊（冷氣藍 / 暖氣琥珀 / 除濕綠 / 其他灰）。"""
    return ac_history.snapshot()


@router.get("/dehumidifier/history")
def api_dehumidifier_history():
    """回傳所有除濕機的 24h power 歷史 snapshot。只有 auto_mode=True 的除濕機
    會被 polling 因此才有資料；前端只在「自動模式 ON」卡片內畫 on-segments
    背景（fresh 綠色）+ 綁定 sensor 的濕度線。"""
    return dehumidifier_history.snapshot()
