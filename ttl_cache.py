"""執行緒安全的小型 TTL 快取。

給「外部 API 回應在一段時間內不會變、但被重複查詢」的路徑用（目前是中央氣象署的
預報與觀測）。刻意做得很小：沒有背景清理緒程、沒有統計，過期項目在下次讀到時才丟。

**只快取成功結果**是使用約定，不是這個類別強制的——呼叫端各自判斷什麼算成功
（`_fetch_forecast` 看 `"error" not in result`、`get_observation` 看 `is not None`），
失敗就不要 set()。快取住失敗會讓「CWA 恢復了但我們還在回錯誤」這種難查的狀況出現。
"""

import threading
import time

_MISS = object()


class TTLCache:
    """key → value，超過 ttl_seconds 就視為不存在。

    max_entries 是防呆而非效能考量：正常只會有一兩個地點，但 `_resolve_location`
    對沒見過的鄉鎮會遍歷 22 個縣市，設個上限免得意外把 free instance 的記憶體吃光。
    滿了就丟最舊寫入的那筆（插入序 = dict 序）。
    """

    def __init__(self, ttl_seconds: float, max_entries: int = 32):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._lock = threading.Lock()
        self._data: dict = {}  # key → (value, stored_at)

    def get(self, key, default=None):
        now = time.time()
        with self._lock:
            entry = self._data.get(key, _MISS)
            if entry is _MISS:
                return default
            value, stored_at = entry
            if now - stored_at >= self._ttl:
                self._data.pop(key, None)
                return default
            return value

    def set(self, key, value) -> None:
        now = time.time()
        with self._lock:
            # 先移除同 key 再寫入，讓它移到插入序尾端（否則更新過的舊 key 會先被淘汰）
            self._data.pop(key, None)
            while len(self._data) >= self._max:
                self._data.pop(next(iter(self._data)))
            self._data[key] = (value, now)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
