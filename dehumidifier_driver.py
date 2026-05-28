"""除濕機品牌無關的控制 / 狀態 driver。

把 Panasonic（auth + gwid + CommandType 數值）與 LG（deviceId + ThinQ
property 巢狀結構）的差異收斂在這裡，讓 dehumidifier_auto 的狀態機只跟
統一介面打交道、不必知道品牌細節。

介面（每個 driver 都實作）：
- get_status() -> dict             原始狀態 dict，或 {"error": ...}
- is_power_on(status) -> bool      從原始狀態判斷電源
- read_state(status) -> dict       內部用 {"power","mode","target"}，給偏移偵測比對
                                   （mode 是 raw value，不是顯示字串）
- status_fields(status) -> dict    Dashboard 顯示用 {power, mode, targetHumidity}
                                   （mode 是中文 display 字串、targetHumidity 是 "55%"）
- fire_on(threshold) -> (ok, ok)   開機並設成持續除濕等效模式（+ LG 目標）
- fire_off()                       關機
- align_continuous(threshold)      只把模式（+LG 目標）對齊持續除濕，不動電源（建立偵測基準用）
- expected_on_state(threshold)     系統「命令開機」後預期的正規化狀態
- expected_off_state()             系統「命令關機」後預期的正規化狀態
"""

import panasonic_api
import lg_api

# Panasonic 自動模式用的持續模式：機器忽略自身目標濕度、由外部 sensor + hysteresis 控制 on/off。
PANA_CONTINUOUS_MODE = "連續除濕"


def state_diverged(expected, actual):
    """比對系統命令的狀態 vs 機器實際狀態，判斷是否被「手動介入」改過。
    - 電源不符（人手動開/關）→ True
    - 開機中模式被改 → True
    - 開機中 (LG) 目標濕度被改 → True
    無法判定的欄位（actual 缺值）一律不算偏移，避免誤判。"""
    if expected is None or actual is None:
        return False
    if expected.get("power") != actual.get("power"):
        return True
    if actual.get("power"):  # 只有開機時模式 / 目標才有意義
        em, am = expected.get("mode"), actual.get("mode")
        if em is not None and am and am != em:
            return True
        et, at = expected.get("target"), actual.get("target")
        if et is not None and at is not None and at != et:
            return True
    return False


class PanasonicDriver:
    def __init__(self, auth, gwid):
        self.auth = auth
        self.gwid = gwid

    def get_status(self):
        return panasonic_api.get_dehumidifier_status(self.auth, self.gwid)

    def is_power_on(self, status):
        return status.get("0x00") == "1"

    def read_state(self, status):
        return {
            "power": status.get("0x00") == "1",
            "mode": status.get("0x01", ""),
            "target": None,  # 連續除濕模式忽略目標濕度，不納入偵測
        }

    def status_fields(self, status):
        if not isinstance(status, dict) or "error" in status:
            return {}
        return {
            "power": status.get("0x00") == "1",
            "mode": panasonic_api.MODE_DISPLAY.get(str(status.get("0x01", "")), ""),
            "targetHumidity": panasonic_api.HUMIDITY_DISPLAY.get(str(status.get("0x04", "")), ""),
        }

    def expected_on_state(self, threshold=None):
        return {
            "power": True,
            "mode": str(panasonic_api.DEHUMIDIFIER_MODE_MAP.get(PANA_CONTINUOUS_MODE, "")),
            "target": None,
        }

    def expected_off_state(self):
        return {"power": False, "mode": None, "target": None}

    def fire_on(self, threshold=None):
        r1 = panasonic_api.dehumidifier_turn_on(self.auth, self.gwid)
        r2 = panasonic_api.dehumidifier_set_mode(self.auth, self.gwid, PANA_CONTINUOUS_MODE)
        return bool(r1.get("success")), bool(r2.get("success"))

    def fire_off(self):
        panasonic_api.dehumidifier_turn_off(self.auth, self.gwid)

    def align_continuous(self, threshold=None):
        """只把模式對齊連續除濕，不動電源（建立偵測基準用，idempotent）。"""
        panasonic_api.dehumidifier_set_mode(self.auth, self.gwid, PANA_CONTINUOUS_MODE)


class LGDriver:
    def __init__(self, device_id):
        self.device_id = device_id

    def get_status(self):
        return lg_api.get_dehumidifier_status(self.device_id)

    def is_power_on(self, status):
        return lg_api._dig(status, lg_api.POWER_NODE, lg_api.POWER_KEY) == lg_api.POWER_ON_VALUE

    def read_state(self, status):
        return {
            "power": lg_api._dig(status, lg_api.POWER_NODE, lg_api.POWER_KEY) == lg_api.POWER_ON_VALUE,
            "mode": lg_api._dig(status, lg_api.JOBMODE_NODE, lg_api.JOBMODE_KEY),
            "target": lg_api._dig(status, lg_api.HUMIDITY_NODE, lg_api.TARGET_HUMIDITY_KEY),
        }

    def status_fields(self, status):
        return lg_api.dehumidifier_status_fields(status) or {}

    def expected_on_state(self, threshold=None):
        target = lg_api.snap_humidity(threshold + lg_api.AUTO_TARGET_OFFSET) if threshold is not None else None
        return {
            "power": True,
            "mode": lg_api.DEHUMIDIFIER_MODE_MAP.get(lg_api.AUTO_CONTINUOUS_MODE),
            "target": target,
        }

    def expected_off_state(self):
        return {"power": False, "mode": None, "target": None}

    def fire_on(self, threshold=None):
        r1 = lg_api.dehumidifier_turn_on(self.device_id)
        r2 = lg_api.dehumidifier_set_mode(self.device_id, lg_api.AUTO_CONTINUOUS_MODE)
        # 機器目標 = 規則門檻 − 10%，壓在我們 OFF 門檻之下，讓 hysteresis 先 fire OFF。
        if threshold is not None:
            lg_api.dehumidifier_set_humidity(self.device_id, threshold + lg_api.AUTO_TARGET_OFFSET)
        return bool(r1.get("success")), bool(r2.get("success"))

    def fire_off(self):
        lg_api.dehumidifier_turn_off(self.device_id)

    def align_continuous(self, threshold=None):
        """只把模式 + 目標對齊（智慧除濕 + 門檻−10），不動電源（建立偵測基準用）。"""
        lg_api.dehumidifier_set_mode(self.device_id, lg_api.AUTO_CONTINUOUS_MODE)
        if threshold is not None:
            lg_api.dehumidifier_set_humidity(self.device_id, threshold + lg_api.AUTO_TARGET_OFFSET)


def make_driver(device_row):
    """依「智能居家」row 的品牌建對應 driver。必要識別碼缺漏回 None（caller skip）。"""
    brand = (device_row.get("品牌") or "Panasonic").strip()
    device_id = device_row.get("Device ID", "")
    if brand == "LG":
        if not device_id:
            return None
        return LGDriver(device_id)
    auth = device_row.get("Auth", "")
    if not auth or not device_id:
        return None
    return PanasonicDriver(auth, device_id)
