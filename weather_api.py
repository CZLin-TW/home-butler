"""
中央氣象署開放資料 API 封裝模組
- 鄉鎮天氣預報（3天逐3小時）
- 支援查詢今日/明日天氣
- 支援全台 22 縣市鄉鎮查詢
"""

import httpx
import os
from datetime import datetime, timedelta
import pytz

CWA_API_KEY = os.environ.get("CWA_API_KEY", "")
BASE_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
TZ = pytz.timezone("Asia/Taipei")

# 預設地點
DEFAULT_LOCATION = "竹北市"

# 全台 22 縣市 → 鄉鎮預報 data_id
CITY_DATA_ID = {
    "宜蘭縣": "F-D0047-001", "桃園市": "F-D0047-005",
    "新竹縣": "F-D0047-009", "苗栗縣": "F-D0047-013",
    "彰化縣": "F-D0047-017", "南投縣": "F-D0047-021",
    "雲林縣": "F-D0047-025", "嘉義縣": "F-D0047-029",
    "屏東縣": "F-D0047-033", "臺東縣": "F-D0047-037",
    "花蓮縣": "F-D0047-041", "澎湖縣": "F-D0047-045",
    "基隆市": "F-D0047-049", "新竹市": "F-D0047-053",
    "嘉義市": "F-D0047-057", "臺北市": "F-D0047-061",
    "高雄市": "F-D0047-065", "新北市": "F-D0047-069",
    "臺中市": "F-D0047-073", "臺南市": "F-D0047-077",
    "連江縣": "F-D0047-081", "金門縣": "F-D0047-085",
}


def _normalize(text):
    """台→臺 正規化"""
    return text.replace("台", "臺")


def _fetch_forecast(data_id, location_name=None):
    """從氣象署 API 抓取鄉鎮預報原始資料"""
    try:
        params = {
            "Authorization": CWA_API_KEY,
            "elementName": "Wx,MinT,MaxT,PoP12h,T,WeatherDescription",
        }
        if location_name:
            params["locationName"] = location_name

        resp = httpx.get(
            f"{BASE_URL}/{data_id}",
            params=params,
            timeout=15,
            verify=False,
        )
        data = resp.json()

        if data.get("success") != "true":
            return {"error": data.get("records", {}).get("msg", "API 回傳失敗")}

        locations_list = data.get("records", {}).get("Locations", [])
        if not locations_list:
            return {"error": "無預報資料"}

        location_array = locations_list[0].get("Location", [])
        if not location_array:
            return {"error": f"找不到「{location_name or '該地區'}」的預報資料"}

        return {
            "data": location_array[0],
            "city": locations_list[0].get("LocationsName", ""),
        }

    except Exception as e:
        return {"error": str(e)}


def _resolve_location(location):
    """
    解析地點，回傳 (data_id, location_name)
    支援：
    - 「竹北市」→ (F-D0047-009, 竹北市)
    - 「竹北」→ 嘗試竹北市/竹北區/竹北鄉/竹北鎮
    - 「新竹縣」→ (F-D0047-009, None) 回傳第一個鄉鎮
    - 「莿桐鄉」→ 遍歷找到雲林縣
    """
    loc = _normalize(location)

    # 情況1：完全是縣市名
    if loc in CITY_DATA_ID:
        return CITY_DATA_ID[loc], None

    # 情況2：開頭包含縣市名（例如「新竹縣竹北市」）
    for city, data_id in CITY_DATA_ID.items():
        if loc.startswith(city):
            town = loc[len(city):]
            return data_id, town if town else None

    # 情況3：只有鄉鎮名，需要猜縣市
    # 建立候選名稱
    candidates = [loc]
    if not any(loc.endswith(s) for s in ["市", "區", "鄉", "鎮"]):
        candidates.extend([loc + "市", loc + "區", loc + "鄉", loc + "鎮"])

    # 優先查新竹縣/新竹市（預設地區），再查其他
    priority = ["新竹縣", "新竹市"]
    search_order = priority + [c for c in CITY_DATA_ID if c not in priority]

    for city in search_order:
        data_id = CITY_DATA_ID[city]
        for candidate in candidates:
            result = _fetch_forecast(data_id, candidate)
            if "error" not in result:
                return data_id, candidate

    return None, None


def _parse_element(weather_elements, element_name):
    """從 WeatherElement 陣列中取出指定元素的時間序列"""
    for elem in weather_elements:
        if elem.get("ElementName") == element_name:
            return elem.get("Time", [])
    return []


def _get_value(period):
    """從單一時間區段取出值"""
    values = period.get("ElementValue", [])
    if not values:
        return None
    v = values[0]
    for key in ["Temperature", "Weather", "WeatherDescription",
                 "MinT", "MaxT", "ProbabilityOfPrecipitation"]:
        if key in v:
            return v[key]
    return list(v.values())[0] if v else None


def _collect_day(time_series, target_date):
    """收集某天所有時段的值"""
    results = []
    for period in time_series:
        time_str = period.get("DataTime") or period.get("StartTime", "")
        if not time_str:
            continue
        try:
            dt = datetime.strptime(time_str[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
        if dt.date() == target_date:
            results.append({"hour": dt.hour, "value": _get_value(period)})
    return results


def get_weather_summary(target="today", location=None):
    """
    取得天氣摘要
    target: "today" 或 "tomorrow"
    location: 地點名稱（鄉鎮或縣市），None 則用預設竹北市
    """
    if not location:
        location = DEFAULT_LOCATION

    data_id, loc_name = _resolve_location(location)
    if data_id is None:
        return {"error": f"找不到「{location}」的天氣資料，請確認地名是否正確"}

    result = _fetch_forecast(data_id, loc_name)
    if "error" in result:
        return result

    loc_data = result["data"]
    actual_name = loc_data.get("LocationName", loc_name or "")
    city_name = result.get("city", "")
    elements = loc_data.get("WeatherElement", [])

    now = datetime.now(TZ)
    target_date = (now + timedelta(days=1)).date() if target == "tomorrow" else now.date()

    # 天氣現象
    wx_values = _collect_day(_parse_element(elements, "天氣現象"), target_date)
    daytime = [v["value"] for v in wx_values if 6 <= v["hour"] <= 18 and v["value"]]
    wx = daytime[len(daytime) // 2] if daytime else (wx_values[0]["value"] if wx_values else "無資料")

    # 溫度
    temp_values = _collect_day(_parse_element(elements, "溫度"), target_date)
    temps = [int(v["value"]) for v in temp_values if v["value"] is not None]
    min_t = min(temps) if temps else None
    max_t = max(temps) if temps else None

    # 降雨機率
    pop_values = _collect_day(_parse_element(elements, "3小時降雨機率"), target_date)
    pops = [int(v["value"]) for v in pop_values if v["value"] is not None]
    max_pop = max(pops) if pops else None

    date_label = "今天" if target == "today" else "明天"

    return {
        "location": actual_name,
        "city": city_name,
        "date_label": date_label,
        "date": target_date.strftime("%m/%d"),
        "wx": wx,
        "min_t": min_t,
        "max_t": max_t,
        "pop": max_pop,
    }


def format_weather(summary):
    """將天氣摘要格式化為人類可讀文字"""
    if "error" in summary:
        return f"❌ 無法取得天氣資料：{summary['error']}"

    loc = summary["location"]
    city = summary.get("city", "")
    display = f"{city}{loc}" if city and not loc.startswith(city) else loc

    lines = [f"🌤️ {display}{summary['date_label']}（{summary['date']}）天氣"]
    lines.append(f"天氣：{summary['wx']}")

    if summary["min_t"] is not None and summary["max_t"] is not None:
        lines.append(f"溫度：{summary['min_t']}~{summary['max_t']}°C")

    if summary["pop"] is not None:
        lines.append(f"降雨機率：{summary['pop']}%")
        if summary["pop"] >= 70:
            lines.append("☔ 記得帶傘！")
        elif summary["pop"] >= 40:
            lines.append("🌂 建議帶把傘以防萬一")

    return "\n".join(lines)


def get_today_weather_text(location=None):
    """取得今日天氣的格式化文字"""
    return format_weather(get_weather_summary("today", location))


def get_tomorrow_weather_text(location=None):
    """取得明日天氣的格式化文字"""
    return format_weather(get_weather_summary("tomorrow", location))


def get_weather_data_for_notify(target="today", location=None):
    """取得天氣摘要字串（給 Claude 組推播訊息用）"""
    summary = get_weather_summary(target, location)
    if "error" in summary:
        return None

    parts = [f"{summary['location']}{summary['date_label']}天氣：{summary['wx']}"]
    if summary["min_t"] is not None and summary["max_t"] is not None:
        parts.append(f"溫度 {summary['min_t']}~{summary['max_t']}°C")
    if summary["pop"] is not None:
        parts.append(f"降雨機率 {summary['pop']}%")

    return "，".join(parts)