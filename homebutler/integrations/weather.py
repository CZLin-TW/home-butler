"""Central Weather Administration (中央氣象署) open data: the week's township forecast and station readings.

Carried over from the old client (weather_api.py, observation_api.py):
- A place is a county/city, "county + township", or a township alone (tried with 市/區/鄉/鎮, Hsinchu first).
- Only successful answers are cached: forecasts 30 minutes, station readings 10 minutes. A failure is
  never cached, so a recovered service is used at once.
- One question has 10 seconds in total across all its requests.
- The forecast's "today": daytime weather first; lowest/highest temperature and apparent temperature;
  the highest chance of rain.

Changed: certificates are verified (the old client turned verification off); only the strict X.509
check that the agency's certificate fails is relaxed.
"""
from __future__ import annotations

import ssl
import threading
import time
from datetime import date, datetime, timedelta, tzinfo

BASE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
DEFAULT_LOCATION = "竹北市"
BUDGET_SECONDS = 10
FORECAST_TTL, OBSERVATION_TTL = 1800, 600
WEEKDAY = "一二三四五六日"
CITY_DATA_ID = {
    "宜蘭縣": "F-D0047-003", "桃園市": "F-D0047-007", "新竹縣": "F-D0047-011", "苗栗縣": "F-D0047-015",
    "彰化縣": "F-D0047-019", "南投縣": "F-D0047-023", "雲林縣": "F-D0047-027", "嘉義縣": "F-D0047-031",
    "屏東縣": "F-D0047-035", "臺東縣": "F-D0047-039", "花蓮縣": "F-D0047-043", "澎湖縣": "F-D0047-047",
    "基隆市": "F-D0047-051", "新竹市": "F-D0047-055", "嘉義市": "F-D0047-059", "臺北市": "F-D0047-063",
    "高雄市": "F-D0047-067", "新北市": "F-D0047-071", "臺中市": "F-D0047-075", "臺南市": "F-D0047-079",
    "連江縣": "F-D0047-083", "金門縣": "F-D0047-087",
}
STATIONS = {
    "臺北市": "臺北", "新北市": "板橋", "板橋區": "板橋", "基隆市": "基隆", "桃園市": "新屋", "新竹縣": "新竹", "新竹市": "新竹",
    "竹北市": "新竹", "苗栗縣": "竹南", "臺中市": "臺中", "彰化縣": "田中", "南投縣": "日月潭", "雲林縣": "古坑",
    "嘉義縣": "嘉義", "嘉義市": "嘉義", "臺南市": "臺南", "高雄市": "高雄", "屏東縣": "恒春", "宜蘭縣": "宜蘭",
    "花蓮縣": "花蓮", "臺東縣": "臺東", "澎湖縣": "澎湖", "金門縣": "金門", "連江縣": "馬祖",
}


class OutOfTime(TimeoutError):
    pass


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    return context


def _norm(text: str) -> str:
    return str(text or "").strip().replace("台", "臺")


def _start(period) -> datetime | None:
    try:
        return datetime.strptime(str(period.get("StartTime", ""))[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _value(period):
    values = period.get("ElementValue") or []
    if not values or not values[0]:
        return None
    first = values[0]
    for key in ("Weather", "MinTemperature", "MaxTemperature", "ProbabilityOfPrecipitation", "WeatherDescription"):
        if key in first:
            return first[key]
    return next(iter(first.values()))


def _number(value) -> int | None:
    if value in (None, "", "-"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _series(elements, name):
    return next((e.get("Time", []) for e in elements if e.get("ElementName") == name), [])


def _day(series, day: date) -> list[tuple[int, object]]:
    return [(s.hour, _value(p)) for p in series if (s := _start(p)) and s.date() == day]


def _current(series, now: datetime):
    """The value of the 12-hour period covering now; else the latest past one; else the first future one."""
    timed = [(start, p) for p in series if (start := _start(p))]
    if not timed:
        return None
    for start, period in timed:
        if start <= now < start + timedelta(hours=12):
            return _value(period)
    past = sorted((t for t in timed if t[0] <= now), key=lambda t: t[0], reverse=True)
    return _value((past or sorted(timed, key=lambda t: t[0]))[0][1])


def _window(series, start: datetime, end: datetime) -> list:
    return [v for p in series if (s := _start(p)) and s < end and s + timedelta(hours=12) > start and (v := _value(p)) is not None]


def _notable(values: list):
    """Rain first, then overcast, else the first."""
    for word in ("雨", "陰"):
        for value in values:
            if word in str(value):
                return value
    return values[0] if values else None


class Cache:
    def __init__(self, ttl: float, clock=time.monotonic, limit: int = 32):
        self.ttl, self.clock, self.limit = ttl, clock, limit
        self.items: dict = {}
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            found = self.items.get(key)
            return found[1] if found and self.clock() - found[0] < self.ttl else None

    def set(self, key, value):
        with self.lock:
            self.items.pop(key, None)
            self.items[key] = (self.clock(), value)
            while len(self.items) > self.limit:
                self.items.pop(next(iter(self.items)))


class Weather:
    def __init__(self, api_key: str, http, zone: tzinfo, clock=time.monotonic, now=None):
        self.api_key, self.http, self.zone, self.clock = api_key, http, zone, clock
        self.now = now or (lambda: datetime.now(zone))
        self.forecasts, self.observations = Cache(FORECAST_TTL, clock), Cache(OBSERVATION_TTL, clock)
        self.places = Cache(24 * 3600, clock)          # a township found once is not searched for again

    def _left(self, deadline: float, most: float) -> float:
        left = deadline - self.clock()
        if left <= 0:
            raise OutOfTime()
        return min(most, left)

    def _forecast(self, data_id: str, place: str | None, deadline: float) -> dict:
        key = (data_id, place)
        if (cached := self.forecasts.get(key)) is not None:
            return cached
        params = {"Authorization": self.api_key, "elementName": "Wx,MinT,MaxT,MinAT,MaxAT,PoP12h,RH,WeatherDescription"}
        if place:
            params["locationName"] = place
        try:
            data = self.http.get(f"{BASE}/{data_id}", params=params, timeout=self._left(deadline, 15)).json()
        except OutOfTime:
            raise
        except Exception as error:
            return {"error": f"氣象署沒有回應（{type(error).__name__}）"}
        if data.get("success") != "true":
            return {"error": (data.get("records") or {}).get("msg", "API 回傳失敗")}
        groups = (data.get("records") or {}).get("Locations") or []
        places = groups[0].get("Location", []) if groups else []
        chosen = [p for p in places if p.get("LocationName") == place] if place else places[:1]
        if not chosen:
            return {"error": f"找不到「{place or '該地區'}」的預報資料"}
        result = {"data": chosen[0], "city": groups[0].get("LocationsName", "")}
        self.forecasts.set(key, result)
        return result

    def _resolve(self, location: str, deadline: float) -> tuple[str | None, str | None]:
        place = _norm(location)
        if place in CITY_DATA_ID:
            return CITY_DATA_ID[place], None
        for city, data_id in CITY_DATA_ID.items():
            if place.startswith(city):
                return data_id, place[len(city):] or None
        if (known := self.places.get(place)) is not None:
            return known
        names = [place] + ([] if place.endswith(("市", "區", "鄉", "鎮")) else [place + s for s in "市區鄉鎮"])
        for city in ["新竹縣", "新竹市"] + [c for c in CITY_DATA_ID if c not in ("新竹縣", "新竹市")]:
            for name in names:
                if "error" not in self._forecast(CITY_DATA_ID[city], name, deadline):
                    self.places.set(place, (CITY_DATA_ID[city], name))
                    return CITY_DATA_ID[city], name
        return None, None

    def observation(self, location: str, deadline: float) -> dict | None:
        place = _norm(location)
        station = STATIONS.get(place) or next((s for k, s in STATIONS.items() if k in place or place in k), None)
        if not station:
            return None
        if (cached := self.observations.get(station)) is not None:
            return cached
        try:
            data = self.http.get(f"{BASE}/O-A0003-001", params={"Authorization": self.api_key, "StationName": station},
                                 timeout=self._left(deadline, 10)).json()
            found = ((data.get("records") or {}).get("Station") or [None])[0] if data.get("success") == "true" else None
        except Exception:
            return None
        if not found:
            return None
        elements = found.get("WeatherElement") or {}

        def valid(value):
            try:
                return float(value) > -90               # the agency marks a missing value -99
            except (TypeError, ValueError):
                return False
        temp = float(elements["AirTemperature"]) if valid(elements.get("AirTemperature")) else None
        humidity = int(float(elements["RelativeHumidity"])) if valid(elements.get("RelativeHumidity")) else None
        if temp is None and humidity is None:
            return None
        observed = str((found.get("ObsTime") or {}).get("DateTime", ""))
        result = {"station": found.get("StationName", station), "temp": temp, "humidity": humidity,
                  "observed_at": observed[11:16] if len(observed) >= 16 else observed}
        self.observations.set(station, result)
        return result

    def summary_data(self, when: str = "today", location: str | None = None) -> dict:
        """The forecast for one day (today … 7 days on), plus the station reading now; or {"error"}."""
        deadline = self.clock() + BUDGET_SECONDS
        location = location or DEFAULT_LOCATION
        now = self.now()
        try:
            day = (now.date() if when in (None, "", "today") else (now + timedelta(days=1)).date() if when == "tomorrow"
                   else datetime.strptime(when, "%Y-%m-%d").date())
        except ValueError:
            day = now.date()
        ahead = (day - now.date()).days
        if ahead < 0:
            return {"error": "無法查詢過去的天氣"}
        if ahead > 7:
            return {"error": "最多只能查詢未來 7 天的天氣"}
        try:
            data_id, place = self._resolve(location, deadline)
            if data_id is None:
                return {"error": f"找不到「{location}」的天氣資料，請確認地名是否正確"}
            found = self._forecast(data_id, place, deadline)
            if "error" in found:
                return found
            observation = self.observation(location, deadline)
        except OutOfTime:
            return {"error": "天氣服務回應逾時，請稍後重試。"}
        elements = found["data"].get("WeatherElement", [])
        weather = _day(_series(elements, "天氣現象"), day)
        daytime = [v for h, v in weather if 6 <= h <= 18 and v]

        def numbers(name):
            return [n for _, v in _day(_series(elements, name), day) if (n := _number(v)) is not None]
        lows, highs = numbers("最低溫度"), numbers("最高溫度")
        feel_low, feel_high, rain = numbers("最低體感溫度"), numbers("最高體感溫度"), numbers("12小時降雨機率")
        label = {0: "今天", 1: "明天", 2: "後天"}.get(ahead, f"週{WEEKDAY[day.weekday()]}")
        naive, ahead24 = now.replace(tzinfo=None), now.replace(tzinfo=None) + timedelta(hours=24)

        def window_numbers(name):
            return [n for v in _window(_series(elements, name), naive, ahead24) if (n := _number(v)) is not None]
        forecast = {
            "current_segment": {"wx": _current(_series(elements, "天氣現象"), naive),
                                **{key: _number(_current(_series(elements, name), naive)) for key, name in
                                   (("min_t", "最低溫度"), ("max_t", "最高溫度"), ("pop", "12小時降雨機率"), ("rh", "平均相對濕度"))}},
            "next_24h": {"wx": _notable(_window(_series(elements, "天氣現象"), naive, ahead24)),
                         "min_t": min(window_numbers("最低溫度"), default=None), "max_t": max(window_numbers("最高溫度"), default=None),
                         "pop": max(window_numbers("12小時降雨機率"), default=None)}}
        return {"location": found["data"].get("LocationName", place or ""), "city": found.get("city", ""), "date_label": label,
                "date": day.strftime("%m/%d"), "wx": daytime[0] if daytime else (weather[0][1] if weather else "無資料"),
                "min_t": min(lows) if lows else None, "max_t": max(highs) if highs else None,
                "min_at": min(feel_low) if feel_low else None, "max_at": max(feel_high) if feel_high else None,
                "pop": max(rain) if rain else None, "observation": observation, "forecast": forecast}

    def summary(self, when: str = "today", location: str | None = None) -> str:
        """The butler's answer (format as before)."""
        found = self.summary_data(when, location)
        if "error" in found:
            return f"❌ 無法取得天氣資料：{found['error']}"
        place = found["location"] if not found["city"] or found["location"].startswith(found["city"]) else found["city"] + found["location"]
        lines = [f"🌤️ {place}{found['date_label']}（{found['date']}）天氣", f"天氣：{found['wx']}"]
        if found["min_t"] is not None and found["max_t"] is not None:
            lines.append(f"溫度：{found['min_t']}~{found['max_t']}°C")
        if found["min_at"] is not None and found["max_at"] is not None:
            lines.append(f"體感溫度：{found['min_at']}~{found['max_at']}°C")
        if found["pop"] is not None:
            lines.append(f"降雨機率：{found['pop']}%")
            if found["pop"] >= 70:
                lines.append("☔ 記得帶傘！")
            elif found["pop"] >= 40:
                lines.append("🌂 建議帶把傘以防萬一")
        reading = found.get("observation")
        if reading:
            parts = [f"{reading['temp']}°C" if reading.get("temp") is not None else "",
                     f"{reading['humidity']}%" if reading.get("humidity") is not None else ""]
            lines.append(f"當下（{reading.get('observed_at', '')} {reading.get('station', '')}測站）：" + " ".join(p for p in parts if p))
        return "\n".join(lines)

    def for_notice(self, when: str = "tomorrow", location: str | None = None) -> str | None:
        """One line for the daily summary, or None."""
        found = self.summary_data(when, location)
        if "error" in found:
            return None
        parts = [f"{found['location']}{found['date_label']}天氣：{found['wx']}"]
        if found["min_t"] is not None and found["max_t"] is not None:
            parts.append(f"溫度 {found['min_t']}~{found['max_t']}°C")
        if found["min_at"] is not None and found["max_at"] is not None:
            parts.append(f"體感溫度 {found['min_at']}~{found['max_at']}°C")
        if found["pop"] is not None:
            parts.append(f"降雨機率 {found['pop']}%")
        return "，".join(parts)
