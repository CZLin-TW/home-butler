"""空調操作歷史的 in-memory ring buffer + Sheet backup。

設計同 sensor_state.py：每 5 分鐘 polling 一次（跟 sensor 共用 tick），讀「智能居家」
分頁的「最後電源/最後溫度/最後模式/最後風速」欄位 snapshot 進來。

「最後電源」空白 = 從未操作過該 AC，skip 不 record（避免無意義 row）。

key by device_name，每台 AC 自己的 ring buffer。
"""

import threading
import time
from threading import Lock

import gspread

from sheets import _get_spreadsheet

MAX_HISTORY_POINTS = 288           # 24h / 5min
SHEET_NAME = "空調狀態歷史"
HEADERS = ["timestamp", "device_name", "location", "power", "temperature", "mode", "fan_speed"]
TRIM_EVERY_N_APPENDS = 12          # 跟 sensor_state 同節奏（~1 小時 trim 一次）
SHEET_HARD_LIMIT_ROWS = 5000

_lock = Lock()
_acs: dict = {}                    # key = device_name
_backfilled = False
_cached_ws = None
_append_counter = 0


def _new_ac():
    return {
        "meta": {},
        "history_dict": {},        # int(t) → point
        "current": {},
        "last_recorded_at": 0.0,
    }


def record(device_name: str, location: str, power, temperature, mode, fan_speed) -> None:
    """記錄一筆 AC snapshot。power 空白會被 caller 過濾，這裡不重複檢查。"""
    now = time.time()
    point = {
        "t": now,
        "power": str(power) if power else "",
        "temperature": temperature if temperature not in (None, "") else None,
        "mode": str(mode) if mode else "",
        "fan_speed": str(fan_speed) if fan_speed else "",
    }

    with _lock:
        if device_name not in _acs:
            _acs[device_name] = _new_ac()
        a = _acs[device_name]
        a["meta"] = {"location": location}
        a["history_dict"][int(now)] = point
        if len(a["history_dict"]) > MAX_HISTORY_POINTS:
            sorted_keys = sorted(a["history_dict"].keys())
            for k in sorted_keys[:-MAX_HISTORY_POINTS]:
                del a["history_dict"][k]
        a["current"] = point
        a["last_recorded_at"] = now

    threading.Thread(target=_sheet_append_async, args=(point, device_name, location), daemon=True).start()


def snapshot() -> dict:
    """Return all AC state for /api/ac/status (key = device_name)."""
    out = {}
    with _lock:
        for name, a in _acs.items():
            sorted_keys = sorted(a["history_dict"].keys())
            history = [a["history_dict"][k] for k in sorted_keys]
            out[name] = {
                "device_name": name,
                **a["meta"],
                "current": a["current"],
                "history": history,
                "last_recorded_at": a["last_recorded_at"],
            }
    return out


def _ensure_history_sheet():
    global _cached_ws
    if _cached_ws is not None:
        return _cached_ws
    ss = _get_spreadsheet()
    try:
        ws = ss.worksheet(SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=SHEET_NAME, rows=2000, cols=len(HEADERS))
        ws.append_row(HEADERS, value_input_option="USER_ENTERED")
        print(f"[ac_history] created sheet '{SHEET_NAME}'")
    _cached_ws = ws
    return ws


def _sheet_append_async(point, device_name, location):
    global _append_counter
    try:
        ws = _ensure_history_sheet()
        ws.append_row(
            [
                point["t"], device_name, location,
                point.get("power", ""),
                point.get("temperature") if point.get("temperature") is not None else "",
                point.get("mode", ""),
                point.get("fan_speed", ""),
            ],
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
        print(f"[ac_history] sheet append error: {e}")


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
                break

        if last_old_idx < 0 and len(records) > SHEET_HARD_LIMIT_ROWS:
            last_old_idx = len(records) - MAX_HISTORY_POINTS - 1
            print(f"[ac_history] hard-limit trim: row count {len(records)} > {SHEET_HARD_LIMIT_ROWS}")

        if last_old_idx >= 0:
            count = last_old_idx + 1
            ws.delete_rows(2, 2 + count - 1)
            print(f"[ac_history] trimmed {count} old rows")
    except Exception as e:
        print(f"[ac_history] trim error: {e}")


def _to_float_or_none(v):
    if v == "" or v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def backfill_from_sheet() -> None:
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
                if name not in _acs:
                    _acs[name] = _new_ac()
                a = _acs[name]
                location = r.get("location", "")
                if location and not a["meta"].get("location"):
                    a["meta"]["location"] = location
                point = {
                    "t": t,
                    "power": str(r.get("power", "")),
                    "temperature": _to_float_or_none(r.get("temperature")),
                    "mode": str(r.get("mode", "")),
                    "fan_speed": str(r.get("fan_speed", "")),
                }
                a["history_dict"][int(t)] = point
                loaded += 1
        print(f"[ac_history] backfilled {loaded} points from Sheet")
    except Exception as e:
        print(f"[ac_history] backfill error: {e}")
    _backfilled = True
