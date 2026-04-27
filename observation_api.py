"""
中央氣象署觀測資料 API 封裝（O-A0003-001 自動氣象站-氣象觀測資料）

跟 weather_api.py 的預報資料互補：
- weather_api 給 12 小時段預報，回答「未來幾小時/幾天會怎樣」
- 這個模組給測站即時讀值，回答「現在到底幾度、濕度多少」
"""

import httpx
import os

CWA_API_KEY = os.environ.get("CWA_API_KEY", "")
BASE_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0003-001"


# 台灣主要地區 → CWA 觀測站對應表。
# 家庭使用優先覆蓋：使用者住地（竹北 = 新竹氣象站，實際位置就在竹北）+ 全台 22 縣市主站。
# 找不到對應的鄉鎮會 fallback 到預報（不是顯示錯誤）。
LOCATION_STATION_MAP = {
    # 北部
    "臺北市": "臺北", "台北市": "臺北",
    "新北市": "板橋", "板橋區": "板橋",
    "基隆市": "基隆",
    "桃園市": "新屋",
    "新竹縣": "新竹", "新竹市": "新竹",
    "竹北市": "新竹",  # 新竹氣象站實際就在竹北
    "苗栗縣": "竹南",
    # 中部
    "臺中市": "臺中", "台中市": "臺中",
    "彰化縣": "田中",
    "南投縣": "日月潭",
    "雲林縣": "古坑",
    # 南部
    "嘉義縣": "嘉義", "嘉義市": "嘉義",
    "臺南市": "臺南", "台南市": "臺南",
    "高雄市": "高雄",
    "屏東縣": "恒春",
    # 東部
    "宜蘭縣": "宜蘭",
    "花蜿縣": "花蜿",
    "臺東縣": "臺東", "台東縣": "臺東",
    # 離島
    "澎湖縣": "澎湖",
    "金門縣": "金門",
    "連江縣": "馬祖",
}


def _normalize(text):
    """台→臺 正規化（跟 weather_api.py 一致）"""
    return text.replace("台", "臺")


def _is_valid(value):
    """CWA 缺值用 -99 或 -99.0 等標示，真正有效值 > -90。"""
    try:
        return float(value) > -90
    except (ValueError, TypeError):
        return False


def find_station(location):
    """根據使用者輸入的地點找對應觀測站。

    三層策略：
    1. 直接查 LOCATION_STATION_MAP（exact match）
    2. Contains match（測站 key 出現在 location 中、或反之）
    3. 找不到 → 回傳 None（caller 應 fallback 到預報資料）
    """
    if not location:
        return None
    loc = _normalize(location.strip())

    # 1. 直接查表
    if loc in LOCATION_STATION_MAP:
        return LOCATION_STATION_MAP[loc]

    # 2. Contains match
    for key, station in LOCATION_STATION_MAP.items():
        if key in loc or loc in key:
            return station

    return None


def get_observation(station_name):
    """以測站名稱查 CWA 觀測資料。

    成功回傳：{
        "station": 測站名稱,
        "temp": 當下溫度 (float, °C),
        "humidity": 當下濕度 (int, %),
        "observed_at": 觀測時間 HH:MM,
    }
    API 失敗、找不到測站、或所有欄位都是缺值 → 回傳 None。
    """
    if not station_name:
        return None
    try:
        # TODO: verify=False 跳過 TLS 驗證。歷史原因待釐清（可能是 CWA 憑證鎖見過問題），
        # 待研究後改為 verify=True。並修訂 weather_api.py 同一安全障隄。
        resp = httpx.get(
            BASE_URL,
            params={"Authorization": CWA_API_KEY, "StationName": station_name},
            timeout=10,
            verify=False,
        )
        data = resp.json()
        if data.get("success") != "true":
            return None
        stations = data.get("records", {}).get("Station", [])
        if not stations:
            return None
        s = stations[0]
        elements = s.get("WeatherElement", {}) or {}
        temp_raw = elements.get("AirTemperature")
        hum_raw = elements.get("RelativeHumidity")
        obs_time = (s.get("ObsTime", {}) or {}).get("DateTime", "")

        temp = float(temp_raw) if _is_valid(temp_raw) else None
        humidity = int(float(hum_raw)) if _is_valid(hum_raw) else None

        # 兩欄都缺就等於沒用，回 None 讓 caller fallback
        if temp is None and humidity is None:
            return None

        # observed_at 取 HH:MM（完整字串形如 "2026-04-19T23:40:00+08:00"）
        observed_at = obs_time[11:16] if len(obs_time) >= 16 else obs_time

        return {
            "station": s.get("StationName", station_name),
            "temp": temp,
            "humidity": humidity,
            "observed_at": observed_at,
        }
    except Exception as e:
        print(f"[OBSERVATION] {station_name} error: {e}")
        return None


def get_observation_for_location(location):
    """一步到位：地點字串 → 觀測資料（找不到對應測站或讀取失敗回 None）"""
    station = find_station(location)
    if not station:
        return None
    return get_observation(station)
