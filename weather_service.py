"""Bounded weather workers; request timeouts never wait for executor shutdown.

Only in-flight requests are shared. Successful forecast/observation caching stays
in the existing providers. At most four distinct jobs run, with no waiting queue.
"""
from concurrent.futures import ThreadPoolExecutor, Future, wait
from threading import Lock


class WeatherService:
    def __init__(self, loader, workers=4, timeout=12):
        self.loader, self.timeout, self.limit = loader, timeout, workers
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="weather")
        self.lock = Lock()
        self.pending = {}

    def submit(self, date, location):
        key = (date, location or "")
        with self.lock:
            if key in self.pending:
                return self.pending[key]
            if len(self.pending) >= self.limit:
                future = Future()
                future.set_result({"error": "天氣服務忙碌中，請稍後重試。", "status_code": 503})
                return future
            future = self.pool.submit(self.loader, date, location)
            self.pending[key] = future
        def release(done):
            with self.lock:
                if self.pending.get(key) is done:
                    self.pending.pop(key)
        future.add_done_callback(release)
        return future

    def get_many(self, dates, location=None):
        futures = [self.submit(date, location) for date in dates]
        done, _ = wait(futures, timeout=self.timeout)
        results = []
        for future in futures:
            if future not in done:
                results.append({"error": "天氣服務回應逾時，請稍後重試。", "status_code": 504})
            else:
                try:
                    results.append(future.result())
                except Exception:
                    results.append({"error": "無法取得天氣資料，請稍後重試。", "status_code": 502})
        return results


def _load(date, location):
    from weather_api import get_weather_summary
    return get_weather_summary(date, location)


weather_service = WeatherService(_load)
