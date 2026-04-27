"""
Panasonic Smart App API 封裝模組
- 帳密登入，自動取得 CPToken
- 設備列表查詢
- 除濕機狀態查詢與控制
"""

import httpx
import os
import json
import threading

BASE_URL = "https://ems2.panasonic.com.tw/api"
APP_TOKEN = "D8CBFF4C-2824-4342-B22D-189166FEF503"
USER_AGENT = "okhttp/4.9.1"
REQUEST_TIMEOUT = 20

PANASONIC_ACCOUNT = os.environ.get("PANASONIC_ACCOUNT", "")
PANASONIC_PASSWORD = os.environ.get("PANASONIC_PASSWORD", "")

# 持久 HTTP Client（連線池 + keep-alive，避免每次冷連線）
_client = httpx.Client(
    base_url=f"{BASE_URL}/",
    headers={"user-agent": USER_AGENT, "Content-Type": "application/json"},
    timeout=REQUEST_TIMEOUT,
)

# Token 快取（服務運行期間保持登入狀態）
_cp_token = None
_refresh_token = None
_token_lock = threading.Lock()


def _headers(extra: dict | None = None) -> dict:
    h = {}
    if extra:
        h.update(extra)
    return h


# ── 登入 / Token ──

def _login() -> bool:
    """用帳密登入，取得 CPToken 和 RefreshToken（需在 _token_lock 內呼叫）"""
    global _cp_token, _refresh_token
    for attempt in range(2):
        try:
            resp = _client.post(
                "userlogin1",
                json={"MemId": PANASONIC_ACCOUNT, "PW": PANASONIC_PASSWORD, "AppToken": APP_TOKEN},
            )
            data = resp.json()
            _cp_token = data["CPToken"]
            _refresh_token = data["RefreshToken"]
            return True
        except Exception as e:
            if attempt == 0:
                print(f"[PANASONIC] Login failed (will retry): {e}")
                continue
            print(f"[PANASONIC] Login failed (gave up): {e}")
            return False


def _do_token_refresh() -> bool:
    """用 RefreshToken 換新的 CPToken（需在 _token_lock 內呼叫）"""
    global _cp_token, _refresh_token
    try:
        resp = _client.post(
            "RefreshToken1",
            json={"RefreshToken": _refresh_token},
        )
        data = resp.json()
        _cp_token = data["CPToken"]
        _refresh_token = data["RefreshToken"]
        return True
    except Exception as e:
        print(f"[PANASONIC] Refresh token failed: {e}")
        return False


def _ensure_token() -> bool:
    """確保 token 存在，沒有就登入"""
    if _cp_token is None:
        with _token_lock:
            if _cp_token is None:  # double-check after acquiring lock
                return _login()
    return True


def _renew_token() -> str:
    """refresh 或重新登入，回傳新 token"""
    with _token_lock:
        if not _do_token_refresh():
            _login()
        return _cp_token


def _request_with_retry(method: str, url: str, **kwargs):
    """發送請求，token 過期自動重試一次，空 response 重新登入後重試，網路錯誤亦重試一次"""
    if not _ensure_token():
        return None

    for attempt in range(2):
        try:
            # 每次用最新的 token 建 header
            if "headers" in kwargs:
                kwargs["headers"]["cptoken"] = _cp_token
            resp = _client.request(method, url, **kwargs)

            # Token 過期（417 狀態碼）：統一嘗試 refresh 後重試
            if resp.status_code == 417:
                if attempt == 0:
                    try:
                        state_msg = resp.json().get("StateMsg", "")
                    except Exception:
                        state_msg = "(empty body)"
                    print(f"[PANASONIC] 417 token error: {state_msg}, refreshing...")
                    _renew_token()
                    continue
                else:
                    print(f"[PANASONIC] 417 persists after token refresh")
                    return None

            if resp.status_code == 200:
                if not resp.text or not resp.text.strip():
                    # 空 response 可能是 token 失效，重新登入後重試
                    if attempt == 0:
                        print(f"[PANASONIC] Empty response, re-login and retry...")
                        _renew_token()
                        continue
                    print(f"[PANASONIC] Empty response persists after retry")
                    return None
                return resp.json()
            else:
                print(f"[PANASONIC] Unexpected status {resp.status_code}: {resp.text}")
                return None

        except Exception as e:
            if attempt == 0:
                print(f"[PANASONIC] Request error (will retry): {e}")
                _renew_token()
                continue
            print(f"[PANASONIC] Request error (gave up): {e}")
            return None


# ── 設備列表 ──

def get_devices() -> list:
    """取得帳號下所有設備列表"""
    data = _request_with_retry(
        "GET",
        "UserGetRegisteredGwList2",
        headers=_headers({"cptoken": _cp_token}),
    )
    if data is None:
        return []
    return data.get("GwList", [])


# ── 除濕機狀態查詢 ──

DEHUMIDIFIER_STATUS_COMMANDS = ["0x00", "0x01", "0x04", "0x09", "0x0d", "0x0e"]

def get_dehumidifier_status(device_auth: str, gwid: str) -> dict:
    """
    查詢除濕機目前狀態
    回傳 dict，key 為 CommandType，value 為目前數值
    例如：{"0x00": "1", "0x01": "1", "0x04": "3"}
    """
    commands = {
        "CommandTypes": [{"CommandType": c} for c in DEHUMIDIFIER_STATUS_COMMANDS],
        "DeviceID": 1,
    }
    data = _request_with_retry(
        "POST",
        "DeviceGetInfo",
        headers=_headers({"cptoken": _cp_token, "auth": device_auth, "gwid": gwid}),
        json=[commands],
    )
    if data is None:
        return {"error": "無法取得除濕機狀態"}

    result = {}
    try:
        device = data["devices"][0]
        for info in device["Info"]:
            result[info["CommandType"]] = info["status"]
    except Exception as e:
        return {"error": str(e)}
    return result


# ── 除濕機控制 ──

def set_dehumidifier_command(device_auth: str, gwid: str, command_type: str, value: int) -> dict:
    """
    對除濕機送出指令
    command_type: "0x00"（電源）, "0x01"（模式）, "0x04"（目標濕度）等
    value: 對應的整數值
    """
    data = _request_with_retry(
        "GET",
        "DeviceSetCommand",
        headers=_headers({"cptoken": _cp_token, "auth": device_auth, "gwid": gwid}),
        params={"DeviceID": 1, "CommandType": command_type, "Value": value},
    )
    if data is None:
        return {"success": False, "error": "指令送出失敗"}
    return {"success": True}


# ── 高階封裝：常用操作 ──

# 電源
def dehumidifier_turn_on(device_auth: str, gwid: str) -> dict:
    return set_dehumidifier_command(device_auth, gwid, "0x00", 1)

def dehumidifier_turn_off(device_auth: str, gwid: str) -> dict:
    return set_dehumidifier_command(device_auth, gwid, "0x00", 0)

# 模式
DEHUMIDIFIER_MODE_MAP = {
    "連續除濕": 0, "continuous": 0,
    "自動除濕": 1, "auto": 1,
    "防黴": 2, "anti-mildew": 2,
    "送風": 3, "fan": 3,
    "目標濕度": 6, "target": 6,
    "空氣清淨": 7, "purify": 7,
    "AI舒適": 8, "ai舒適": 8,
    "省電": 9, "eco": 9,
    "快速除濕": 10, "quick": 10,
    "靜音除濕": 11, "silent": 11,
}

def dehumidifier_set_mode(device_auth: str, gwid: str, mode_str: str) -> dict:
    mode = DEHUMIDIFIER_MODE_MAP.get(mode_str)
    if mode is None:
        return {"success": False, "error": f"不支援的模式：{mode_str}"}
    return set_dehumidifier_command(device_auth, gwid, "0x01", mode)

# 目標濕度（0x04：0=40%, 1=45%, 2=50%, 3=55%, 4=60%, 5=65%, 6=70%）
HUMIDITY_VALUE_MAP = {40: 0, 45: 1, 50: 2, 55: 3, 60: 4, 65: 5, 70: 6}

def dehumidifier_set_humidity(device_auth: str, gwid: str, humidity: int) -> dict:
    """設定目標濕度，接受 40/45/50/55/60/65/70"""
    # 找最近的支援值
    closest = min(HUMIDITY_VALUE_MAP.keys(), key=lambda x: abs(x - humidity))
    value = HUMIDITY_VALUE_MAP[closest]
    return set_dehumidifier_command(device_auth, gwid, "0x04", value)


# ── 格式化狀態為人類可讀文字 ──

POWER_MAP = {"0": "關閉", "1": "開啟"}
MODE_DISPLAY = {
    "0": "連續除濕", "1": "自動除濕", "2": "防黴", "3": "送風",
    "4": "ECONAVI", "5": "保乾", "6": "目標濕度", "7": "空氣清淨",
    "8": "AI舒適", "9": "省電", "10": "快速除濕", "11": "靜音除濕", "12": "鞋類乾燥",
}
HUMIDITY_DISPLAY = {"0": "40%", "1": "45%", "2": "50%", "3": "55%", "4": "60%", "5": "65%", "6": "70%"}

def format_dehumidifier_status(status: dict, device_name: str = "除濕機") -> str:
    if "error" in status:
        return f"❌ 無法取得{device_name}狀態：{status['error']}"
    power = POWER_MAP.get(str(status.get("0x00", "")), "未知")
    mode = MODE_DISPLAY.get(str(status.get("0x01", "")), "未知")
    humidity = HUMIDITY_DISPLAY.get(str(status.get("0x04", "")), "未設定")
    return f"💧 {device_name}：{power}｜模式：{mode}｜目標濕度：{humidity}"
