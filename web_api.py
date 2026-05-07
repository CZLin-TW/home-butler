"""
Web Dashboard REST API
提供給 Smart Home Dashboard 前端使用的 REST API endpoints。
所有業務邏輯重用現有 handlers，不重複實作。
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from sheets import RequestContext, get_all_devices_by_type, get_device_id_by_name, get_device_auth_by_name
from handlers.food import handle_add, handle_delete, handle_modify, handle_query
from handlers.todo import handle_add_todo, handle_modify_todo, handle_delete_todo
from handlers.device import (
    handle_control_ac, handle_control_ir, handle_query_sensor,
    handle_control_dehumidifier, handle_query_dehumidifier,
    apply_sensor_compensation,
)
from handlers.schedule import handle_add_schedule, handle_modify_schedule, handle_delete_schedule, handle_query_schedule
from auth import verify_api_key
import switchbot_api
import panasonic_api
import weather_api
import pc_state
import sensor_state

router = APIRouter(prefix="/api", dependencies=[Depends(verify_api_key)])


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


def _fetch_dehumidifier_status(auth, device_id):
    """查詢除濕機狀態（供平行執行）"""
    try:
        status = panasonic_api.get_dehumidifier_status(auth, device_id)
        if "error" not in status:
            return {
                "power": status.get("0x00") == "1",
                "mode": panasonic_api.MODE_DISPLAY.get(str(status.get("0x01", "")), ""),
                "targetHumidity": panasonic_api.HUMIDITY_DISPLAY.get(str(status.get("0x04", "")), ""),
            }
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
def api_get_device_status():
    """查詢感測器/除濕機即時狀態，回傳 {裝置名稱: 狀態}。
    前端在裝置清單載入後非同步呼叫此 endpoint 補齊即時數值。"""
    ctx = RequestContext()
    ctx.load()
    devices = [r for r in ctx.get("智能居家") if r.get("狀態") == "啟用"]

    status_map = {}
    futures = {}

    with ThreadPoolExecutor(max_workers=4) as executor:
        for d in devices:
            name = d.get("名稱", "")
            if d.get("類型") == "感應器" and d.get("Device ID"):
                future = executor.submit(_fetch_sensor_status, d["Device ID"])
                futures[future] = (name, d)
            if d.get("類型") == "除濕機" and d.get("Auth") and d.get("Device ID"):
                future = executor.submit(_fetch_dehumidifier_status, d["Auth"], d["Device ID"])
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


@router.get("/devices/sensor")
def api_query_sensor(device_name: str = ""):
    ctx = RequestContext()
    ctx.load()
    result = handle_query_sensor({"device_name": device_name}, ctx)
    if "❌" in result: raise HTTPException(status_code=400, detail=result)
    return {"message": result}


# ── 待辦事項 ──

@router.get("/todos")
def api_get_todos():
    ctx = RequestContext()
    ctx.load()
    return [r for r in ctx.get("待辦事項") if r.get("狀態") == "待辦"]


class TodoAddRequest(BaseModel):
    item: str
    date: str
    time: Optional[str] = ""
    person: str
    type: Optional[str] = "私人"


@router.post("/todos")
def api_add_todo(req: TodoAddRequest):
    ctx = RequestContext()
    ctx.load()
    data = {"item": req.item, "date": req.date, "time": req.time or "", "person": req.person, "type": req.type or "私人"}
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
    dh_modes = {}
    for k, v in panasonic_api.DEHUMIDIFIER_MODE_MAP.items():
        if v not in dh_modes: dh_modes[v] = k

    return {
        "ac": {
            "modes": [{"value": label, "label": label} for _, label in sorted(ac_modes.items())],
            "fan_speeds": [{"value": label, "label": label} for _, label in sorted(ac_fans.items())],
            "temperature": {"min": 16, "max": 30},
        },
        "dehumidifier": {
            "modes": [{"value": label, "label": label} for _, label in sorted(dh_modes.items())],
            "humidity": sorted(panasonic_api.HUMIDITY_VALUE_MAP.keys()),
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
