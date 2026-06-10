"""
SwitchBot API v1.1 封裝模組
- HMAC-SHA256 認證
- 設備列表查詢
- 設備狀態查詢（溫濕度感應器）
- 設備控制指令（冷氣 IR 等）
"""

import time
import hashlib
import hmac
import base64
import uuid
import httpx
import os

SWITCHBOT_TOKEN = os.environ.get("SWITCHBOT_TOKEN", "")
SWITCHBOT_SECRET = os.environ.get("SWITCHBOT_SECRET", "")
BASE_URL = "https://api.switch-bot.com/v1.1"


def _make_headers():
    """產生 SwitchBot API v1.1 所需的認證 headers"""
    token = SWITCHBOT_TOKEN
    secret = SWITCHBOT_SECRET
    nonce = str(uuid.uuid4())
    t = int(round(time.time() * 1000))
    string_to_sign = f"{token}{t}{nonce}"

    sign = base64.b64encode(
        hmac.new(
            secret.encode("utf-8"),
            msg=string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
    ).decode("utf-8")

    return {
        "Authorization": token,
        "sign": sign,
        "nonce": nonce,
        "t": str(t),
        "Content-Type": "application/json; charset=utf8",
    }


def get_devices():
    """取得所有設備列表（物理設備 + 紅外線虛擬設備）"""
    try:
        resp = httpx.get(f"{BASE_URL}/devices", headers=_make_headers(), timeout=10)
        data = resp.json()
        if data.get("statusCode") == 100:
            return {
                "physical": data["body"].get("deviceList", []),
                "infrared": data["body"].get("infraredRemoteList", []),
            }
        return {"error": data.get("message", "未知錯誤")}
    except Exception as e:
        return {"error": str(e)}


def get_device_status(device_id):
    """取得指定設備的狀態（溫度、濕度等）"""
    try:
        resp = httpx.get(
            f"{BASE_URL}/devices/{device_id}/status",
            headers=_make_headers(),
            timeout=10,
        )
        data = resp.json()
        if data.get("statusCode") == 100:
            return data["body"]
        return {"error": data.get("message", "未知錯誤")}
    except Exception as e:
        return {"error": str(e)}


def send_command(device_id, command, parameter="default", command_type="command"):
    """
    對指定設備發送控制指令

    冷氣 (Air Conditioner) 常用指令：
    - command="turnOn", parameter="default"  → 開機
    - command="turnOff", parameter="default" → 關機
    - command="setAll", parameter="{temp},{mode},{fan},{power}"
        temp: 16~30
        mode: 1=auto, 2=cool, 3=dry, 4=fan, 5=heat
        fan:  1=auto, 2=low, 3=medium, 4=high
        power: on/off
    """
    try:
        payload = {
            "command": command,
            "parameter": parameter,
            "commandType": command_type,
        }
        resp = httpx.post(
            f"{BASE_URL}/devices/{device_id}/commands",
            headers=_make_headers(),
            json=payload,
            timeout=10,
        )
        data = resp.json()
        if data.get("statusCode") == 100:
            return {"success": True, "message": "指令已送出"}
        return {"success": False, "error": data.get("message", "未知錯誤")}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── 高階封裝：冷氣控制 ──

AC_MODE_MAP = {
    "自動": 1, "auto": 1,
    "冷氣": 2, "制冷": 2, "cool": 2, "冷": 2,
    "除濕": 3, "乾燥": 3, "dry": 3,
    "送風": 4, "風扇": 4, "fan": 4,
    "暖氣": 5, "制熱": 5, "heat": 5, "暖": 5,
}

AC_FAN_MAP = {
    "自動": 1, "auto": 1,
    "低": 2, "low": 2, "弱": 2, "小": 2,
    "中": 3, "medium": 3,
    "高": 4, "high": 4, "強": 4, "大": 4,
}


def ac_turn_on(device_id):
    return send_command(device_id, "turnOn")


def ac_turn_off(device_id):
    return send_command(device_id, "turnOff")


def ac_set_all(device_id, temperature=26, mode=2, fan_speed=1, power="on"):
    """
    設定冷氣所有參數
    temperature: 16~30
    mode: 1=auto, 2=cool, 3=dry, 4=fan, 5=heat
    fan_speed: 1=auto, 2=low, 3=medium, 4=high
    power: "on" / "off"
    """
    parameter = f"{temperature},{mode},{fan_speed},{power}"
    return send_command(device_id, "setAll", parameter)


# ── 高階封裝：感應器讀取 ──

def get_hub_sensor(device_id):
    """讀取 Hub 的溫濕度感應器。Meter Pro CO2 會多回 CO2 欄位（ppm）。"""
    status = get_device_status(device_id)
    if "error" in status:
        return status
    temp = status.get("temperature")
    humidity = status.get("humidity")
    co2 = status.get("CO2")    # Meter Pro CO2 實測欄位名 "CO2"（大寫），其他感測器沒這欄回 None
    # SwitchBot Cloud 偶爾在 hub 失聯時回 statusCode=100 但 body temperature/humidity
    # 都是 0（default 值，不是真實讀值）。室內感測器不可能同時 0°C + 0%RH，
    # 把這種 case 視為失敗而不是 record 進去。
    if temp == 0 and humidity == 0:
        return {"error": "sensor returned 0/0 (hub likely disconnected)"}
    return {"temperature": temp, "humidity": humidity, "co2": co2}


# ── Webhook 管理（照明自動化用） ──
# SwitchBot 一個 token 只能註冊一個 webhook URL；目前唯一的 consumer 是
# home-butler /switchbot/webhook（Hub 2 lightLevel → 自動夜燈）。

def _webhook_request(path, payload):
    try:
        resp = httpx.post(
            f"{BASE_URL}/webhook/{path}",
            headers=_make_headers(),
            json=payload,
            timeout=10,
        )
        data = resp.json()
        if data.get("statusCode") == 100:
            return {"success": True, "body": data.get("body") or {}}
        return {"success": False, "error": data.get("message", "未知錯誤")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def query_webhook():
    """查詢目前註冊的 webhook URL。從未註冊過時 SwitchBot 回非 100（視為無）。"""
    result = _webhook_request("queryWebhook", {"action": "queryUrl"})
    if not result["success"]:
        return {"urls": [], "error": result["error"]}
    return {"urls": result["body"].get("urls") or []}


def setup_webhook(url):
    return _webhook_request("setupWebhook", {
        "action": "setupWebhook", "url": url, "deviceList": "ALL",
    })


def update_webhook(url):
    return _webhook_request("updateWebhook", {
        "action": "updateWebhook", "config": {"url": url, "enable": True},
    })


def delete_webhook(url):
    return _webhook_request("deleteWebhook", {"action": "deleteWebhook", "url": url})


def ensure_webhook(url):
    """確保 webhook 指向 url（冪等，startup 呼叫）。
    沒註冊過 → setup；已註冊別的 URL → update；已是目標 URL → no-op。"""
    q = query_webhook()
    urls = q.get("urls") or []
    if url in urls:
        return {"success": True, "already": True}
    if urls:
        return update_webhook(url)
    return setup_webhook(url)


# ── 高階封裝：DIY IR 設備控制 ──

# 會被映射到標準 turnOn/turnOff 的按鈕名稱
IR_POWER_ON_NAMES = {"電源", "開", "開機", "turn on", "turnon", "power on", "on"}
IR_POWER_OFF_NAMES = {"關", "關機", "turn off", "turnoff", "power off", "off"}

def ir_control(device_id, button_name):
    """
    控制 DIY IR 設備
    - 開/關類按鈕 → 使用標準 turnOn/turnOff（commandType: command）
    - 其他自訂按鈕 → 使用 customize 模式
    """
    lower = button_name.lower().strip()

    if lower in IR_POWER_ON_NAMES or button_name in IR_POWER_ON_NAMES:
        return send_command(device_id, "turnOn", "default", "command")
    elif lower in IR_POWER_OFF_NAMES or button_name in IR_POWER_OFF_NAMES:
        return send_command(device_id, "turnOff", "default", "command")
    else:
        return send_command(device_id, button_name, "default", "customize")