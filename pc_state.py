"""PC monitoring in-memory state.

第一版簡化設計（前面討論的 Option B 簡化版）：
- 純 in-memory ring buffer，home-butler 重啟資料丟失（重啟後 24h 圖會少一段）
- agent 每 60 秒 POST heartbeat 累積進 ring buffer
- 24h × 60s = 1440 點上限
- 不做後端 down-sample，前端拿 raw 點自己畫；mobile 真的卡再加 10min bucket
- 不做 backfill；agent 重啟也不補先前資料

之後升級方向（先記著別做）：
- ring buffer 改 SQLite，agent backfill on home-butler restart
- API 回傳前 down-sample 成 144 個 fixed bucket avg + 1 sliding partial bucket
- 新增 Sheet「電腦」分頁註冊允許 IP whitelist + 硬體 spec
"""

import time
from collections import deque
from threading import Lock

MAX_HISTORY_POINTS = 1440          # 24h × 60s
OFFLINE_THRESHOLD_S = 180          # 超過 3 分鐘沒 heartbeat 視為離線

_lock = Lock()
_pcs: dict = {}                    # key = ip, value = per-PC state dict


def record_heartbeat(payload: dict) -> None:
    """Append a heartbeat to the ring buffer for the PC identified by IP.

    Caller layer (web_api) is responsible for schema validation. This function
    僅做存取，沒帶的欄位存 None。
    """
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
            _pcs[ip] = {
                "meta": {},
                "history": deque(maxlen=MAX_HISTORY_POINTS),
                "current": {},
                "last_heartbeat_at": 0.0,
            }
        pc = _pcs[ip]
        pc["meta"] = {
            "hostname": payload.get("hostname", ""),
            "cpu_model": payload.get("cpu_model", ""),
            "gpu_model": payload.get("gpu_model", ""),
        }
        pc["history"].append(point)
        # current 包含最新的 raw 點 + F@H 狀態（F@H 不進 history，只放 current）
        pc["current"] = {**point, "fah": payload.get("fah")}
        pc["last_heartbeat_at"] = now


def snapshot() -> dict:
    """Return all PC state for /api/computers/status (key = ip)."""
    now = time.time()
    out = {}
    with _lock:
        for ip, pc in _pcs.items():
            online = (now - pc["last_heartbeat_at"]) <= OFFLINE_THRESHOLD_S
            out[ip] = {
                "ip": ip,
                **pc["meta"],
                "current": pc["current"],
                "history": list(pc["history"]),
                "last_heartbeat_at": pc["last_heartbeat_at"],
                "online": online,
            }
    return out
