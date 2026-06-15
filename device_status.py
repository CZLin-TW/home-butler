"""Unified in-memory device status cache for Dashboard reads.

The Google Sheet remains the persistent source for device configuration and
write-only AC last-command state. Frequently changing status is kept here so
GET /api/devices/status can return immediately without waiting for cloud APIs.
"""

import time
from threading import Lock


_lock = Lock()
_statuses: dict[str, dict] = {}
_device_rows: dict[str, dict] = {}
_refreshing = False
_last_refresh_started = 0.0


def load_catalog(rows) -> None:
    """Replace the active device catalog and hydrate persisted AC state."""
    active_rows = {}
    for row in rows:
        if row.get("狀態") != "啟用":
            continue
        name = str(row.get("名稱", "")).strip()
        if name:
            active_rows[name] = dict(row)

    with _lock:
        next_statuses = {}
        for name, row in active_rows.items():
            status = dict(_statuses.get(name, {}))
            status.update({
                "type": row.get("類型", ""),
                "location": row.get("位置", ""),
            })
            if row.get("類型") == "空調":
                status.update({
                    "lastPower": row.get("最後電源", ""),
                    "lastTemperature": row.get("最後溫度", ""),
                    "lastMode": row.get("最後模式", ""),
                    "lastFanSpeed": row.get("最後風速", ""),
                    "lastUpdatedAt": row.get("最後更新時間", ""),
                })
            next_statuses[name] = status

        _device_rows.clear()
        _device_rows.update(active_rows)
        _statuses.clear()
        _statuses.update(next_statuses)


def has_catalog() -> bool:
    with _lock:
        return bool(_device_rows)


def device_rows() -> list[dict]:
    with _lock:
        return [dict(row) for row in _device_rows.values()]


def update(device_name: str, fields: dict) -> None:
    """Merge one device's latest fields into the cache."""
    name = str(device_name or "").strip()
    if not name or not fields:
        return
    with _lock:
        current = dict(_statuses.get(name, {}))
        current.update(fields)
        _statuses[name] = current


def snapshot(device_name: str = "") -> dict:
    """Return a copy of all status, or one named device in map form."""
    with _lock:
        if device_name:
            status = _statuses.get(device_name)
            return {device_name: dict(status)} if status is not None else {}
        return {name: dict(status) for name, status in _statuses.items()}


def try_begin_refresh(min_interval_s: float = 45.0) -> bool:
    """Acquire the single-flight background refresh slot."""
    global _refreshing, _last_refresh_started
    now = time.monotonic()
    with _lock:
        if _refreshing or now - _last_refresh_started < min_interval_s:
            return False
        _refreshing = True
        _last_refresh_started = now
        return True


def finish_refresh() -> None:
    global _refreshing
    with _lock:
        _refreshing = False
