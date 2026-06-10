"""照明條件式自動開關（自動夜燈）。

每個 Hue 區域可獨立設定一條規則：光感應器（SwitchBot Hub 2 的 lightLevel
1~20）+ 亮度門檻 + 觸發場景 + 開燈亮度（1~100）+ 啟用時段（支援跨午夜）。

評估路徑兩條：
- SwitchBot Webhook（主）：Hub 2 任一數值（溫度/濕度/亮度）變化都會推
  changeReport、皆附當下 lightLevel → 收到就評估，秒級反應。
- 5min polling tick（輔）：處理時段開始（主動拉一次 status 評估）、時段結束
  （關燈一次）、以及 webhook 漏接的兜底。

時段內邏輯：
  lightLevel <= threshold 且燈是關的 → recall 場景 + 設定開燈亮度
  lightLevel >  threshold 且燈是開的 → 關燈
時段結束：關燈一次，之後不再理會（window_active in-memory 旗標）。

已知且刻意不處理：開燈後環境變亮可能跨過門檻造成開關循環（閃爍）。
由使用者自行調整門檻與開燈亮度迴避。

Rule 設定持久化在 Sheet「照明自動規則」分頁；runtime state（window_active /
last_light_level）只放 in-memory，重啟歸零，由下個 tick 重建。

Hue 指令走 agent WebSocket（async），這裡的呼叫端是 sync thread（polling
thread / webhook 衍生 thread），用 run_coroutine_threadsafe 橋接——startup
時 main.py 必須先呼叫 set_event_loop()。
"""

import asyncio
import re
import threading
import time
from threading import Lock

import switchbot_api
from agent_ws import send_agent_command
from config import now_taipei
from sheets import append_record, get_or_create_sheet, update_row_fields

SHEET_NAME = "照明自動規則"
HEADERS = [
    "area_id", "area_name", "enabled", "sensor_device_id", "sensor_name",
    "threshold", "scene_id", "scene_name", "scene_type", "scene_action",
    "brightness", "start_time", "end_time", "last_event", "last_event_at",
]

# 同一 level 的 webhook 事件短時間重複進來（溫濕度變化也觸發 changeReport）
# 不重打 hue.list_areas，省 agent 往返。
EVAL_DEBOUNCE_S = 10

# Hub 2 對雲端的回報是「變化幅度驅動」：亮度劇變立刻報、小幅變化要等定期同步，
# 所以 /status 的雲端快取常常是舊值。但任何 changeReport（含溫濕度觸發的）都
# 帶當下 lightLevel → webhook 進來順手快取，這個年齡內的快取值視為比 status 新鮮。
WEBHOOK_FRESH_S = 360

_lock = Lock()
_rules: dict = {}          # area_id → rule config
_runtime: dict = {}        # area_id → runtime state（不持久化）
_sensor_levels: dict = {}  # normalized device id → {"level", "at"}（webhook 亮度快取）
_loop = None               # FastAPI event loop（橋接 async agent 指令用）


def _new_runtime():
    return {
        "window_active": False,
        "last_light_level": None,
        "last_light_at": 0.0,
        "last_eval_at": 0.0,
        "last_eval_level": None,
    }


def _bool(v):
    return str(v).strip().upper() in ("TRUE", "1", "YES")


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _norm_time(v) -> str:
    """Sheet USER_ENTERED 可能把 '18:00' 改寫成 '18:00:00' 等格式，
    讀取時一律正規化回 HH:MM；解析不了回空字串（規則視同未設定時段）。"""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(v or ""))
    if not m:
        return ""
    hh, mm = int(m.group(1)), int(m.group(2))
    if hh > 23 or mm > 59:
        return ""
    return f"{hh:02d}:{mm:02d}"


def _normalize_device_id(v) -> str:
    """Webhook 的 deviceMac 可能帶冒號、Sheet 存的 Device ID 沒有，
    比對前都拆掉分隔符、轉大寫。"""
    return re.sub(r"[^0-9A-Za-z]", "", str(v or "")).upper()


def _in_window(rule, now) -> bool:
    start = _norm_time(rule.get("start_time"))
    end = _norm_time(rule.get("end_time"))
    if not start or not end or start == end:
        return False
    cur = now.strftime("%H:%M")
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end   # 跨午夜，如 18:00 → 06:00


# ── Agent 指令橋接 ──────────────────────────────────────

def set_event_loop(loop):
    """main.py 在 async startup 抓 running loop 餵進來，sync thread 才能下 Hue 指令。"""
    global _loop
    _loop = loop


def _agent_command(command_type, payload) -> dict:
    if _loop is None:
        raise RuntimeError("event loop not ready（startup 未完成）")
    future = asyncio.run_coroutine_threadsafe(
        send_agent_command(command_type, payload, required_capability="hue", timeout=20.0),
        _loop,
    )
    message = future.result(timeout=25.0)
    if message.get("status") != "ok":
        raise RuntimeError(message.get("error") or "Hue command failed")
    result = message.get("result")
    return result if isinstance(result, dict) else {}


def _area_is_on(area_id) -> bool:
    result = _agent_command("hue.list_areas", {})
    areas = result.get("areas") if isinstance(result.get("areas"), list) else []
    for area in areas:
        if str(area.get("id") or "") == area_id:
            return bool(area.get("on"))
    raise RuntimeError(f"hue.list_areas 找不到區域 {area_id}")


def _fire_scene_on(area_id, rule):
    _agent_command("hue.recall_scene", {
        "scene_id": rule.get("scene_id", ""),
        "action": rule.get("scene_action") or "active",
        "resource_type": rule.get("scene_type") or "scene",
    })
    # bridge 套場景要一拍，等一下再把亮度蓋上去，避免被場景自帶亮度覆寫
    time.sleep(0.8)
    _agent_command("hue.set_state", {
        "area_id": area_id,
        "brightness": rule.get("brightness", 50),
        "resource_type": "grouped_light",
    })


def _fire_off(area_id):
    _agent_command("hue.set_state", {
        "area_id": area_id,
        "on": False,
        "resource_type": "grouped_light",
    })


# ── 規則評估 ────────────────────────────────────────────

def _evaluate_rule(area_id, rule, light_level, source):
    """時段內單次評估。source: webhook / tick / set_rule（log 用）。"""
    now = time.time()
    with _lock:
        rt = _runtime.setdefault(area_id, _new_runtime())
        if (source == "webhook"
                and rt.get("last_eval_level") == light_level
                and now - rt.get("last_eval_at", 0) < EVAL_DEBOUNCE_S):
            return
        rt["last_eval_at"] = now
        rt["last_eval_level"] = light_level

    try:
        is_on = _area_is_on(area_id)
    except Exception as e:
        print(f"[light-auto] {rule.get('area_name') or area_id} 讀取區域狀態失敗（{source}）: {e}")
        return

    threshold = rule.get("threshold", 5)
    label = rule.get("area_name") or area_id
    if light_level <= threshold and not is_on:
        try:
            _fire_scene_on(area_id, rule)
            print(f"[light-auto] ON {label}: lightLevel={light_level} <= {threshold} "
                  f"scene={rule.get('scene_name') or rule.get('scene_id')} "
                  f"bri={rule.get('brightness')} ({source})")
            _write_event(area_id, "triggered_on")
        except Exception as e:
            print(f"[light-auto] {label} 開燈失敗: {e}")
    elif light_level > threshold and is_on:
        try:
            _fire_off(area_id)
            print(f"[light-auto] OFF {label}: lightLevel={light_level} > {threshold} ({source})")
            _write_event(area_id, "triggered_off")
        except Exception as e:
            print(f"[light-auto] {label} 關燈失敗: {e}")


def on_light_report(device_id, light_level):
    """Webhook 主路徑：Hub 2 changeReport 進來呼叫。Sync、可在任意 thread 跑。"""
    light_level = _int(light_level, None)
    if light_level is None:
        return
    key = _normalize_device_id(device_id)
    if not key:
        return
    now = now_taipei()
    with _lock:
        # 不限有規則的感應器都快取——偵測按鈕（light-level 端點）也吃這份
        _sensor_levels[key] = {"level": light_level, "at": time.time()}
        matches = [
            (aid, dict(r)) for aid, r in _rules.items()
            if r.get("enabled") and _normalize_device_id(r.get("sensor_device_id")) == key
        ]
    for area_id, rule in matches:
        with _lock:
            rt = _runtime.setdefault(area_id, _new_runtime())
            rt["last_light_level"] = light_level
            rt["last_light_at"] = time.time()
        if not _in_window(rule, now):
            continue   # 時段外不動作；時段結束的關燈交給 polling tick
        with _lock:
            _runtime[area_id]["window_active"] = True
        _evaluate_rule(area_id, rule, light_level, "webhook")


def tick():
    """每 5min polling tick 呼叫：時段開始/結束的邊界處理 + webhook 漏接兜底。"""
    with _lock:
        active = [(aid, dict(r)) for aid, r in _rules.items() if r.get("enabled")]
    if not active:
        return

    now = now_taipei()
    levels: dict = {}   # 同一感應器多條規則共用一次 status 拉取
    for area_id, rule in active:
        label = rule.get("area_name") or area_id
        try:
            in_win = _in_window(rule, now)
            with _lock:
                was_active = _runtime.setdefault(area_id, _new_runtime()).get("window_active", False)

            if not in_win:
                if was_active:
                    # 時段結束：關燈一次。失敗不清旗標，下個 tick 重試。
                    _fire_off(area_id)
                    print(f"[light-auto] WINDOW END off {label}")
                    _write_event(area_id, "window_end_off")
                    with _lock:
                        _runtime[area_id]["window_active"] = False
                continue

            sensor_id = str(rule.get("sensor_device_id") or "")
            if not sensor_id:
                continue
            if sensor_id not in levels:
                cached = get_cached_light_level(sensor_id)
                if cached and time.time() - cached["at"] <= WEBHOOK_FRESH_S:
                    # webhook 剛報過 → 比 status 雲端快取新鮮，且省一次 API 呼叫
                    levels[sensor_id] = cached["level"]
                else:
                    status = switchbot_api.get_device_status(sensor_id)
                    levels[sensor_id] = (
                        status.get("lightLevel")
                        if isinstance(status, dict) and "error" not in status else None
                    )
            light = _int(levels[sensor_id], None)

            with _lock:
                rt = _runtime.setdefault(area_id, _new_runtime())
                rt["window_active"] = True
                if light is not None:
                    rt["last_light_level"] = light
                    rt["last_light_at"] = time.time()

            if light is None:
                print(f"[light-auto] {label}: 拉不到 lightLevel（sensor {sensor_id}）")
                continue
            _evaluate_rule(area_id, rule, light, "tick")
        except Exception as e:
            print(f"[light-auto] {label} tick error: {e}")


def _probe_and_evaluate(area_id):
    """set_rule 啟用後立即評估一次（拉當下 lightLevel），不等下個 webhook/tick。"""
    with _lock:
        rule = dict(_rules.get(area_id) or {})
    if not rule.get("enabled") or not _in_window(rule, now_taipei()):
        return
    status = switchbot_api.get_device_status(str(rule.get("sensor_device_id") or ""))
    light = _int(
        status.get("lightLevel") if isinstance(status, dict) and "error" not in status else None,
        None,
    )
    if light is None:
        print(f"[light-auto] {rule.get('area_name') or area_id} 啟用後即時評估：拉不到 lightLevel")
        return
    with _lock:
        rt = _runtime.setdefault(area_id, _new_runtime())
        rt["window_active"] = True
        rt["last_light_level"] = light
        rt["last_light_at"] = time.time()
    _evaluate_rule(area_id, rule, light, "set_rule")


# ── Public API（給 lighting_api / main 用） ─────────────

def load_rules():
    """Startup 載入規則。Runtime state 不存 Sheet，從零重建。"""
    try:
        ws = get_or_create_sheet(SHEET_NAME, HEADERS)
        records = ws.get_all_records()
        with _lock:
            _rules.clear()
            for r in records:
                area_id = str(r.get("area_id", "") or "").strip()
                if not area_id:
                    continue
                _rules[area_id] = {
                    "area_name": str(r.get("area_name", "") or ""),
                    "enabled": _bool(r.get("enabled")),
                    "sensor_device_id": str(r.get("sensor_device_id", "") or ""),
                    "sensor_name": str(r.get("sensor_name", "") or ""),
                    "threshold": _int(r.get("threshold"), 5),
                    "scene_id": str(r.get("scene_id", "") or ""),
                    "scene_name": str(r.get("scene_name", "") or ""),
                    "scene_type": str(r.get("scene_type", "") or "scene"),
                    "scene_action": str(r.get("scene_action", "") or "active"),
                    "brightness": _int(r.get("brightness"), 50),
                    "start_time": _norm_time(r.get("start_time")),
                    "end_time": _norm_time(r.get("end_time")),
                    "last_event": str(r.get("last_event", "") or ""),
                    "last_event_at": str(r.get("last_event_at", "") or ""),
                }
                _runtime.setdefault(area_id, _new_runtime())
        print(f"[light-auto] loaded {len(_rules)} rules from Sheet")
    except Exception as e:
        print(f"[light-auto] load error: {e}")


def get_all_rules() -> dict:
    with _lock:
        return {
            aid: {**r, "runtime": dict(_runtime.get(aid) or _new_runtime())}
            for aid, r in _rules.items()
        }


def get_cached_light_level(device_id):
    """某感應器最後一次 webhook 回報的亮度。回 {"level", "at"} 或 None。"""
    key = _normalize_device_id(device_id)
    with _lock:
        entry = _sensor_levels.get(key)
        return dict(entry) if entry else None


def set_rule(area_id, *, enabled, sensor_device_id="", sensor_name="",
             threshold=5, scene_id="", scene_name="", scene_type="scene",
             scene_action="active", brightness=50, start_time="", end_time="",
             area_name=""):
    """Dashboard 設定/更新規則。先寫 Sheet（失敗就拋、不留半套），再更新 memory。
    啟用且當下在時段內 → 背景 thread 立即評估一次。"""
    with _lock:
        old = _rules.get(area_id, {})
    rule = {
        "area_name": str(area_name or old.get("area_name", "")),
        "enabled": bool(enabled),
        "sensor_device_id": str(sensor_device_id or ""),
        "sensor_name": str(sensor_name or ""),
        "threshold": _int(threshold, 5),
        "scene_id": str(scene_id or ""),
        "scene_name": str(scene_name or ""),
        "scene_type": str(scene_type or "scene"),
        "scene_action": str(scene_action or "active"),
        "brightness": _int(brightness, 50),
        "start_time": _norm_time(start_time),
        "end_time": _norm_time(end_time),
        "last_event": old.get("last_event", ""),
        "last_event_at": old.get("last_event_at", ""),
    }
    _write_rule(area_id, rule)
    with _lock:
        _rules[area_id] = rule
        _runtime[area_id] = _new_runtime()   # 設定變更一律重置 runtime
    if rule["enabled"]:
        threading.Thread(target=_probe_and_evaluate, args=(area_id,), daemon=True).start()
    with _lock:
        return {**_rules[area_id], "runtime": dict(_runtime[area_id])}


def delete_rule(area_id):
    """先刪 Sheet 列（失敗就拋，避免重啟後規則復活），再清 memory。"""
    ws = get_or_create_sheet(SHEET_NAME, HEADERS)
    records = ws.get_all_records()
    for idx, row in enumerate(records, start=2):
        if str(row.get("area_id", "") or "").strip() == area_id:
            ws.delete_rows(idx)
            break
    with _lock:
        _rules.pop(area_id, None)
        _runtime.pop(area_id, None)


# ── Sheet I/O ──────────────────────────────────────────

def _rule_to_row_fields(rule) -> dict:
    return {
        "area_name": rule.get("area_name", ""),
        "enabled": "TRUE" if rule.get("enabled") else "FALSE",
        "sensor_device_id": rule.get("sensor_device_id", ""),
        "sensor_name": rule.get("sensor_name", ""),
        "threshold": rule.get("threshold", 5),
        "scene_id": rule.get("scene_id", ""),
        "scene_name": rule.get("scene_name", ""),
        "scene_type": rule.get("scene_type", "scene"),
        "scene_action": rule.get("scene_action", "active"),
        "brightness": rule.get("brightness", 50),
        "start_time": rule.get("start_time", ""),
        "end_time": rule.get("end_time", ""),
        "last_event": rule.get("last_event", ""),
        "last_event_at": rule.get("last_event_at", ""),
    }


def _write_rule(area_id, rule):
    ws = get_or_create_sheet(SHEET_NAME, HEADERS)
    records = ws.get_all_records()
    fields = _rule_to_row_fields(rule)
    for idx, row in enumerate(records, start=2):
        if str(row.get("area_id", "") or "").strip() == area_id:
            update_row_fields(ws, idx, fields)
            return
    append_record(ws, {"area_id": area_id, **fields})


def _write_event(area_id, event):
    """觸發事件記錄（memory + Sheet 那一列的 last_event 欄）。Sheet 寫失敗只 log。"""
    at = now_taipei().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        rule = _rules.get(area_id)
        if rule is not None:
            rule["last_event"] = event
            rule["last_event_at"] = at
    try:
        ws = get_or_create_sheet(SHEET_NAME, HEADERS)
        records = ws.get_all_records()
        for idx, row in enumerate(records, start=2):
            if str(row.get("area_id", "") or "").strip() == area_id:
                update_row_fields(ws, idx, {"last_event": event, "last_event_at": at})
                return
    except Exception as e:
        print(f"[light-auto] event write error: {e}")
