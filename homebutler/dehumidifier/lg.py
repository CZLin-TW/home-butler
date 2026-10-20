"""LG ThinQ Connect (the official API) for the dehumidifiers, with a personal access token.

After 3 failures in a row nothing is sent for 5 minutes, so an LG outage never slows the rest. A command
whose answer was lost is "unknown" and never sent again.
"""
from __future__ import annotations

import base64
import threading
import time
import uuid

API_KEY = "v6GFvkweNo7DK7yD3ylIZ9w52aKBU0eJ7wLXkSR3"     # ThinQ's public client key, built into its SDK
FAILURE_THRESHOLD = 3
COOLDOWN_SECONDS = 300
REGIONS = {"KIC": "https://api-kic.lgthinq.com", "AIC": "https://api-aic.lgthinq.com", "EIC": "https://api-eic.lgthinq.com"}
EUROPE = {"AL", "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IS", "IE", "IT", "LV", "LT",
          "LU", "MT", "NL", "NO", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "CH", "GB", "UK"}

# Calibrated on DHUM_056905_WW (2026-05).
POWER = ("operation", "dehumidifierOperationMode")
POWER_ON, POWER_OFF = "POWER_ON", "POWER_OFF"
TARGET = ("humidity", "targetHumidity")
CURRENT = ("humidity", "currentHumidity")
JOB_MODE = ("dehumidifierJobMode", "currentJobMode")
TARGET_MIN, TARGET_MAX, TARGET_STEP = 30, 70, 5
MODES = {
    "空氣清淨": "AIR_CLEAN", "清淨": "AIR_CLEAN", "強力除濕": "INTENSIVE_DRY", "集中除濕": "INTENSIVE_DRY",
    "快速除濕": "RAPID_HUMIDITY", "快速": "RAPID_HUMIDITY", "衣物乾燥": "CLOTHES_DRY", "乾衣": "CLOTHES_DRY",
    "智慧除濕": "SMART_HUMIDITY", "自動除濕": "SMART_HUMIDITY", "自動": "SMART_HUMIDITY", "靜音除濕": "QUIET_HUMIDITY", "靜音": "QUIET_HUMIDITY",
}
MODE_LABELS = {"AIR_CLEAN": "空氣清淨", "INTENSIVE_DRY": "強力除濕", "RAPID_HUMIDITY": "快速除濕", "CLOTHES_DRY": "衣物乾燥",
               "SMART_HUMIDITY": "智慧除濕", "QUIET_HUMIDITY": "靜音除濕"}


def endpoint(country: str, base: str = "") -> str:
    if base:
        return base.rstrip("/")
    country = (country or "TW").upper()
    return REGIONS["KIC" if country == "KR" else "EIC" if country in EUROPE else "AIC"]


def dig(state, node_key):
    """A value from ThinQ's state, which sometimes nests it under the node and sometimes not."""
    node, key = node_key
    if not isinstance(state, dict):
        return None
    inner = state.get(node)
    if isinstance(inner, dict) and key in inner:
        return inner[key]
    return state.get(key)


def snap(humidity) -> int:
    """To the machine's 5% steps within 30–70."""
    value = int(round(int(humidity) / TARGET_STEP) * TARGET_STEP)
    return max(TARGET_MIN, min(TARGET_MAX, value))


class LGClient:
    def __init__(self, token: str, country: str, client_id: str, http, base: str = "", clock=time.time):
        self.token, self.country, self.client_id, self.http, self.clock = token, (country or "TW").upper(), client_id, http, clock
        self.base = endpoint(country, base)
        self.lock = threading.Lock()
        self.failures = 0
        self.open_until = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _record(self, ok: bool) -> None:
        with self.lock:
            if ok:
                self.failures = 0
                return
            self.failures += 1
            if self.failures >= FAILURE_THRESHOLD:
                self.open_until = self.clock() + COOLDOWN_SECONDS

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "x-country": self.country,
                "x-message-id": base64.urlsafe_b64encode(uuid.uuid4().bytes).decode().rstrip("="),
                "x-client-id": self.client_id, "x-api-key": API_KEY, "x-service-phase": "OP",
                "x-user-agent": "home-butler", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, body: dict | None = None):
        if not self.token:
            return {"error": "LG 未設定存取權杖"}
        if self.clock() < self.open_until:
            return {"error": "LG ThinQ 暫時不可用（連續失敗冷卻中）"}
        try:
            response = self.http.request(method, self.base + path, headers=self._headers(), json=body)
            data = response.json()
        except Exception as error:
            self._record(False)
            return {"error": f"LG ThinQ 沒有回應（{type(error).__name__}）", "uncertain": True}
        if response.status_code != 200 or (isinstance(data, dict) and data.get("error")):
            self._record(False)
            return {"error": f"LG ThinQ 拒絕（HTTP {response.status_code}）"}
        self._record(True)
        return data["response"] if isinstance(data, dict) and "response" in data else data

    def status(self, device_id: str) -> dict:
        found = self._request("GET", f"/devices/{device_id}/state")
        return found if isinstance(found, dict) else {"error": "LG 狀態格式不符"}

    def _control(self, device_id: str, body: dict) -> dict:
        result = self._request("POST", f"/devices/{device_id}/control", body)
        if isinstance(result, dict) and "error" in result:
            return {"success": False, "error": result["error"], "uncertain": result.get("uncertain", False)}
        return {"success": True}

    def power(self, device_id: str, on: bool) -> dict:
        return self._control(device_id, {POWER[0]: {POWER[1]: POWER_ON if on else POWER_OFF}})

    def set_mode(self, device_id: str, mode: str) -> dict:
        if mode not in MODES:
            return {"success": False, "error": f"不支援的模式：{mode}"}
        return self._control(device_id, {JOB_MODE[0]: {JOB_MODE[1]: MODES[mode]}})

    def set_humidity(self, device_id: str, humidity: int) -> dict:
        return self._control(device_id, {TARGET[0]: {TARGET[1]: snap(humidity)}})
