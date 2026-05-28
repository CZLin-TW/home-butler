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
import time

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

# Token 快取（服務運行期間保持登入狀態）。
# 同一個 lock 同時保護 token 跟認證熔斷狀態。
_cp_token = None
_refresh_token = None
_token_lock = threading.Lock()

# 認證熔斷器：多台 Panasonic 並發時 token 會 race（兩 thread 同時 417 → 各自 refresh
# → 雲端 rotate token 互相 invalidate → 雪崩）。Panasonic 雲端對短時間連續 refresh
# 會觸發風控（refresh / login 全 timeout、回空 body）。連續 N 次 auth 失敗就冷卻 M 秒
# fast-fail，等雲端風控自然解除。
_auth_failures = 0
_auth_open_until = 0.0
AUTH_FAILURE_THRESHOLD = 3
AUTH_COOLDOWN_SEC = 300


def _circuit_open() -> bool:
    return time.time() < _auth_open_until


def _record_auth_result(ok: bool) -> None:
    """在 _token_lock 內呼叫。"""
    global _auth_failures, _auth_open_until
    if ok:
        _auth_failures = 0
        return
    _auth_failures += 1
    if _auth_failures >= AUTH_FAILURE_THRESHOLD:
        _auth_open_until = time.time() + AUTH_COOLDOWN_SEC
        print(
            f"[PANASONIC] auth circuit OPEN for {AUTH_COOLDOWN_SEC}s "
            f"after {_auth_failures} consecutive failures"
        )


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
    """確保 token 存在，沒有就登入。熔斷開啟時 fast-fail。"""
    if _cp_token is not None:
        return True
    with _token_lock:
        if _cp_token is not None:  # double-check after acquiring lock
            return True
        if _circuit_open():
            return False
        ok = _login()
        _record_auth_result(ok)
        return ok


def _renew_token(stale_token: str | None = None) -> str | None:
    """refresh + fallback login，回傳新 token；失敗或熔斷時回 None。

    stale_token: caller 本次 request 用的 token。進 lock 後若 _cp_token 已不等於
    stale_token，表示已被其他 thread refresh 過 — 直接吃白食、不再 refresh，避免
    thundering herd 把 Panasonic 雲端打爆（雲端會 rotate token、互相 invalidate）。
    """
    with _token_lock:
        if stale_token is not None and _cp_token is not None and _cp_token != stale_token:
            return _cp_token
        if _circuit_open():
            return None
        ok = _do_token_refresh() or _login()
        _record_auth_result(ok)
        return _cp_token if ok else None


def _request_with_retry(method: str, url: str, **kwargs):
    """發送請求，token 過期自動 refresh 重試一次。熔斷開啟或 renew 失敗時直接
    return None，不再無限 hammer Panasonic 雲端。"""
    if not _ensure_token():
        return None

    for attempt in range(2):
        if _circuit_open():
            return None
        # 記下本次用的 token，作為 stale_token 傳給 _renew_token 比對，
        # 避免多 thread 同時 417 各自 refresh 把 token 互相 rotate 失效。
        used_token = _cp_token
        try:
            if "headers" in kwargs:
                kwargs["headers"]["cptoken"] = used_token
            resp = _client.request(method, url, **kwargs)

            # Token 過期（417 狀態碼）：統一嘗試 refresh 後重試
            if resp.status_code == 417:
                if attempt == 0:
                    try:
                        state_msg = resp.json().get("StateMsg", "")
                    except Exception:
                        state_msg = "(empty body)"
                    print(f"[PANASONIC] 417 token error: {state_msg}, refreshing...")
                    if _renew_token(stale_token=used_token) is None:
                        return None
                    continue
                else:
                    print(f"[PANASONIC] 417 persists after token refresh")
                    return None

            if resp.status_code == 200:
                if not resp.text or not resp.text.strip():
                    # 空 response 可能是 token 失效，重新登入後重試
                    if attempt == 0:
                        print(f"[PANASONIC] Empty response, re-login and retry...")
                        if _renew_token(stale_token=used_token) is None:
                            return None
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
                if _renew_token(stale_token=used_token) is None:
                    return None
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


def get_dehumidifier_full_status(device_auth: str, gwid: str) -> dict:
    """Debug 用：掃 0x00 ~ 0x1F 所有 CommandType。
    用來找未知欄位（例如風量、風向）對應哪個 CommandType。
    Panasonic 對不存在的 CommandType 通常不回傳該 key，所以結果只含實際存在的。"""
    all_commands = [f"0x{i:02x}" for i in range(0x20)]
    commands = {
        "CommandTypes": [{"CommandType": c} for c in all_commands],
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
