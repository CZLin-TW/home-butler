"""除濕機條件式自動開關 (auto mode)。

每台除濕機可獨立啟用：選一個感測器 + 持續時間 T + 濕度門檻 threshold（UI
獨立 dropdown，跟除濕機機體的目標濕度設定分離）。

- 運轉中 hysteresis（不對稱、置中 threshold）：
    H_on  = threshold + 2   ≥ 連續 T → ON
    H_off = threshold − 1   ≤ 連續 T → OFF
    (H_off, H_on) 灰色區維持當前狀態
  例：threshold=60 → 實際控制帶 59~62、置中接近 60。
- Toggle 從 OFF→ON 瞬間採對稱單一門檻：sensor ≥ threshold 立即 ON、
  < threshold 立即 OFF（不等 T，因為使用者意圖明確）

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
import device_status
import dehumidifier_driver
from sheets import _get_spreadsheet

RULES_SHEET = "除濕機自動規則"
HYSTERESIS_ABOVE = 2               # H_on  = threshold + 2
HYSTERESIS_BELOW = 1               # H_off = threshold − 1
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
    return {"above_since": None, "below_since": None, "sensor_missing_ticks": 0, "expected": None}


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
                    "duration_min": int(
                        30 if r.get("duration_min") in (None, "") else r.get("duration_min")
                    ),
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


def _public_rule(rule: dict, runtime: dict) -> dict:
    """Attach effective humidity thresholds for API clients."""
    threshold = rule.get("threshold", 50)
    return {
        **rule,
        "humidity_on_threshold": threshold + HYSTERESIS_ABOVE,
        "humidity_off_threshold": threshold - HYSTERESIS_BELOW,
        "runtime": dict(runtime),
    }


def get_all_rules() -> dict:
    """For API + LINE bot 讀。"""
    with _lock:
        return {
            n: _public_rule(r, _state.get(n, _new_runtime()))
            for n, r in _rules.items()
        }


def set_rule(device_name, auto_mode, sensor_name=None, duration_min=None,
             threshold=None, on_mode=None, sensor_humidity=None,
             power_now=None, driver=None):
    """Dashboard 設定/更新規則。

    Toggle 從 OFF→ON 且 sensor_humidity + power_now + driver 都備齊時，
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
    if not old_auto and auto_mode and sensor_humidity is not None and driver is not None:
        last_event = _toggle_on_immediate(device_name, rule, sensor_humidity, power_now, driver)
        if last_event:
            last_event_at = now

    phase = _phase_for_set(rule, sensor_humidity, power_now)
    _write_sheet(device_name, rule, phase, None, last_event, last_event_at, preserve_history=last_event is None)

    with _lock:
        return _public_rule(rule, _state.get(device_name, _new_runtime()))


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
        # per-device 例外隔離：單台殘留逃逸路徑（如 update_cell quota 例外）不影響
        # 其它台這個 tick 的評估與 countdown。
        try:
            _evaluate_one_device(device_name, rule, d, sensor_snapshot, now)
        except Exception as e:
            print(f"[dehum-auto] {device_name} evaluate error: {e}")
            continue


def _evaluate_one_device(device_name, rule, d, sensor_snapshot, now):
    """單台除濕機一個 tick 的評估（從 evaluate_all 抽出，方便 per-device 例外隔離）。"""
    driver = dehumidifier_driver.make_driver(d)
    if driver is None:
        print(f"[dehum-auto] {device_name} 缺少品牌所需識別碼（Panasonic 需 Auth+Device ID / LG 需 Device ID），skip")
        return

    status = driver.get_status()
    if not isinstance(status, dict) or "error" in status:
        err = status.get("error") if isinstance(status, dict) else status
        print(f"[dehum-auto] {device_name} status fetch: {err}")
        return
    power_now = driver.is_power_on(status)
    device_status.update(device_name, driver.status_fields(status))

    # Record power 狀態到 history（給 Dashboard 自動模式 chart 背景畫 on segments）
    location = d.get("位置", "")
    dehumidifier_history.record(device_name, location, power_now)

    # 手動介入偵測：比對機器實際狀態 vs 系統命令的基準狀態。
    # 模式被改 / 電源跟預期不符 → 視為使用者手動接管 → 解除自動模式、不動機器。
    with _lock:
        state = _state.setdefault(device_name, _new_runtime())
        expected = state.get("expected")
    actual = driver.read_state(status)

    if expected is None:
        # 首次 / 重啟後：建立基準。機器開著就把模式對齊自動規則指定模式，
        # 記下 expected，本 tick 不做偵測（使用者確認的「第一個 tick 跳過」）。
        if actual.get("power"):
            try:
                driver.align_continuous(rule["threshold"])
            except Exception as e:
                print(f"[dehum-auto] {device_name} align baseline error: {e}")
            new_expected = driver.expected_on_state(rule["threshold"])
        else:
            new_expected = driver.expected_off_state()
        with _lock:
            _state.setdefault(device_name, _new_runtime())["expected"] = new_expected
    elif dehumidifier_driver.state_diverged(expected, actual):
        print(f"[dehum-auto] {device_name} 偵測到手動變更：expected={expected} actual={actual}")
        _disable_due_to_manual(device_name, rule, now)
        return

    sensor = sensor_snapshot.get(rule["sensor_name"], {})
    humidity = None
    if sensor.get("online", False):
        humidity = sensor.get("current", {}).get("humidity")

    if humidity is None:
        _handle_sensor_missing(device_name, rule, power_now, driver, now)
        return

    with _lock:
        state = _state.setdefault(device_name, _new_runtime())
        state["sensor_missing_ticks"] = 0

    _evaluate_steady(device_name, rule, humidity, power_now, driver, now)


# ── Internal evaluators ─────────────────────────────────

def _toggle_on_immediate(device_name, rule, sensor_humidity, power_now, driver):
    """Toggle 從 OFF→ON 瞬間：對稱單一門檻判斷（無 hysteresis，用 ≥ 端贏 tie）。
    直接比較 sensor 原始濕度，跟 steady-state 一致。"""
    threshold = rule["threshold"]
    humidity_value = float(sensor_humidity)
    if humidity_value >= threshold:
        if not power_now:
            _fire_on(device_name, rule, driver)
            return "toggled_immediate_on"
    else:
        if power_now:
            _fire_off(device_name, driver)
            return "toggled_immediate_off"
    return None


def _evaluate_steady(device_name, rule, humidity, power_now, driver, now):
    threshold = rule["threshold"]
    duration_s = rule["duration_min"] * 60
    h_on = threshold + HYSTERESIS_ABOVE
    h_off = threshold - HYSTERESIS_BELOW
    # 直接比較 sensor 原始濕度，讓觸發點與 API / Dashboard 顯示一致。
    # 例：target=60 → <=59 累積關閉、59<humidity<62 維持、>=62 累積開啟。
    humidity_value = float(humidity)

    fire = None
    countdown_min = None
    phase = None

    with _lock:
        state = _state.setdefault(device_name, _new_runtime())

        if humidity_value >= h_on:
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
        elif humidity_value <= h_off:
            # 邊界用 <=（含等於）：剛好打到 h_off 就進關閉累積；h_on 同樣含等於。
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
            # 灰色區 (h_off, h_on) = (threshold−1, threshold+2)：
            # reset 計時器，維持當前 power
            state["above_since"] = None
            state["below_since"] = None
            phase = "idle_humid" if power_now else "idle_dry"

    last_event = None
    if fire == "on":
        _fire_on(device_name, rule, driver)
        last_event = "triggered_on"
        phase = "idle_humid"
    elif fire == "off":
        _fire_off(device_name, driver)
        last_event = "triggered_off"
        phase = "idle_dry"

    _write_sheet(
        device_name, rule, phase, countdown_min,
        last_event, now if last_event else None,
        preserve_history=last_event is None,
    )


def _handle_sensor_missing(device_name, rule, power_now, driver, now):
    with _lock:
        state = _state.setdefault(device_name, _new_runtime())
        state["sensor_missing_ticks"] += 1
        missing = state["sensor_missing_ticks"]

    if missing >= SENSOR_DISABLE_TICKS:
        _force_disable(device_name, rule, power_now, driver, now)
    elif missing >= SENSOR_WARNING_TICKS:
        _write_sheet(
            device_name, rule, "sensor_lost_warning", None,
            None, None, preserve_history=True,
        )


def _force_disable(device_name, rule, power_now, driver, now):
    """60min sensor 失聯：解除 auto_mode + 關除濕機（若開著）。"""
    with _lock:
        _rules[device_name]["auto_mode"] = False
        _state[device_name] = _new_runtime()

    if power_now:
        _fire_off(device_name, driver)

    rule_after = {**rule, "auto_mode": False}
    _write_sheet(
        device_name, rule_after, "disabled", None,
        "auto_disabled_sensor_lost", now, preserve_history=False,
    )
    print(f"[dehum-auto] AUTO DISABLED {device_name}: sensor lost ≥60min")


def _disable_due_to_manual(device_name, rule, now):
    """偵測到使用者手動改了除濕機（模式 / 目標 / 電源）→ 解除自動模式。
    刻意「不動機器」——尊重使用者剛手動設定的狀態，只關掉自動規則並標記。"""
    with _lock:
        _rules[device_name]["auto_mode"] = False
        _state[device_name] = _new_runtime()

    rule_after = {**rule, "auto_mode": False}
    _write_sheet(
        device_name, rule_after, "disabled", None,
        "auto_disabled_manual", now, preserve_history=False,
    )
    print(f"[dehum-auto] AUTO DISABLED {device_name}: 偵測到手動操作（不動機器）")


# ── Phase computation ──────────────────────────────────

def _phase_for_set(rule, sensor_humidity, power_now):
    """set_rule 後計算當下 phase。下個 tick evaluate_all 會覆寫成更精準的值。
    原始濕度比較規則同 _evaluate_steady。"""
    if not rule.get("auto_mode"):
        return "disabled"
    if sensor_humidity is None:
        return "sensor_lost_warning"
    threshold = rule["threshold"]
    h_on = threshold + HYSTERESIS_ABOVE
    h_off = threshold - HYSTERESIS_BELOW
    humidity_value = float(sensor_humidity)
    if humidity_value >= h_on:
        return "idle_humid" if power_now else "armed_above"
    elif humidity_value <= h_off:
        return "armed_below" if power_now else "idle_dry"
    else:
        return "idle_humid" if power_now else "idle_dry"


# ── Fire helpers（品牌差異交給 driver） ─────────────────

def _fire_on(device_name, rule, driver):
    """送開機 → 設成持續除濕等效模式（機器忽略自身目標濕度，threshold 只是本規則的
    判斷門檻、不下發到機器）。品牌差異收斂在 driver.fire_on()。
    任一指令真的拋 exception 才走 except；success flag 區分「呼叫到了」vs「沒生效」。"""
    fully_ok = False
    try:
        turn_on_ok, set_mode_ok = driver.fire_on(rule["threshold"])
        fully_ok = bool(turn_on_ok and set_mode_ok)
        if fully_ok:
            print(f"[dehum-auto] FIRE ON {device_name}: 持續除濕 threshold={rule['threshold']}")
        else:
            print(
                f"[dehum-auto] FIRE ON {device_name} partial: "
                f"turn_on={turn_on_ok} set_mode={set_mode_ok} threshold={rule['threshold']}"
            )
    except Exception as e:
        print(f"[dehum-auto] fire on {device_name} error: {e}")
    # 記下系統命令的基準狀態，供下個 tick 偵測手動介入。
    # 只有「完整成功」才寫理想 ON state；partial / 例外時設 None，讓下個 tick 走
    # evaluate 的「重新對齊」分支（align_continuous 重送沒生效的指令並重記基準），
    # 避免把『指令沒生效』誤判成『使用者手動介入』而自廢自動模式——LG 多了
    # set_humidity 這個失敗點後尤其容易踩到。
    with _lock:
        st = _state.setdefault(device_name, _new_runtime())
        st["expected"] = driver.expected_on_state(rule["threshold"]) if fully_ok else None


def _fire_off(device_name, driver):
    try:
        driver.fire_off()
        print(f"[dehum-auto] FIRE OFF {device_name}")
    except Exception as e:
        print(f"[dehum-auto] fire off {device_name} error: {e}")
    with _lock:
        _state.setdefault(device_name, _new_runtime())["expected"] = driver.expected_off_state()


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
