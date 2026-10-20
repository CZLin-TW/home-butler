"""Panasonic Smart App cloud: sign-in, status and commands for the dehumidifiers.

Carried over from the old client on purpose:
- One token for the process; when it expires (HTTP 417) only one thread renews it, the others reuse the
  new one. Several renewals at once make the cloud rotate tokens against each other and then block the
  account for a while.
- After 3 failed sign-ins in a row, nothing is sent for 5 minutes.
- A status read may be retried once after renewing the token. A command is retried only on an explicit
  417; an empty answer or a dropped connection after a command is "unknown" and never sent again.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("homebutler.dehumidifier")

BASE_URL = "https://ems2.panasonic.com.tw/api/"
APP_TOKEN = "D8CBFF4C-2824-4342-B22D-189166FEF503"      # the app's public identifier, not a secret
HEADERS = {"user-agent": "okhttp/4.9.1", "Content-Type": "application/json"}
FAILURE_THRESHOLD = 3
COOLDOWN_SECONDS = 300
STATUS_COMMANDS = ["0x00", "0x01", "0x04", "0x09", "0x0d", "0x0e"]

# Modes this model accepts (CommandType 0x01).
MODES = {"連續除濕": 0, "continuous": 0, "防霉抑菌": 2, "anti-mildew": 2, "目標濕度": 6, "target": 6,
         "空氣清淨": 7, "purify": 7, "AI舒適": 8, "ai舒適": 8}
MODE_LABELS = {"0": "連續除濕", "1": "自動除濕", "2": "防霉抑菌", "3": "送風", "4": "ECONAVI", "5": "保乾", "6": "目標濕度",
               "7": "空氣清淨", "8": "AI舒適", "9": "省電", "10": "快速除濕", "11": "靜音除濕", "12": "鞋類乾燥"}
# Target humidity (CommandType 0x04): 0=40% … 6=70%.
HUMIDITY_STEPS = {40: 0, 45: 1, 50: 2, 55: 3, 60: 4, 65: 5, 70: 6}
HUMIDITY_LABELS = {str(v): f"{k}%" for k, v in HUMIDITY_STEPS.items()}


class PanasonicClient:
    def __init__(self, account: str, password: str, http, clock=time.time):
        self.account, self.password, self.http, self.clock = account, password, http, clock
        self.lock = threading.Lock()
        self.token: str | None = None
        self.refresh_token: str | None = None
        self.failures = 0
        self.open_until = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.account and self.password)

    # ── token ───────────────────────────────────────────────────────────────

    def _blocked(self) -> bool:
        return self.clock() < self.open_until

    def _record(self, ok: bool) -> None:
        if ok:
            self.failures = 0
            return
        self.failures += 1
        if self.failures >= FAILURE_THRESHOLD:
            self.open_until = self.clock() + COOLDOWN_SECONDS
            log.warning("Panasonic sign-in paused for %s s after %s failures", COOLDOWN_SECONDS, self.failures)

    def _sign_in(self) -> bool:
        for attempt in range(2):
            try:
                data = self.http.post(BASE_URL + "userlogin1", headers=HEADERS,
                                      json={"MemId": self.account, "PW": self.password, "AppToken": APP_TOKEN}).json()
                self.token, self.refresh_token = data["CPToken"], data["RefreshToken"]
                return True
            except Exception as error:
                log.warning("Panasonic sign-in failed (%s)%s", type(error).__name__, ", retrying" if attempt == 0 else "")
        return False

    def _refresh(self) -> bool:
        try:
            data = self.http.post(BASE_URL + "RefreshToken1", headers=HEADERS, json={"RefreshToken": self.refresh_token}).json()
            self.token, self.refresh_token = data["CPToken"], data["RefreshToken"]
            return True
        except Exception as error:
            log.warning("Panasonic token refresh failed (%s)", type(error).__name__)
            return False

    def _ensure(self) -> bool:
        if self.token is not None:
            return True
        with self.lock:
            if self.token is not None:
                return True
            if self._blocked() or not self.configured:
                return False
            ok = self._sign_in()
            self._record(ok)
            return ok

    def _renew(self, stale: str | None) -> bool:
        with self.lock:
            if stale is not None and self.token is not None and self.token != stale:
                return True                       # another thread already renewed it
            if self._blocked():
                return False
            ok = self._refresh() or self._sign_in()
            self._record(ok)
            return ok

    # ── requests ────────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, device_headers: dict, *, command: bool, **kwargs):
        """The answer's JSON, or None when there is none (for a command: the result is unknown)."""
        if not self._ensure():
            return None
        for attempt in range(2):
            if self._blocked():
                return None
            used = self.token
            try:
                response = self.http.request(method, BASE_URL + path, headers={**HEADERS, **device_headers, "cptoken": used}, **kwargs)
            except Exception as error:
                if command or attempt == 1:
                    log.warning("Panasonic %s: no answer (%s)", path, type(error).__name__)
                    return None
                if not self._renew(used):
                    return None
                continue
            if response.status_code == 417:
                if attempt == 1 or not self._renew(used):
                    return None
                continue
            if response.status_code != 200:
                log.warning("Panasonic %s: HTTP %s", path, response.status_code)
                return None
            if not response.text.strip():
                if command or attempt == 1 or not self._renew(used):
                    return None
                continue
            return response.json()
        return None

    def status(self, auth: str, gwid: str) -> dict:
        """{CommandType: value} such as {"0x00": "1"}, or {"error": ...}."""
        body = [{"CommandTypes": [{"CommandType": c} for c in STATUS_COMMANDS], "DeviceID": 1}]
        data = self._request("POST", "DeviceGetInfo", {"auth": auth, "gwid": gwid}, command=False, json=body)
        if data is None:
            return {"error": "無法取得除濕機狀態"}
        try:
            return {info["CommandType"]: info["status"] for info in data["devices"][0]["Info"]}
        except (KeyError, IndexError, TypeError):
            return {"error": "除濕機狀態格式不符"}

    def command(self, auth: str, gwid: str, command_type: str, value: int) -> dict:
        data = self._request("GET", "DeviceSetCommand", {"auth": auth, "gwid": gwid}, command=True,
                             params={"DeviceID": 1, "CommandType": command_type, "Value": value})
        if data is None:
            return {"success": False, "error": "指令結果未確認", "uncertain": True}
        return {"success": True}

    def power(self, auth: str, gwid: str, on: bool) -> dict:
        return self.command(auth, gwid, "0x00", 1 if on else 0)

    def set_mode(self, auth: str, gwid: str, mode: str) -> dict:
        if mode not in MODES:
            return {"success": False, "error": f"不支援的模式：{mode}"}
        return self.command(auth, gwid, "0x01", MODES[mode])

    def set_humidity(self, auth: str, gwid: str, humidity: int) -> dict:
        closest = min(HUMIDITY_STEPS, key=lambda step: abs(step - int(humidity)))
        return self.command(auth, gwid, "0x04", HUMIDITY_STEPS[closest])
