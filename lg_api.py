"""
LG ThinQ Connect API 封裝模組（thinq.dev 官方 API）

認證用 Personal Access Token（PAT），不需帳密登入流程。
裝置以 ThinQ 的 deviceId 識別（填在「智能居家」分頁的 Device ID 欄；品牌欄填 LG）。

⚠️ 連不到 LG API 的環境無法測試。除濕機的 property 欄位名 / 值會因機型而異，
   下方「校準點」常數需用 debug 端點（/lg/devices/{id}/profile、/state）對照真實
   裝置回應後調整。control / state 的巢狀結構就是照 profile 來的。
"""

import base64
import os
import uuid

import httpx

from config import LG_PAT, LG_COUNTRY, LG_CLIENT_ID, LG_API_BASE

# ThinQ Connect 公開 API key（所有 client 共用，官方 SDK 內建值，非機密）
API_KEY = "v6GFvkweNo7DK7yD3ylIZ9w52aKBU0eJ7wLXkSR3"
USER_AGENT = "home-butler"
REQUEST_TIMEOUT = 20

# 區域 endpoint：依國碼決定。KR→韓國、歐洲國家→歐洲，其餘（含台灣 TW）→ AIC。
_REGION_ENDPOINTS = {
    "KIC": "https://api-kic.lgthinq.com",
    "AIC": "https://api-aic.lgthinq.com",
    "EIC": "https://api-eic.lgthinq.com",
}
_EU_COUNTRIES = {
    "AL", "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
    "GR", "HU", "IS", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "NO", "PL",
    "PT", "RO", "SK", "SI", "ES", "SE", "CH", "GB", "UK",
}


def _endpoint() -> str:
    if LG_API_BASE:
        return LG_API_BASE.rstrip("/")
    country = (LG_COUNTRY or "TW").upper()
    if country == "KR":
        region = "KIC"
    elif country in _EU_COUNTRIES:
        region = "EIC"
    else:
        region = "AIC"
    return _REGION_ENDPOINTS[region]


def probe_regions():
    """對三個區域 endpoint 各打一次 GET /devices，回報結果，用來找出帳號對應區域。
    哪個區回 200（或非 1310 Not supported domain）就是對的，把它填到 LG_API_BASE。"""
    if not LG_PAT:
        return {"error": "LG_PAT 未設定"}
    out = {}
    for region, base in _REGION_ENDPOINTS.items():
        try:
            resp = _client.get(f"{base}/devices", headers=_headers())
            try:
                body = resp.json()
            except Exception:
                body = resp.text[:300]
            out[region] = {"base": base, "status": resp.status_code, "body": body}
        except Exception as e:
            out[region] = {"base": base, "error": str(e)}
    return out


_client = httpx.Client(timeout=REQUEST_TIMEOUT)


def _message_id() -> str:
    """ThinQ 要求每個 request 帶唯一 x-message-id（22 字 base64url、無 padding）。"""
    return base64.urlsafe_b64encode(uuid.uuid4().bytes).decode().rstrip("=")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {LG_PAT}",
        "x-country": (LG_COUNTRY or "TW").upper(),
        "x-message-id": _message_id(),
        "x-client-id": LG_CLIENT_ID,
        "x-api-key": API_KEY,
        "x-service-phase": "OP",
        "x-user-agent": USER_AGENT,
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, json_body: dict | None = None):
    """打 ThinQ Connect，回傳 response payload 或 {"error": ...}。"""
    if not LG_PAT:
        return {"error": "LG_PAT 未設定（請在 Render 環境變數填入 thinq.dev 產生的 PAT）"}
    url = f"{_endpoint()}{path}"
    try:
        resp = _client.request(method, url, headers=_headers(), json=json_body)
    except Exception as e:
        return {"error": f"LG ThinQ 連線失敗：{e}"}
    try:
        data = resp.json()
    except Exception:
        return {"error": f"LG ThinQ 回應非 JSON（HTTP {resp.status_code}）：{resp.text[:200]}"}
    if resp.status_code != 200:
        err = data.get("error", data) if isinstance(data, dict) else data
        return {"error": f"LG ThinQ HTTP {resp.status_code}：{err}"}
    if isinstance(data, dict) and "error" in data and data["error"]:
        return {"error": data["error"]}
    # ThinQ 把實際資料包在 response 底下
    if isinstance(data, dict) and "response" in data:
        return data["response"]
    return data


# ── 探索（校準與設定用） ──

def get_devices():
    """列出 PAT 帳號下所有 ThinQ 裝置。回傳 list 或 {"error": ...}。"""
    return _request("GET", "/devices")


def get_device_profile(device_id: str):
    """某裝置的能力 profile（property 欄位 / 可用值），校準除濕機欄位用。"""
    return _request("GET", f"/devices/{device_id}/profile")


def get_device_state(device_id: str):
    """某裝置目前狀態（巢狀 property 結構）。"""
    return _request("GET", f"/devices/{device_id}/state")


def _control(device_id: str, body: dict) -> dict:
    """送控制指令，回傳 {"success": bool, ...} 對齊 panasonic_api 介面。"""
    result = _request("POST", f"/devices/{device_id}/control", json_body=body)
    if isinstance(result, dict) and "error" in result:
        return {"success": False, "error": result["error"]}
    return {"success": True}


# ════════════════════════════════════════════
# 校準點 — 用 /lg/devices/{id}/profile + /state 確認後調整這一區
# 不同除濕機機型的 property node / key / 值可能不同
# ════════════════════════════════════════════

POWER_NODE = "operation"
POWER_KEY = "dehumidifierOperationMode"
POWER_ON_VALUE = "POWER_ON"
POWER_OFF_VALUE = "POWER_OFF"

HUMIDITY_NODE = "humidity"
TARGET_HUMIDITY_KEY = "targetHumidity"
CURRENT_HUMIDITY_KEY = "currentHumidity"

JOBMODE_NODE = "operation"
JOBMODE_KEY = "dehumidifierOperationMode"

# 使用者講的模式 → ThinQ jobMode 值。先放常見值，校準後補全。
DEHUMIDIFIER_MODE_MAP = {
    "連續除濕": "CONTINUOUS_DEHUMIDIFY", "continuous": "CONTINUOUS_DEHUMIDIFY",
    "自動除濕": "SMART_DEHUMIDIFY", "auto": "SMART_DEHUMIDIFY",
    "快速除濕": "FAST_DEHUMIDIFY", "quick": "FAST_DEHUMIDIFY",
    "送風": "AIR_CLEAN", "fan": "AIR_CLEAN",
}

# ════════════════════════════════════════════


def dehumidifier_turn_on(device_id: str) -> dict:
    return _control(device_id, {POWER_NODE: {POWER_KEY: POWER_ON_VALUE}})


def dehumidifier_turn_off(device_id: str) -> dict:
    return _control(device_id, {POWER_NODE: {POWER_KEY: POWER_OFF_VALUE}})


def dehumidifier_set_mode(device_id: str, mode_str: str) -> dict:
    value = DEHUMIDIFIER_MODE_MAP.get(mode_str)
    if value is None:
        return {"success": False, "error": f"不支援的模式：{mode_str}"}
    return _control(device_id, {JOBMODE_NODE: {JOBMODE_KEY: value}})


def dehumidifier_set_humidity(device_id: str, humidity: int) -> dict:
    return _control(device_id, {HUMIDITY_NODE: {TARGET_HUMIDITY_KEY: int(humidity)}})


def get_dehumidifier_status(device_id: str) -> dict:
    """回傳原始 state dict（或 {"error": ...}）。"""
    return get_device_state(device_id)


def _dig(state: dict, node: str, key: str):
    """從巢狀 state 取值；ThinQ 有時把 node 包成 dict、有時平鋪，兩種都試。"""
    if not isinstance(state, dict):
        return None
    sub = state.get(node)
    if isinstance(sub, dict) and key in sub:
        return sub[key]
    return state.get(key)


def format_dehumidifier_status(status: dict, device_name: str = "除濕機") -> str:
    if not isinstance(status, dict) or "error" in status:
        err = status.get("error") if isinstance(status, dict) else status
        return f"❌ 無法取得{device_name}狀態：{err}"
    power_raw = _dig(status, POWER_NODE, POWER_KEY)
    power = "開啟" if power_raw == POWER_ON_VALUE else ("關閉" if power_raw == POWER_OFF_VALUE else "未知")
    target = _dig(status, HUMIDITY_NODE, TARGET_HUMIDITY_KEY)
    current = _dig(status, HUMIDITY_NODE, CURRENT_HUMIDITY_KEY)
    parts = [f"💧 {device_name}：{power}"]
    if current is not None:
        parts.append(f"目前濕度 {current}%")
    if target is not None:
        parts.append(f"目標濕度 {target}%")
    return "｜".join(parts)
