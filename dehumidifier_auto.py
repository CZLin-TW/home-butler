"""除濕機條件式自動開關 (auto mode)。

每台除濕機可獨立啟用：選一個感測器 + 持續時間 T。「目標濕度」(UI 既有
segment) 同時當作門檻：

- 運轉中 hysteresis：≥ 門檻+5 連續 T → ON，< 門檻 連續 T → OFF，
  [門檻, 門檻+5) 灰色區維持當前狀態
- Toggle 從 OFF→ON 瞬間採對稱單一門檻：sensor ≥ 門檻 立即 ON、< 門檻
  立即 OFF（不等 T，因為使用者意圖明確）

自動模式 ON 期間，這台除濕機只接受「規則本身」的控制；外部來源
（Dashboard 手動、LINE bot、排程）都會被 is_locked() 攔下。

Sensor 連續失聯：
- 6 ticks（30min）→ phase=sensor_lost_warning（LINE bot 可知）
- 12 ticks（60min）→ 自動解除 auto_mode + 關除濕機（如果開著）

Rule 設定值持久化在 Sheet「除濕機自動規則」分頁；in-memory 只放 runtime
state machine（above_since / below_since / sensor_missing_ticks），重啟
從零累積。每 5min 寫 phase / countdown 給 LINE bot 讀。
"""

import threading
import time
from datetime import datetime
from threading import Lock

import gspread

import dehumidifier_history
import panasonic_api
from sheets import _get_spreadsheet

RULES_SHEET = "除濕機自動規則"
HYSTERESIS_OFFSET = 5              # H_on = threshold + 5
SENSOR_WARNING_TICKS = 6           # 30min（6 × 5min polling）
SENSOR_DISABLE_TICKS = 12          # 60min
# 自動模式下強制走「連續除濕」：其他模式（尤其「目標濕度」）會讓除濕機看自己
# 機體周邊濕度達標就停，但機體周邊通常比房間其他位置更乾、外部 sensor 未達
# 門檻，導致永遠 trigger 不到 auto-OFF。連續除濕忽略內部判定，控制權完全
# 交給外部 sensor + 我們的 hysteresis。
AUTO_MODE_DEHUMIDIFIER_MODE = "連續除濕"

HEADERS = [
    "device_name", "auto_mode", "sensor_name", "duration_min",
    "threshold", "on_mode",
    "auto_phase", "countdown_min", "last_event", "last_event_at",
]

# auto_phase 值：
#   disabled              — auto_mode=OFF
#   idle_dry              — auto_mode ON、power OFF、感測器在灰色區或低於門檻
#   idle_humid            — auto_mode ON、power ON、感測器在灰色區或高於 H_on
#   armed_above           — 累積中，準備觸發 ON
#   armed_below           — 累積中，準備觸發 OFF
#   sensor_lost_warning   — 感測器 ≥30min 沒回報

_lock = Lock()
_state: dict = {}                  # device_name → runtime state
_rules: dict = {}                  # device_name → rule config
_cached_ws = None


def _new_runtime():
    return {"above_since": None, "below_since": None, "sensor_missing_ticks": 0}


def _round_humidity(h: float) -> int:
    """四捨五入到整數（0.5 進位）。Python 內建 round() 是 banker's rounding
    (round half to even)，60.5 會變 60 不是 61，跟「四捨五入」直覺不一致。
    sensor 小數值 vs 整數門檻比對時用這個，避免 60.0 卡點、60.1 reset 的問題。
    濕度永遠非負，這寫法安全。"""
    return int(h + 0.5)


def _ensure_sheet():
    global _cached_ws
    if _cached_ws is not None:
        return _cached_ws
    ss = _get_spreadsheet()
    try:
        ws = ss.worksheet(RULES_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=RULES_SHEET, rows=20, cols=len(HEADERS))
        ws.append_row(HEADERS, value_input_option="USER_ENTERED")
        print(f"[dehum-auto] created sheet '{RULES_SHEET}'")
    _cached_ws = ws
    return ws


def _bool(v):
    return str(v).strip().upper() in ("TRUE", "1", "YES")


# ── Public API ──────────────────────────────────────────

def load_rules():
    """Startup 載入規則。Runtime state 不存 Sheet，從零累積。"""
    try:
        ws = _ensure_sheet()
        records = ws.get_all_records()
        with _lock:
            _rules.clear()
            for r in records:
                name = r.get("device_name", "")
                if not name:
                    continue
                _rules[name] = {
                    "auto_mode": _bool(r.get("auto_mode")),
                    "sensor_name": r.get("sensor_name", ""),
                    "duration_min": int(r.get("duration_min") or 30),
                    "threshold": int(r.get("threshold") or 50),
                    "on_mode": r.get("on_mode", "目標濕度"),
                }
                _state.setdefault(name, _new_runtime())
        print(f"[dehum-auto] loaded {len(_rules)} rules from Sheet")
    except Exception as e:
        print(f"[dehum-auto] load error: {e}")


def is_locked(device_name: str) -> bool:
    """外部 caller（手動控制 / 排程 / LINE bot）呼叫前檢查。True = 拒收。"""
    with _lock:
        rule = _rules.get(device_name)
        return rule is not None and rule.get("auto_mode", False)


def get_all_rules() -> dict:
    """For API + LINE bot 讀。"""
    with _lock:
        return {
            n: {**r, "runtime": dict(_state.get(n, _new_runtime()))}
            for n, r in _rules.items()
        }


def set_rule(device_name, auto_mode, sensor_name=None, duration_min=None,
             threshold=None, on_mode=None, sensor_humidity=None,
             power_now=None, auth=None, gwid=None):
    """Dashboard 設定/更新規則。

    Toggle 從 OFF→ON 且 sensor_humidity + power_now + auth/gwid 都備齊時，
    會立即依「對稱單一門檻」規則 fire ON 或 OFF。Toggle ON→OFF 只關閉規則，
    不主動改除濕機當前狀態。"""
    now = time.time()

    with _lock:
        existing = _rules.get(device_name, {})
        old_auto = existing.get("auto_mode", False)
        rule = {
            "auto_mode": auto_mode,
            "sensor_name": sensor_name if sensor_name is not None else existing.get("sensor_name", ""),
            "duration_min": duration_min if duration_min is not None else existing.get("duration_min", 30),
            "threshold": threshold if threshold is not None else existing.get("threshold", 50),
            # on_mode 永遠強制成 AUTO_MODE_DEHUMIDIFIER_MODE，忽略 caller 傳入。
            # 保留欄位是為 Sheet schema 一致 + 將來若改成可選不同模式的彈性。
            "on_mode": AUTO_MODE_DEHUMIDIFIER_MODE,
        }
        _rules[device_name] = rule
        if old_auto != auto_mode:
            _state[device_name] = _new_runtime()

    last_event = None
    last_event_at = None
    if not old_auto and auto_mode and sensor_humidity is not None and auth and gwid:
        last_event = _toggle_on_immediate(device_name, rule, sensor_humidity, power_now, auth, gwid)
        if last_event:
            last_event_at = now

    phase = _phase_for_set(rule, sensor_humidity, power_now)
    _write_sheet(device_name, rule, phase, None, last_event, last_event_at, preserve_history=last_event is None)

    with _lock:
        return {**rule, "runtime": dict(_state.get(device_name, _new_runtime()))}


def evaluate_all(ctx, sensor_snapshot):
    """每 5min polling tick 呼叫。為每個 auto_mode=ON 的除濕機跑一次評估。"""
    with _lock:
        active = [(n, dict(r)) for n, r in _rules.items() if r.get("auto_mode")]

    if not active:
        return

    devices_by_name = {}
    for d in ctx.get("智能居家"):
        if d.get("狀態") != "啟用":
            continue
        name = d.get("名稱", "")
        if d.get("類型") == "除濕機" and name:
            devices_by_name[name] = d

    now = time.time()
    for device_name, rule in active:
        d = devices_by_name.get(device_name)
        if not d:
            print(f"[dehum-auto] {device_name} 不在「智能居家」啟用列表，skip")
            continue
        auth = d.get("Auth", "")
        gwid = d.get("Device ID", "")
        if not auth or not gwid:
            continue

        status = panasonic_api.get_dehumidifier_status(auth, gwid)
        if "error" in status:
            print(f"[dehum-auto] {device_name} status fetch: {status.get('error')}")
            continue
        power_now = status.get("0x00") == "1"

        # Record power 狀態到 history（給 Dashboard 自動模式 chart 背景畫 on segments）
        location = d.get("位置", "")
        dehumidifier_history.record(device_name, location, power_now)

        # 強制 mode invariant：Panasonic 偶發 set_mode 沒生效導致 mode 漂回使用者
        # 上次的設定，每 tick 對齊一次（power=on 才檢查，off 時 mode 無意義）
        _enforce_mode_invariant(device_name, status, auth, gwid)

        sensor = sensor_snapshot.get(rule["sensor_name"], {})
        humidity = None
        if sensor.get("online", False):
            humidity = sensor.get("current", {}).get("humidity")

        if humidity is None:
            _handle_sensor_missing(device_name, rule, power_now, auth, gwid, now)
            continue

        with _lock:
            state = _state.setdefault(device_name, _new_runtime())
            state["sensor_missing_ticks"] = 0

        _evaluate_steady(device_name, rule, humidity, power_now, auth, gwid, now)


# ── Internal evaluators ─────────────────────────────────

def _toggle_on_immediate(device_name, rule, sensor_humidity, power_now, auth, gwid):
    """Toggle 從 OFF→ON 瞬間：對稱單一門檻判斷（無 hysteresis，用 ≥ 端贏 tie）。
    sensor 四捨五入後比，跟 steady-state 一致。"""
    threshold = rule["threshold"]
    humidity_int = _round_humidity(sensor_humidity)
    if humidity_int >= threshold:
        if not power_now:
            _fire_on(device_name, rule, auth, gwid)
            return "toggled_immediate_on"
    else:
        if power_now:
            _fire_off(device_name, auth, gwid)
            return "toggled_immediate_off"
    return None


def _evaluate_steady(device_name, rule, humidity, power_now, auth, gwid, now):
    threshold = rule["threshold"]
    duration_s = rule["duration_min"] * 60
    h_on = threshold + HYSTERESIS_OFFSET
    h_off = threshold
    # 四捨五入後再比較：sensor 0.x 小數 vs 整數門檻精準匹配不會卡。
    # 例：target=60 → 59.5~60.4 都算「≤60」會累積關閉、60.5~64.4 灰色區。
    humidity_int = _round_humidity(humidity)

    fire = None
    countdown_min = None
    phase = None

    with _lock:
        state = _state.setdefault(device_name, _new_runtime())

        if humidity_int >= h_on:
            state["below_since"] = None
            if state["above_since"] is None:
                state["above_since"] = now
            if not power_now:
                elapsed = now - state["above_since"]
                if elapsed >= duration_s:
                    fire = "on"
                    state["above_since"] = None
                else:
                    countdown_min = int((duration_s - elapsed) // 60)
                    phase = "armed_above"
            else:
                phase = "idle_humid"
        elif humidity_int <= h_off:
            # 改用 <=（含等於）：sensor 剛好等於 target 也算「夠乾」進關閉累積。
            # 原本用 < 時剛好打到 60.0 會落到灰色區、下個 tick 60.1 又 reset，不直覺。
            state["above_since"] = None
            if state["below_since"] is None:
                state["below_since"] = now
            if power_now:
                elapsed = now - state["below_since"]
                if elapsed >= duration_s:
                    fire = "off"
                    state["below_since"] = None
                else:
                    countdown_min = int((duration_s - elapsed) // 60)
                    phase = "armed_below"
            else:
                phase = "idle_dry"
        else:
            # 灰色區 (threshold, threshold+5)：reset 計時器，維持當前 power
            state["above_since"] = None
            state["below_since"] = None
            phase = "idle_humid" if power_now else "idle_dry"

    last_event = None
    if fire == "on":
        _fire_on(device_name, rule, auth, gwid)
        last_event = "triggered_on"
        phase = "idle_humid"
    elif fire == "off":
        _fire_off(device_name, auth, gwid)
        last_event = "triggered_off"
        phase = "idle_dry"

    _write_sheet(
        device_name, rule, phase, countdown_min,
        last_event, now if last_event else None,
        preserve_history=last_event is None,
    )


def _handle_sensor_missing(device_name, rule, power_now, auth, gwid, now):
    with _lock:
        state = _state.setdefault(device_name, _new_runtime())
        state["sensor_missing_ticks"] += 1
        missing = state["sensor_missing_ticks"]

    if missing >= SENSOR_DISABLE_TICKS:
        _force_disable(device_name, rule, power_now, auth, gwid, now)
    elif missing >= SENSOR_WARNING_TICKS:
        _write_sheet(
            device_name, rule, "sensor_lost_warning", None,
            None, None, preserve_history=True,
        )


def _force_disable(device_name, rule, power_now, auth, gwid, now):
    """60min sensor 失聯：解除 auto_mode + 關除濕機（若開著）。"""
    with _lock:
        _rules[device_name]["auto_mode"] = False
        _state[device_name] = _new_runtime()

    if power_now:
        _fire_off(device_name, auth, gwid)

    rule_after = {**rule, "auto_mode": False}
    _write_sheet(
        device_name, rule_after, "disabled", None,
        "auto_disabled_sensor_lost", now, preserve_history=False,
    )
    print(f"[dehum-auto] AUTO DISABLED {device_name}: sensor lost ≥60min")


# ── Phase computation ──────────────────────────────────

def _phase_for_set(rule, sensor_humidity, power_now):
    """set_rule 後計算當下 phase。下個 tick evaluate_all 會覆寫成更精準的值。
    四捨五入規則同 _evaluate_steady。"""
    if not rule.get("auto_mode"):
        return "disabled"
    if sensor_humidity is None:
        return "sensor_lost_warning"
    threshold = rule["threshold"]
    h_on = threshold + HYSTERESIS_OFFSET
    humidity_int = _round_humidity(sensor_humidity)
    if humidity_int >= h_on:
        return "idle_humid" if power_now else "armed_above"
    elif humidity_int <= threshold:
        return "armed_below" if power_now else "idle_dry"
    else:
        return "idle_humid" if power_now else "idle_dry"


# ── Panasonic API wrappers ─────────────────────────────

def _fire_on(device_name, rule, auth, gwid):
    """送開機 → 設目標濕度 → 強制設「連續除濕」。

    指令順序刻意是「humidity 在 mode 之前」，不是疏失：set_humidity (0x04) 在
    Panasonic 機體會有 side-effect 把 mode (0x01) 撥回「目標濕度」(6)——大概是
    因為「設定目標濕度」這個動作本身在面板上隱含切換到該模式。如果反過來
    先 set_mode 再 set_humidity，最終 mode 會落在「目標濕度」而非「連續除濕」，
    auto rule 就會壞掉（機體會看自己周邊濕度自己決定停機，無視外部 sensor）。

    擺成「set_mode 最後送」確保最終 mode 一定是連續除濕，set_humidity 的副作用
    被下一個指令蓋掉。0x04 仍然有寫入，給 Dashboard 顯示「auto rule 的 threshold」
    讀。`_enforce_mode_invariant` 留著當保險絲——cover Panasonic 真的偶發
    set_mode 失效（少數情況）。

    檢查每個指令的 success flag，failure log 出來方便事後追蹤（Panasonic API
    偶發 200 OK 但沒真的執行）。任一指令真的拋 exception 才走 except。
    """
    try:
        r1 = panasonic_api.dehumidifier_turn_on(auth, gwid)
        # 順序敏感：humidity 先送，mode 後送。詳見 docstring。
        r2 = panasonic_api.dehumidifier_set_humidity(auth, gwid, rule["threshold"])
        r3 = panasonic_api.dehumidifier_set_mode(auth, gwid, AUTO_MODE_DEHUMIDIFIER_MODE)
        ok_all = r1.get("success") and r2.get("success") and r3.get("success")
        if ok_all:
            print(f"[dehum-auto] FIRE ON {device_name}: 連續除濕 target={rule['threshold']}")
        else:
            print(
                f"[dehum-auto] FIRE ON {device_name} partial: "
                f"turn_on={r1.get('success')} set_humidity={r2.get('success')} "
                f"set_mode={r3.get('success')} target={rule['threshold']}"
            )
    except Exception as e:
        print(f"[dehum-auto] fire on {device_name} error: {e}")


def _enforce_mode_invariant(device_name, status, auth, gwid):
    """Auto mode 期間若除濕機 mode != 連續除濕，重送一次 set_mode 矯正。

    保險絲，cover 兩種情況：
    1. Panasonic API 偶發 set_mode 200 OK 但沒真的執行（少數）
    2. 自動規則 ON 期間，使用者繞過 lock 從機體面板直接改了模式（罕見）

    Common case「fire_on 之後 set_humidity 把 mode 撥回目標濕度」已經在
    `_fire_on` 用「set_mode 最後送」處理掉了，這裡只負責殘餘異常。

    每 polling tick 強制 invariant。power=off 時 mode 無意義不檢查。
    Idempotent：已是連續除濕也送不會壞。
    """
    if status.get("0x00") != "1":
        return  # power off，下次 fire_on 才會帶 mode
    current_mode = status.get("0x01", "")
    expected_mode = panasonic_api.DEHUMIDIFIER_MODE_MAP.get(AUTO_MODE_DEHUMIDIFIER_MODE)
    if expected_mode is None:
        return  # config 錯誤，skip
    if current_mode != str(expected_mode):
        print(
            f"[dehum-auto] mode drift on {device_name}: code={current_mode!r} != "
            f"{expected_mode}（連續除濕），重新套用"
        )
        panasonic_api.dehumidifier_set_mode(auth, gwid, AUTO_MODE_DEHUMIDIFIER_MODE)


def _fire_off(device_name, auth, gwid):
    try:
        panasonic_api.dehumidifier_turn_off(auth, gwid)
        print(f"[dehum-auto] FIRE OFF {device_name}")
    except Exception as e:
        print(f"[dehum-auto] fire off {device_name} error: {e}")


# ── Sheet I/O ──────────────────────────────────────────

def _write_sheet(device_name, rule, phase, countdown_min,
                 last_event, last_event_at, preserve_history):
    """更新該 device 的 Sheet 那一列（找不到就 append）。

    preserve_history=True 時 last_event / last_event_at 沒提供就沿用舊值，
    避免每 tick 把事件記錄洗掉。"""
    try:
        ws = _ensure_sheet()
        records = ws.get_all_records()
        target_row = None
        existing_event = ""
        existing_event_at = ""
        for i, r in enumerate(records):
            if r.get("device_name") == device_name:
                target_row = i + 2   # +1 for header, +1 to 1-indexed
                existing_event = r.get("last_event", "")
                existing_event_at = r.get("last_event_at", "")
                break

        if last_event:
            event_str = last_event
            event_at_str = datetime.fromtimestamp(last_event_at).strftime("%Y-%m-%d %H:%M:%S")
        elif preserve_history:
            event_str = existing_event
            event_at_str = existing_event_at
        else:
            event_str = ""
            event_at_str = ""

        row = [
            device_name,
            "TRUE" if rule.get("auto_mode") else "FALSE",
            rule.get("sensor_name", ""),
            rule.get("duration_min", 30),
            rule.get("threshold", 50),
            rule.get("on_mode", ""),
            phase or "disabled",
            countdown_min if countdown_min is not None else "",
            event_str,
            event_at_str,
        ]

        if target_row:
            last_col = chr(ord("A") + len(HEADERS) - 1)
            ws.update(
                f"A{target_row}:{last_col}{target_row}",
                [row], value_input_option="USER_ENTERED",
            )
        else:
            ws.append_row(row, value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"[dehum-auto] sheet write error: {e}")
