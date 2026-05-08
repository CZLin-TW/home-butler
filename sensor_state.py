"""SwitchBot 感測器溫濕度 in-memory state + Sheet backup（解 home-butler 重啟資料遺失）。

設計同 pc_state.py，但資料來源是 home-butler 自己每 60s 主動 polling SwitchBot API
（不需要 PC agent，因為 SwitchBot 是 cloud API、home-butler 自己就拿得到）。

- 啟用中、類型=感應器 的設備全部 polling
- in-memory: dict[device_name → { meta, history_dict[int(t) → point], current, last_polled_at }]
- Sheet「感測器歷史」分頁背景 append + 定時 trim
- 啟動時 backfill_from_sheet() 還原 24h
- 分頁不存在時自動建（含 header row）
- polling thread 失敗不影響主流程，下次 tick 重試
"""

import threading
import time
from threading import Lock

import gspread

from sheets import _get_spreadsheet

MAX_HISTORY_POINTS = 288           # 24h / 5min（polling 5 分鐘一次）
OFFLINE_THRESHOLD_S = 900          # 15 分鐘沒 poll 視為離線（給 polling 一兩次容錯）
SENSOR_HISTORY_SHEET = "感測器歷史"
HISTORY_HEADERS = ["timestamp", "device_name", "location", "temp", "humidity"]
TRIM_EVERY_N_APPENDS = 12          # ~1 小時 trim 一次（polling 5min × N 感測器）
SHEET_HARD_LIMIT_ROWS = 5000       # 防呆上限

_lock = Lock()
_sensors: dict = {}                # key = device_name
_backfilled = False
_cached_ws = None
_append_counter = 0


def _new_sensor():
    return {
        "meta": {},
        "history_dict": {},
        "current": {},
        "last_polled_at": 0.0,
    }


def record(device_name: str, location: str, temp, humidity) -> None:
    """寫入一筆感測器讀值。in-memory 即時、Sheet append 走背景 thread。
    temp / humidity 任一可為 None（讀值缺失），不影響另一個。"""
    if temp is None and humidity is None:
        return  # 兩個都缺、沒意義
    now = time.time()
    point = {"t": now, "temp": temp, "humidity": humidity}

    with _lock:
        if device_name not in _sensors:
            _sensors[device_name] = _new_sensor()
        s = _sensors[device_name]
        s["meta"] = {"location": location}
        s["history_dict"][int(now)] = point
        if len(s["history_dict"]) > MAX_HISTORY_POINTS:
            sorted_keys = sorted(s["history_dict"].keys())
            for k in sorted_keys[:-MAX_HISTORY_POINTS]:
                del s["history_dict"][k]
        s["current"] = point
        s["last_polled_at"] = now

    threading.Thread(target=_sheet_append_async, args=(point, device_name, location), daemon=True).start()


def snapshot() -> dict:
    """Return all sensor state for /api/sensors/status (key = device_name)."""
    now = time.time()
    out = {}
    with _lock:
        for name, s in _sensors.items():
            online = (now - s["last_polled_at"]) <= OFFLINE_THRESHOLD_S
            sorted_keys = sorted(s["history_dict"].keys())
            history = [s["history_dict"][k] for k in sorted_keys]
            out[name] = {
                "device_name": name,
                **s["meta"],
                "current": s["current"],
                "history": history,
                "last_polled_at": s["last_polled_at"],
                "online": online,
            }
    return out


# ── Sheet I/O ──────────────────────────────────────────

def _ensure_history_sheet():
    global _cached_ws
    if _cached_ws is not None:
        return _cached_ws
    ss = _get_spreadsheet()
    try:
        ws = ss.worksheet(SENSOR_HISTORY_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=SENSOR_HISTORY_SHEET, rows=2000, cols=len(HISTORY_HEADERS))
        ws.append_row(HISTORY_HEADERS, value_input_option="USER_ENTERED")
        print(f"[sensor_state] created sheet '{SENSOR_HISTORY_SHEET}'")
    _cached_ws = ws
    return ws


def _sheet_append_async(point: dict, device_name: str, location: str) -> None:
    global _append_counter
    try:
        ws = _ensure_history_sheet()
        ws.append_row(
            [point["t"], device_name, location, point.get("temp"), point.get("humidity")],
            value_input_option="USER_ENTERED",
        )
        with _lock:
            _append_counter += 1
            should_trim = _append_counter >= TRIM_EVERY_N_APPENDS
            if should_trim:
                _append_counter = 0
        if should_trim:
            _trim_sheet(ws)
    except Exception as e:
        print(f"[sensor_state] sheet append error: {e}")


def _trim_sheet(ws) -> None:
    try:
        records = ws.get_all_records()
        cutoff = time.time() - 86400
        last_old_idx = -1
        for i, r in enumerate(records):
            try:
                t = float(r.get("timestamp", 0))
            except (ValueError, TypeError):
                continue
            if t < cutoff:
                last_old_idx = i
            else:
                break  # 假設按 append 順序排（=按 timestamp 升序）

        if last_old_idx < 0 and len(records) > SHEET_HARD_LIMIT_ROWS:
            last_old_idx = len(records) - MAX_HISTORY_POINTS - 1
            print(f"[sensor_state] hard-limit trim: row count {len(records)} > {SHEET_HARD_LIMIT_ROWS}")

        if last_old_idx >= 0:
            count = last_old_idx + 1
            ws.delete_rows(2, 2 + count - 1)
            print(f"[sensor_state] trimmed {count} old rows")
    except Exception as e:
        print(f"[sensor_state] trim error: {e}")


def _to_float_or_none(v):
    if v == "" or v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def backfill_from_sheet() -> None:
    """Startup 跑一次，從 Sheet 撈最近 24h 填回 in-memory ring buffer。"""
    global _backfilled
    if _backfilled:
        return
    try:
        ws = _ensure_history_sheet()
        records = ws.get_all_records()
        cutoff = time.time() - 86400
        loaded = 0
        with _lock:
            for r in records:
                t = _to_float_or_none(r.get("timestamp"))
                if t is None or t < cutoff:
                    continue
                name = r.get("device_name", "")
                if not name:
                    continue
                if name not in _sensors:
                    _sensors[name] = _new_sensor()
                s = _sensors[name]
                location = r.get("location", "")
                if location and not s["meta"].get("location"):
                    s["meta"]["location"] = location
                point = {
                    "t": t,
                    "temp": _to_float_or_none(r.get("temp")),
                    "humidity": _to_float_or_none(r.get("humidity")),
                }
                # Skip 已寫進 Sheet 的 0,0 row（早期版本 get_hub_sensor 沒 filter
                # SwitchBot 失聯時回的 0,0；現在 filter 了，但歷史資料還在 Sheet。
                # 24h 後 trim 會自動清，期間 backfill 跳過避免污染 chart）。
                if point["temp"] == 0 and point["humidity"] == 0:
                    continue
                s["history_dict"][int(t)] = point
                loaded += 1
        print(f"[sensor_state] backfilled {loaded} points from Sheet")
    except Exception as e:
        print(f"[sensor_state] backfill error: {e}")
    _backfilled = True
