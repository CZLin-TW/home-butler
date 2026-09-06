import time
import unittest
from threading import Event
from weather_service import WeatherService
from weather_budget import weather_query, remaining_timeout, _deadline, WeatherTimeout


class WeatherServiceTests(unittest.TestCase):
    def test_timeout_returns_without_waiting_for_worker_and_caps_queue(self):
        release = Event()
        service = WeatherService(lambda *a: release.wait(2), workers=1, timeout=0.02)
        try:
            start = time.monotonic()
            self.assertEqual(service.get_many(["today"])[0]["status_code"], 504)
            self.assertLess(time.monotonic() - start, 0.3)
            self.assertIs(service.submit("today", None), service.submit("today", None))
            self.assertEqual(service.get_many(["tomorrow"])[0]["status_code"], 503)
            self.assertEqual(len(service.pending), 1)
        finally:
            release.set()
            service.pool.shutdown()

    def test_errors_release_capacity_and_recovery_is_not_cached(self):
        service = WeatherService(lambda *a: (_ for _ in ()).throw(ValueError()), workers=1)
        try:
            self.assertEqual(service.get_many(["today"])[0]["status_code"], 502)
            service.loader = lambda *a: {"wx": "sunny"}
            self.assertEqual(service.get_many(["today"]), [{"wx": "sunny"}])
        finally:
            service.pool.shutdown()

    def test_deadline_stops_further_location_requests_and_resets(self):
        @weather_query
        def lookup():
            self.assertLessEqual(remaining_timeout(), 10)
            _deadline.set(time.monotonic() - 1)
            remaining_timeout()
        self.assertEqual(lookup()["status_code"], 504)
        self.assertEqual(remaining_timeout(), 15)


if __name__ == "__main__": unittest.main()
