import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from homebutler.integrations.weather import Weather

ZONE = ZoneInfo("Asia/Taipei")


def element(name, key, values):
    return {"ElementName": name, "Time": [{"StartTime": start, "ElementValue": [{key: value}]} for start, value in values]}


FORECAST = {"success": "true", "records": {"Locations": [{"LocationsName": "某縣", "Location": [{"LocationName": "某鄉", "WeatherElement": [
    element("天氣現象", "Weather", [("2026-10-21T00:00:00", "晴"), ("2026-10-21T06:00:00", "短暫雨")]),
    element("最低溫度", "MinTemperature", [("2026-10-21T00:00:00", "21"), ("2026-10-21T06:00:00", "23")]),
    element("最高溫度", "MaxTemperature", [("2026-10-21T00:00:00", "27"), ("2026-10-21T06:00:00", "30")]),
    element("12小時降雨機率", "ProbabilityOfPrecipitation", [("2026-10-21T06:00:00", "70"), ("2026-10-21T18:00:00", "-")]),
]}]}]}}


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


class WeatherTests(unittest.TestCase):
    def make(self, answer):
        self.calls, self.clock = [], Clock()

        def handle(request):
            self.calls.append(request.url.params.get("locationName"))
            return answer(request)
        return Weather("key", httpx.Client(transport=httpx.MockTransport(handle)), ZONE, clock=self.clock,
                       now=lambda: datetime(2026, 10, 21, 9, tzinfo=ZONE))

    def test_a_township_alone_is_found_and_the_day_summarised(self):
        def answer(request):
            if request.url.path.endswith("O-A0003-001"):
                return httpx.Response(200, json={"success": "false"})
            ok = request.url.params.get("locationName") == "某鄉" and request.url.path.endswith("F-D0047-011")
            return httpx.Response(200, json=FORECAST if ok else {"success": "true", "records": {"Locations": [{"Location": []}]}})
        weather = self.make(answer)
        text = weather.summary("today", "某")
        self.assertEqual(text, "🌤️ 某縣某鄉今天（10/21）天氣\n天氣：短暫雨\n溫度：21~30°C\n降雨機率：70%\n☔ 記得帶傘！")
        asked = len(self.calls)
        weather.summary("today", "某")
        self.assertEqual(len(self.calls), asked)                  # cached
        self.assertEqual(weather.summary("2026-11-30"), "❌ 無法取得天氣資料：最多只能查詢未來 7 天的天氣")

    def test_failures_are_not_cached_and_time_is_bounded(self):
        weather = self.make(lambda request: httpx.Response(500, text="no"))
        self.assertIn("❌", weather.summary("today", "新竹縣"))
        self.assertIn("❌", weather.summary("today", "新竹縣"))
        self.assertEqual(len(self.calls), 2)

        def slow(request):
            self.clock.t += 11
            return httpx.Response(200, json={"success": "true", "records": {"Locations": [{"Location": []}]}})
        weather = self.make(slow)
        self.assertIn("逾時", weather.summary("today", "某"))
        self.assertLess(len(self.calls), 3)
