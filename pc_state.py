"""PC monitoring in-memory state + Google Sheets backup（解 home-butler 重啟資料遺失問題）。

設計（升級自第一版簡化版）：
- agent 不變，繼續每 60s push 單點 heartbeat
- in-memory: dict[int(timestamp_sec) → point]，方便 backfill 時 merge by timestamp
- Sheet「PC 監控歷史」分頁背景 append 一筆（thread，不阻塞 HTTP response）
- 啟動時 backfill_from_sheet() 一次：撈最近 24h 填回 in-memory
- 定時 trim：每 N 次 append 刪除超過 24h 的舊 row（連續區段一次 batch delete）
- 分頁不存在時自動建（含 header row），使用者不用手動新增分頁

之後升級方向（先記著別做）：
- gap detection：history 中相鄰兩點時間差 > N × tick 時插 null 點，前端 Recharts 才會正確斷線
- 為避免 trim race condition，trim 用 batch_update 一次發
"""

import threading
import time
from threading import Lock

import gspread

from sheets import _get_spreadsheet

MAX_HISTORY_POINTS = 1440          # 24h × 60s 上限
OFFLINE_THRESHOLD_S = 180          # 3 分鐘沒 heartbeat 視為離線
PC_HISTORY_SHEET = "PC 監控歷史"
HISTORY_HEADERS = ["timestamp", "ip", "cpu_pct", "ram_pct", "gpu_pct", "cpu_temp_c", "gpu_temp_c"]
TRIM_EVERY_N_APPENDS = 100         # ~50 分鐘一次（兩台 PC × 60s）
SHEET_HARD_LIMIT_ROWS = 10000      # 防呆：trim fail 時的最後一道牆

_lock = Lock()
_pcs: dict = {}                    # key = ip
_backfilled = False
_cached_ws = None                  # 分頁物件 cache（避免每次 append 都 worksheet() 一次）
_append_counter = 0


def _new_pc():
    return {
        "meta": {},
        "history_dict": {},        # int(t) → point
        "current": {},
        "last_heartbeat_at": 0.0,
    }


def record_heartbeat(payload: dict) -> None:
    """Append a heartbeat。in-memory 寫完立即 return；Sheet append 走背景 thread 不阻塞。"""
    ip = payload["ip"]
    now = time.time()
    point = {
        "t": now,
        "cpu_pct": payload.get("cpu_pct"),
        "ram_pct": payload.get("ram_pct"),
        "gpu_pct": payload.get("gpu_pct"),
        "cpu_temp_c": payload.get("cpu_temp_c"),
        "gpu_temp_c": payload.get("gpu_temp_c"),
    }

    with _lock:
        if ip not in _pcs:
            _pcs[ip] = _new_pc()
        pc = _pcs[ip]
        pc["meta"] = {
            "hostname": payload.get("hostname", ""),
            "cpu_model": payload.get("cpu_model", ""),
            "gpu_model": payload.get("gpu_model", ""),
        }
        pc["history_dict"][int(now)] = point
        if len(pc["history_dict"]) > MAX_HISTORY_POINTS:
            sorted_keys = sorted(pc["history_dict"].keys())
            for k in sorted_keys[:-MAX_HISTORY_POINTS]:
                del pc["history_dict"][k]
        pc["current"] = {**point, "fah": payload.get("fah")}
        pc["last_heartbeat_at"] = now

    threading.Thread(target=_sheet_append_async, args=(point, ip), daemon=True).start()


def snapshot() -> dict:
    """Return all PC state for /api/computers/status (key = ip)."""
    now = time.time()
    out = {}
    with _lock:
        for ip, pc in _pcs.items():
            online = (now - pc["last_heartbeat_at"]) <= OFFLINE_THRESHOLD_S
            sorted_keys = sorted(pc["history_dict"].keys())
            history = [pc["history_dict"][k] for k in sorted_keys]
            out[ip] = {
                "ip": ip,
                **pc["meta"],
                "current": pc["current"],
                "history": history,
                "last_heartbeat_at": pc["last_heartbeat_at"],
                "online": online,
            }
    return out


# ── Sheet I/O ──────────────────────────────────────────

def _ensure_history_sheet():
    """確保「PC 監控歷史」分頁存在，沒就建（含 header row）。Cache ws 物件避免重複查找。"""
    global _cached_ws
    if _cached_ws is not None:
        return _cached_ws
    ss = _get_spreadsheet()
    try:
        ws = ss.worksheet(PC_HISTORY_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=PC_HISTORY_SHEET, rows=2000, cols=len(HISTORY_HEADERS))
        ws.append_row(HISTORY_HEADERS, value_input_option="USER_ENTERED")
        print(f"[pc_state] created sheet '{PC_HISTORY_SHEET}'")
    _cached_ws = ws
    return ws


def _sheet_append_async(point: dict, ip: str) -> None:
    """背景寫入一筆 row。失敗不影響主流程，只 log。"""
    global _append_counter
    try:
        ws = _ensure_history_sheet()
        ws.append_row(
            [
                point["t"], ip,
                point.get("cpu_pct"), point.get("ram_pct"), point.get("gpu_pct"),
                point.get("cpu_temp_c"), point.get("gpu_temp_c"),
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
        print(f"[pc_state] sheet append error: {e}")


def _trim_sheet(ws) -> None:
    """刪掉 timestamp < now - 24h 的 row。row 1 是 header，從 row 2 起算。

    假設 row 按 append 順序排（=按 timestamp 升序），所以過期 row 都在最前面，
    一次 batch delete 一段最便宜（不必逐筆 API call）。"""
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
                break  # 遇到第一個 fresh 即停（chronological 假設）

        # Hard limit 防呆：即使 timestamp 都 fresh，row 數量爆了也強制砍
        if last_old_idx < 0 and len(records) > SHEET_HARD_LIMIT_ROWS:
            last_old_idx = len(records) - MAX_HISTORY_POINTS - 1
            print(f"[pc_state] hard-limit trim: row count {len(records)} > {SHEET_HARD_LIMIT_ROWS}")

        if last_old_idx >= 0:
            count = last_old_idx + 1
            ws.delete_rows(2, 2 + count - 1)
            print(f"[pc_state] trimmed {count} old rows")
    except Exception as e:
        print(f"[pc_state] trim error: {e}")


def _to_float_or_none(v):
    if v == "" or v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def backfill_from_sheet() -> None:
    """從 Sheet 撈最近 24h 的 row 填回 in-memory ring buffer。FastAPI startup 跑一次。

    重啟時 in-memory 是空的，這個 fn 把過去資料還原回來；只在 process 啟動時跑一次
    （flag _backfilled），之後 in-memory 累積不再讀 Sheet。"""
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
                ip = r.get("ip", "")
                if not ip:
                    continue
                if ip not in _pcs:
                    _pcs[ip] = _new_pc()
                pc = _pcs[ip]
                point = {
                    "t": t,
                    "cpu_pct": _to_float_or_none(r.get("cpu_pct")),
                    "ram_pct": _to_float_or_none(r.get("ram_pct")),
                    "gpu_pct": _to_float_or_none(r.get("gpu_pct")),
                    "cpu_temp_c": _to_float_or_none(r.get("cpu_temp_c")),
                    "gpu_temp_c": _to_float_or_none(r.get("gpu_temp_c")),
                }
                pc["history_dict"][int(t)] = point
                loaded += 1
        print(f"[pc_state] backfilled {loaded} points from Sheet (cutoff={cutoff:.0f})")
    except Exception as e:
        print(f"[pc_state] backfill error: {e}")
    _backfilled = True
