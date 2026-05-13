"""除濕機 ON/OFF 歷史 in-memory ring buffer + Sheet backup。

設計同 ac_history.py：dehumidifier_auto.evaluate_all 每 5 分鐘 polling tick
順手把當下 power 狀態 snapshot 進來。auto_mode=False 的除濕機**不會**被
polling，因此 history 也不會 record——這正好符合 UI 設計（自動模式 chart
只在 auto_mode ON 時顯示）。

key by device_name，每台除濕機自己的 ring buffer。
"""

import threading
import time
from threading import Lock

import gspread

from sheets import _get_spreadsheet

MAX_HISTORY_POINTS = 288           # 24h / 5min
SHEET_NAME = "除濕機歷史"
HEADERS = ["timestamp", "device_name", "location", "power"]
TRIM_EVERY_N_APPENDS = 12          # ~1 小時 trim 一次
SHEET_HARD_LIMIT_ROWS = 5000

_lock = Lock()
_dehums: dict = {}                 # key = device_name
_backfilled = False
_cached_ws = None
_append_counter = 0


def _new_dehum():
    return {
        "meta": {},
        "history_dict": {},        # int(t) → point
        "current": {},
        "last_recorded_at": 0.0,
    }


def record(device_name: str, location: str, power: bool) -> None:
    """記錄除濕機當下 power 狀態。dehumidifier_auto.evaluate_all 在抓完
    Panasonic status 後呼叫。"""
    now = time.time()
    point = {"t": now, "power": "on" if power else "off"}

    with _lock:
        if device_name not in _dehums:
            _dehums[device_name] = _new_dehum()
        d = _dehums[device_name]
        d["meta"] = {"location": location}
        d["history_dict"][int(now)] = point
        if len(d["history_dict"]) > MAX_HISTORY_POINTS:
            sorted_keys = sorted(d["history_dict"].keys())
            for k in sorted_keys[:-MAX_HISTORY_POINTS]:
                del d["history_dict"][k]
        d["current"] = point
        d["last_recorded_at"] = now

    threading.Thread(target=_sheet_append_async, args=(point, device_name, location), daemon=True).start()


def snapshot() -> dict:
    """Return all dehumidifier power history for /api/dehumidifier/history (key = device_name)."""
    out = {}
    with _lock:
        for name, d in _dehums.items():
            sorted_keys = sorted(d["history_dict"].keys())
            history = [d["history_dict"][k] for k in sorted_keys]
            out[name] = {
                "device_name": name,
                **d["meta"],
                "current": d["current"],
                "history": history,
                "last_recorded_at": d["last_recorded_at"],
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
        print(f"[dehum_history] created sheet '{SHEET_NAME}'")
    _cached_ws = ws
    return ws


def _sheet_append_async(point, device_name, location):
    global _append_counter
    try:
        ws = _ensure_history_sheet()
        ws.append_row(
            [point["t"], device_name, location, point.get("power", "")],
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
        print(f"[dehum_history] sheet append error: {e}")


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
            print(f"[dehum_history] hard-limit trim: row count {len(records)} > {SHEET_HARD_LIMIT_ROWS}")

        if last_old_idx >= 0:
            count = last_old_idx + 1
            ws.delete_rows(2, 2 + count - 1)
            print(f"[dehum_history] trimmed {count} old rows")
    except Exception as e:
        print(f"[dehum_history] trim error: {e}")


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
                if name not in _dehums:
                    _dehums[name] = _new_dehum()
                d = _dehums[name]
                location = r.get("location", "")
                if location and not d["meta"].get("location"):
                    d["meta"]["location"] = location
                point = {
                    "t": t,
                    "power": str(r.get("power", "")),
                }
                d["history_dict"][int(t)] = point
                loaded += 1
        print(f"[dehum_history] backfilled {loaded} points from Sheet")
    except Exception as e:
        print(f"[dehum_history] backfill error: {e}")
    _backfilled = True
