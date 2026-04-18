"""
中央氣象署開放資料 API 封裝模組
- 鄉鎮天氣預報（一週，每 12 小時）
- 支援指定日期查詢（最多 7 天）
- 支援全台 22 縣市鄉鎮查詢
"""

import httpx
import os
from datetime import datetime, timedelta
import pytz
from observation_api import get_observation_for_location

CWA_API_KEY = os.environ.get("CWA_API_KEY", "")
BASE_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
TZ = pytz.timezone("Asia/Taipei")

# 預設地點
DEFAULT_LOCATION = "竹北市"

# 全台 22 縣市 → 一週鄉鎮預報 data_id
CITY_DATA_ID = {
    "宜蘭縣": "F-D0047-003", "桃園市": "F-D0047-007",
    "新竹縣": "F-D0047-011", "苗栗縣": "F-D0047-015",
    "彰化縣": "F-D0047-019", "南投縣": "F-D0047-023",
    "雲林縣": "F-D0047-027", "嘉義縣": "F-D0047-031",
    "屏東縣": "F-D0047-035", "臺東縣": "F-D0047-039",
    "花蓮縣": "F-D0047-043", "澎湖縣": "F-D0047-047",
    "基隆市": "F-D0047-051", "新竹市": "F-D0047-055",
    "嘉義市": "F-D0047-059", "臺北市": "F-D0047-063",
    "高雄市": "F-D0047-067", "新北市": "F-D0047-071",
    "臺中市": "F-D0047-075", "臺南市": "F-D0047-079",
    "連江縣": "F-D0047-083", "金門縣": "F-D0047-087",
}


def _normalize(text):
    """台→臺 正規化"""
    return text.replace("台", "臺")


def _fetch_forecast(data_id, location_name=None):
    """從氣象署 API 抓取一週鄉鎮預報原始資料"""
    try:
        params = {
            "Authorization": CWA_API_KEY,
            "elementName": "Wx,MinT,MaxT,MinAT,MaxAT,PoP12h,RH,WeatherDescription",
            
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

        if data.get("success") != "true":  # 氣象署 API 回傳字串 "true"，非布林值
            return {"error": data.get("records", {}).get("msg", "API 回傳失敗")}

        locations_list = data.get("records", {}).get("Locations", [])
        if not locations_list:
            return {"error": "無預報資料"}

        location_array = locations_list[0].get("Location", [])
        if not location_array:
            return {"error": f"找不到「{location_name or '該地區'}」的預報資料"}

        # 驗證 locationName 匹配
        if location_name:
            matched = [loc for loc in location_array if loc.get("LocationName") == location_name]
            if matched:
                target_loc = matched[0]
            else:
                return {"error": f"找不到「{location_name}」的預報資料"}
        else:
            target_loc = location_array[0]

        return {
            "data": target_loc,
            "city": locations_list[0].get("LocationsName", ""),
        }

    except Exception as e:
        return {"error": str(e)}


def _resolve_location(location):
    """
    解析地點，回傳 (data_id, location_name)
    支援：竹北市、竹北、新竹縣竹北市、新竹市東區、莿桐鄉
    """
    loc = _normalize(location)

    # 情況1：完全是縣市名
    if loc in CITY_DATA_ID:
        return CITY_DATA_ID[loc], None

    # 情況2：開頭包含縣市名
    for city, data_id in CITY_DATA_ID.items():
        if loc.startswith(city):
            town = loc[len(city):]
            return data_id, town if town else None

    # 情況3：只有鄉鎮名，遍歷所有縣市
    candidates = [loc]
    if not any(loc.endswith(s) for s in ["市", "區", "鄉", "鎮"]):
        candidates.extend([loc + "市", loc + "區", loc + "鄉", loc + "鎮"])

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
    for key in ["Weather", "MinTemperature", "MaxTemperature",
                 "ProbabilityOfPrecipitation", "WeatherDescription"]:
        if key in v:
            return v[key]
    return list(v.values())[0] if v else None  # v 為空字典時 if v 為 False，不會 IndexError


def _find_current_value(time_series, now_naive):
    """回傳「最貼近現在」的預報值，依序嘗試：
    1. 涵蓋 now 的那一段（每段 12 小時）
    2. 最近一段過去的（StartTime ≤ now 中最晚的）
    3. 最早的未來段（StartTime > now 中最早的）
    只有 time_series 完全為空才回 None。確保跨日界線時濕度等欄位不會消失。
    now_naive: 不帶 tzinfo 的 datetime，跟 CWA StartTime 格式一致。"""
    parsed = []
    for period in time_series:
        start_str = period.get("StartTime", "")
        if not start_str:
            continue
        try:
            start_dt = datetime.strptime(start_str[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
        parsed.append((start_dt, period))

    if not parsed:
        return None

    # 1. 涵蓋 now
    for start_dt, period in parsed:
        if start_dt <= now_naive < start_dt + timedelta(hours=12):
            return _get_value(period)

    # 2. 最近過去段
    past = [(s, p) for s, p in parsed if s <= now_naive]
    if past:
        past.sort(key=lambda x: x[0], reverse=True)
        return _get_value(past[0][1])

    # 3. 最早未來段
    parsed.sort(key=lambda x: x[0])
    return _get_value(parsed[0][1])


def _segments_in_window(time_series, start_naive, end_naive):
    """收集所有跟 [start, end) 有時間交集的預報段。
    每段涵蓋 [StartTime, StartTime+12hr)。"""
    results = []
    for period in time_series:
        start_str = period.get("StartTime", "")
        if not start_str:
            continue
        try:
            seg_start = datetime.strptime(start_str[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
        seg_end = seg_start + timedelta(hours=12)
        if seg_start < end_naive and seg_end > start_naive:
            value = _get_value(period)
            if value is not None:
                results.append({"value": value, "hour": seg_start.hour, "start": seg_start})
    return results


def _to_int(v):
    """把 CWA 回的值轉 int；缺值/空字串/None 時回 None。"""
    if v is None or v == "" or v == "-":
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def _pick_notable_wx(items):
    """從一堆 wx 候選中挑最突出的：下雨 > 陰 > 其他 > 第一個。"""
    if not items:
        return None
    rainy = [i for i in items if "雨" in str(i.get("value", ""))]
    if rainy:
        return rainy[0]["value"]
    cloudy = [i for i in items if "陰" in str(i.get("value", ""))]
    if cloudy:
        return cloudy[0]["value"]
    return items[0]["value"]


def _collect_day(time_series, target_date):
    """收集某天所有時段的值"""
    results = []
    for period in time_series:
        start_str = period.get("StartTime", "")
        if not start_str:
            continue
        try:
            dt = datetime.strptime(start_str[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
        if dt.date() == target_date:
            results.append({"hour": dt.hour, "value": _get_value(period)})
    return results


def _parse_date(date_str):
    """將日期字串解析為 date 物件，支援 YYYY-MM-DD"""
    now = datetime.now(TZ)
    if not date_str or date_str == "today":
        return now.date()
    if date_str == "tomorrow":
        return (now + timedelta(days=1)).date()
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return now.date()


def get_weather_summary(date_str="today", location=None):
    """
    取得天氣摘要
    date_str: "today"、"tomorrow"、或 "YYYY-MM-DD"
    location: 地點名稱
    """
    if not location:
        location = DEFAULT_LOCATION

    target_date = _parse_date(date_str)
    now = datetime.now(TZ)

    # 檢查日期範圍（最多 7 天）
    days_diff = (target_date - now.date()).days
    if days_diff < 0:
        return {"error": "無法查詢過去的天氣"}
    if days_diff > 7:
        return {"error": "最多只能查詢未來 7 天的天氣"}

    # 解析地點
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

    # 天氣現象（Wx）
    wx_values = _collect_day(_parse_element(elements, "天氣現象"), target_date)
    daytime = [v["value"] for v in wx_values if 6 <= v["hour"] <= 18 and v["value"]]
    wx = daytime[0] if daytime else (wx_values[0]["value"] if wx_values else "無資料")

    # 最低溫（MinT）
    mint_values = _collect_day(_parse_element(elements, "最低溫度"), target_date)
    mints = [int(v["value"]) for v in mint_values if v["value"] is not None]
    min_t = min(mints) if mints else None

    # 最高溫（MaxT）
    maxt_values = _collect_day(_parse_element(elements, "最高溫度"), target_date)
    maxts = [int(v["value"]) for v in maxt_values if v["value"] is not None]
    max_t = max(maxts) if maxts else None

    # 最低體感溫度（MinAT）
    minat_values = _collect_day(_parse_element(elements, "最低體感溫度"), target_date)
    minats = [int(float(v["value"])) for v in minat_values if v["value"] is not None and v["value"] != "" and v["value"] != "-"]
    min_at = min(minats) if minats else None

    # 最高體感溫度（MaxAT）
    maxat_values = _collect_day(_parse_element(elements, "最高體感溫度"), target_date)
    maxats = [int(float(v["value"])) for v in maxat_values if v["value"] is not None and v["value"] != "" and v["value"] != "-"]
    max_at = max(maxats) if maxats else None

    # 降雨機率（PoP12h）
    pop_values = _collect_day(_parse_element(elements, "12小時降雨機率"), target_date)
    pops = [int(v["value"]) for v in pop_values if v["value"] is not None and v["value"] != "" and v["value"] != "-"]
    max_pop = max(pops) if pops else None

    # ── 「當下」類資料（跟 target_date 無關，永遠基於 now）─────────────
    now_naive = now.replace(tzinfo=None)
    wx_series = _parse_element(elements, "天氣現象")
    mint_series = _parse_element(elements, "最低溫度")
    maxt_series = _parse_element(elements, "最高溫度")
    pop_series = _parse_element(elements, "12小時降雨機率")
    rh_series = _parse_element(elements, "平均相對濕度")

    # 當前段（涵蓋 now 的那 12 小時段）
    current_segment = {
        "wx": _find_current_value(wx_series, now_naive),
        "min_t": _to_int(_find_current_value(mint_series, now_naive)),
        "max_t": _to_int(_find_current_value(maxt_series, now_naive)),
        "pop": _to_int(_find_current_value(pop_series, now_naive)),
        "rh": _to_int(_find_current_value(rh_series, now_naive)),
    }

    # 未來 24 小時 window 內的所有段 aggregate
    win_end = now_naive + timedelta(hours=24)
    next24_wx = _segments_in_window(wx_series, now_naive, win_end)
    next24_mint = [_to_int(v["value"]) for v in _segments_in_window(mint_series, now_naive, win_end)]
    next24_maxt = [_to_int(v["value"]) for v in _segments_in_window(maxt_series, now_naive, win_end)]
    next24_pop = [_to_int(v["value"]) for v in _segments_in_window(pop_series, now_naive, win_end)]
    next24_mint = [x for x in next24_mint if x is not None]
    next24_maxt = [x for x in next24_maxt if x is not None]
    next24_pop = [x for x in next24_pop if x is not None]
    next_24h = {
        "wx": _pick_notable_wx(next24_wx),
        "min_t": min(next24_mint) if next24_mint else None,
        "max_t": max(next24_maxt) if next24_maxt else None,
        "pop": max(next24_pop) if next24_pop else None,
    }

    # 觀測（真實當下讀值，只對有對應測站的地點有效）
    observation = get_observation_for_location(location)

    # 日期標籤
    if days_diff == 0:
        date_label = "今天"
    elif days_diff == 1:
        date_label = "明天"
    elif days_diff == 2:
        date_label = "後天"
    else:
        weekday = ["一", "二", "三", "四", "五", "六", "日"]
        date_label = f"週{weekday[target_date.weekday()]}"

    return {
        "location": actual_name,
        "city": city_name,
        "date_label": date_label,
        "date": target_date.strftime("%m/%d"),
        # 目標日整天 aggregate（LINE bot 回答「今天/明天/某日」類查詢用）
        "wx": wx,
        "min_t": min_t,
        "max_t": max_t,
        "min_at": min_at,
        "max_at": max_at,
        "pop": max_pop,
        # 當下觀測（跟 target_date 無關、永遠是 now）
        "observation": observation,
        # 基於 now 的預報 sub-structures
        "forecast": {
            "current_segment": current_segment,
            "next_24h": next_24h,
        },
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

    if summary.get("min_at") is not None and summary.get("max_at") is not None:
        lines.append(f"體感溫度：{summary['min_at']}~{summary['max_at']}°C")

    if summary["pop"] is not None:
        lines.append(f"降雨機率：{summary['pop']}%")
        if summary["pop"] >= 70:
            lines.append("☔ 記得帶傘！")
        elif summary["pop"] >= 40:
            lines.append("🌂 建議帶把傘以防萬一")

    obs = summary.get("observation")
    if obs:
        parts = []
        if obs.get("temp") is not None:
            parts.append(f"{obs['temp']}°C")
        if obs.get("humidity") is not None:
            parts.append(f"{obs['humidity']}%")
        if parts:
            lines.append(f"當下（{obs.get('observed_at', '')} {obs.get('station', '')}測站）：" + " ".join(parts))

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
        print(f"[WEATHER DATA] {summary['error']}")
        return None

    parts = [f"{summary['location']}{summary['date_label']}天氣：{summary['wx']}"]
    if summary["min_t"] is not None and summary["max_t"] is not None:
        parts.append(f"溫度 {summary['min_t']}~{summary['max_t']}°C")
    if summary.get("min_at") is not None and summary.get("max_at") is not None:
        parts.append(f"體感溫度 {summary['min_at']}~{summary['max_at']}°C")
    if summary["pop"] is not None:
        parts.append(f"降雨機率 {summary['pop']}%")

    return "，".join(parts)
