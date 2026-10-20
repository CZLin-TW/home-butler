"""The system's version number, which lives in the Dashboard (package.json), read at most once an hour."""
from __future__ import annotations

import time


class DashboardVersion:
    TTL = 3600

    def __init__(self, base_url: str, http, clock=time.monotonic):
        self.base_url, self.http, self.clock = base_url, http, clock
        self.value, self.read_at = None, None

    def __call__(self) -> str:
        if self.value is not None and self.clock() - self.read_at < self.TTL:
            return self.value
        if not self.base_url:
            return "未知"
        try:
            found = str(self.http.get(self.base_url + "/api/version", timeout=5).json().get("version") or "")
        except Exception:
            found = ""
        if not found:
            return self.value or "未知"
        self.value, self.read_at = found, self.clock()
        return found
