"""除濕機品牌無關的控制 / 狀態 driver。

把 Panasonic（auth + gwid + CommandType 數值）與 LG（deviceId + ThinQ
property 巢狀結構）的差異收斂在這裡，讓 dehumidifier_auto 的狀態機只跟
統一介面打交道、不必知道品牌細節。

介面（每個 driver 都實作）：
- get_status() -> dict          原始狀態 dict，或 {"error": ...}
- is_power_on(status) -> bool    從原始狀態判斷電源
- fire_on() -> (turn_on_ok, set_mode_ok)   開機並設成「持續除濕」等效模式
- fire_off()                     關機
- enforce_continuous(device_name, status)  若模式漂掉就重新套用持續除濕
"""

import panasonic_api
import lg_api

# Panasonic 自動模式用的持續模式：機器忽略自身目標濕度、由外部 sensor + hysteresis 控制 on/off。
PANA_CONTINUOUS_MODE = "連續除濕"


class PanasonicDriver:
    def __init__(self, auth, gwid):
        self.auth = auth
        self.gwid = gwid

    def get_status(self):
        return panasonic_api.get_dehumidifier_status(self.auth, self.gwid)

    def is_power_on(self, status):
        return status.get("0x00") == "1"

    def fire_on(self, threshold=None):
        r1 = panasonic_api.dehumidifier_turn_on(self.auth, self.gwid)
        r2 = panasonic_api.dehumidifier_set_mode(self.auth, self.gwid, PANA_CONTINUOUS_MODE)
        return bool(r1.get("success")), bool(r2.get("success"))

    def fire_off(self):
        panasonic_api.dehumidifier_turn_off(self.auth, self.gwid)

    def enforce_continuous(self, device_name, status, threshold=None):
        """Panasonic API 偶發 set_mode 沒生效、mode 漂回使用者上次手動值，每 tick 矯正。
        power=off 時 mode 無意義不檢查。Idempotent。threshold 不適用（Panasonic 連續除濕
        不下發目標濕度）。"""
        if status.get("0x00") != "1":
            return
        current_mode = status.get("0x01", "")
        expected_mode = panasonic_api.DEHUMIDIFIER_MODE_MAP.get(PANA_CONTINUOUS_MODE)
        if expected_mode is None:
            return
        if current_mode != str(expected_mode):
            print(
                f"[dehum-auto] mode drift on {device_name} (Panasonic): "
                f"code={current_mode!r} != {expected_mode}（{PANA_CONTINUOUS_MODE}），重新套用"
            )
            panasonic_api.dehumidifier_set_mode(self.auth, self.gwid, PANA_CONTINUOUS_MODE)


class LGDriver:
    def __init__(self, device_id):
        self.device_id = device_id

    def get_status(self):
        return lg_api.get_dehumidifier_status(self.device_id)

    def is_power_on(self, status):
        return lg_api._dig(status, lg_api.POWER_NODE, lg_api.POWER_KEY) == lg_api.POWER_ON_VALUE

    def fire_on(self, threshold=None):
        r1 = lg_api.dehumidifier_turn_on(self.device_id)
        r2 = lg_api.dehumidifier_set_mode(self.device_id, lg_api.AUTO_CONTINUOUS_MODE)
        # 機器目標 = 規則門檻 − 10%，壓在我們 OFF 門檻之下，讓 hysteresis 先 fire OFF、
        # 機器不會自己先達標停機。set_humidity 內含 snap/clamp 到合法範圍。
        if threshold is not None:
            lg_api.dehumidifier_set_humidity(self.device_id, threshold + lg_api.AUTO_TARGET_OFFSET)
        return bool(r1.get("success")), bool(r2.get("success"))

    def fire_off(self):
        lg_api.dehumidifier_turn_off(self.device_id)

    def enforce_continuous(self, device_name, status, threshold=None):
        """每 tick 對齊：模式 = 智慧除濕、目標濕度 = 門檻−10%。涵蓋「auto 啟用時機器
        本來就開著」沒走 fire_on 的情況，確保策略一定生效。只在偏離時才送 API。"""
        if lg_api._dig(status, lg_api.POWER_NODE, lg_api.POWER_KEY) != lg_api.POWER_ON_VALUE:
            return
        current = lg_api._dig(status, lg_api.JOBMODE_NODE, lg_api.JOBMODE_KEY)
        expected = lg_api.DEHUMIDIFIER_MODE_MAP.get(lg_api.AUTO_CONTINUOUS_MODE)
        if expected and current != expected:
            print(
                f"[dehum-auto] mode drift on {device_name} (LG): "
                f"{current!r} != {expected}（{lg_api.AUTO_CONTINUOUS_MODE}），重新套用"
            )
            lg_api.dehumidifier_set_mode(self.device_id, lg_api.AUTO_CONTINUOUS_MODE)
        if threshold is not None:
            desired = lg_api.snap_humidity(threshold + lg_api.AUTO_TARGET_OFFSET)
            current_target = lg_api._dig(status, lg_api.HUMIDITY_NODE, lg_api.TARGET_HUMIDITY_KEY)
            if current_target != desired:
                print(f"[dehum-auto] target drift on {device_name} (LG): {current_target} != {desired}，重新套用")
                lg_api.dehumidifier_set_humidity(self.device_id, desired)


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
