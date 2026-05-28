"""除濕機 + 感測器 status in-memory TTL cache，給 web_api /devices/status 用。

動機：Dashboard 60s 全局 polling + 命令送出後樂觀 polling + 多 tab/多 client 同時開
會疊出對 Panasonic / LG / SwitchBot 雲端的 hammer。短 TTL cache 把重複 hit 收斂到
單一來源，特別是 Panasonic 兩台共享 token 並發容易觸發雲端風控的情境。

Invalidation：control endpoint / 自動模式 _fire_on/off 完事後呼叫 invalidate()，
下次 read miss 強制 refetch 拿到新狀態，樂觀更新體感無延遲。

TTL 選 15s：
- 比 Dashboard 60s 全局 polling 短 → 多 client 重疊時間窗共用一份
- 比 device-controller 樂觀 30s polling 的一半短 → invalidate 後最多撐 15s 不打雲端
- 不至於 stale 到使用者察覺（人類 perception > 200ms 才有感、15s 內手動操作不會發生）
"""

import threading
import time

CACHE_TTL_SEC = 15

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def get(name: str) -> dict | None:
    """回傳 cached status；miss / 過期 return None。"""
    if not name:
        return None
    with _lock:
        entry = _cache.get(name)
        if entry is None:
            return None
        ts, status = entry
        if time.time() - ts > CACHE_TTL_SEC:
            return None
        return status


def put(name: str, status: dict) -> None:
    """寫入 cache。非 dict / 空 dict 跳過（避免快取錯誤狀態被當成有效）。"""
    if not name or not isinstance(status, dict) or not status:
        return
    with _lock:
        _cache[name] = (time.time(), status)


def invalidate(name: str) -> None:
    """命令送出後呼叫，強制下次 read 重打雲端。多次呼叫 idempotent。"""
    if not name:
        return
    with _lock:
        _cache.pop(name, None)
