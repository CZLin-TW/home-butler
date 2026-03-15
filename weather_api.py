"""
中央氣象署開放資料 API 封裝模組
- 鄉鎮天氣預報（2天逐3小時）
- 支援查詢今日/明日天氣
"""

import httpx
import os
from datetime import datetime, timedelta
import pytz

CWA_API_KEY = os.environ.get("CWA_API_KEY", "")
BASE_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
TZ = pytz.timezone("Asia/Taipei")

# 新竹縣 2天逐3小時鄉鎮預報
DEFAULT_DATA_ID = "F-D0047-009"
DEFAULT_LOCATION = "竹北市"


def _fetch_forecast(data_id=DEFAULT_DATA_ID, location=DEFAULT_LOCATION):
    """從氣象署 API 抓取鄉鎮預報原始資料"""
    try:
        params = {
            "Authorization": CWA_API_KEY,
            "locationName": location,
            "elementName": "Wx,MinT,MaxT,PoP12h,T,WeatherDescription",
        }
        resp = httpx.get(
            f"{BASE_URL}/{data_id}",
            params=params,
            timeout=15,
        )
        data = resp.json()

        if data.get("success") != "true":
            return {"error": data.get("records", {}).get("msg", "API 回傳失敗")}

        locations = data.get("records", {}).get("locations", [{}])[0].get("location", [])
        if not locations:
            return {"error": f"找不到 {location} 的預報資料"}

        return {"data": locations[0]}

    except Exception as e:
        return {"error": str(e)}


def _parse_element(weather_elements, element_name):
    """從 weatherElement 陣列中取出指定元素的時間序列"""
    for elem in weather_elements:
        if elem.get("elementName") == element_name:
            return elem.get("time", [])
    return []


def _find_period_value(time_series, target_date, prefer_daytime=True):
    """
    從時間序列中找出目標日期的值
    prefer_daytime: True 優先取白天時段，False 取全天
    """
    results = []
    for period in time_series:
        start = period.get("startTime", "")
        if not start:
            continue
        try:
            start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

        if start_dt.date() == target_date:
            values = period.get("elementValue", [])
            value = values[0].get("value", "") if values else ""
            results.append({
                "start": start,
                "end": period.get("endTime", ""),
                "value": value,
                "hour": start_dt.hour,
            })

    if not results:
        return None

    if prefer_daytime:
        # 優先取 06:00 或 12:00 開始的時段
        for r in results:
            if r["hour"] in (6, 12):
                return r["value"]

    return results[0]["value"]


def get_weather_summary(target="today", data_id=DEFAULT_DATA_ID, location=DEFAULT_LOCATION):
    """
    取得天氣摘要
    target: "today" 或 "tomorrow"
    回傳 dict: {location, date, wx, min_t, max_t, pop, description} 或 {error}
    """
    result = _fetch_forecast(data_id, location)
    if "error" in result:
        return result

    loc_data = result["data"]
    weather_elements = loc_data.get("weatherElement", [])

    now = datetime.now(TZ)
    if target == "tomorrow":
        target_date = (now + timedelta(days=1)).date()
    else:
        target_date = now.date()

    # 天氣描述（WeatherDescription 包含最完整的資訊）
    desc_series = _parse_element(weather_elements, "WeatherDescription")
    description = _find_period_value(desc_series, target_date)

    # 天氣現象
    wx_series = _parse_element(weather_elements, "Wx")
    wx = _find_period_value(wx_series, target_date)

    # 溫度
    min_t_series = _parse_element(weather_elements, "MinT")
    max_t_series = _parse_element(weather_elements, "MaxT")
    min_t = _find_period_value(min_t_series, target_date, prefer_daytime=False)
    max_t = _find_period_value(max_t_series, target_date, prefer_daytime=False)

    # 取所有該日的 MinT/MaxT 來算真正的最低最高
    all_min = []
    all_max = []
    for period in min_t_series:
        start = period.get("startTime", "")
        try:
            start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
            if start_dt.date() == target_date:
                val = period.get("elementValue", [{}])[0].get("value", "")
                if val:
                    all_min.append(int(val))
        except (ValueError, IndexError):
            pass
    for period in max_t_series:
        start = period.get("startTime", "")
        try:
            start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
            if start_dt.date() == target_date:
                val = period.get("elementValue", [{}])[0].get("value", "")
                if val:
                    all_max.append(int(val))
        except (ValueError, IndexError):
            pass

    actual_min = min(all_min) if all_min else min_t
    actual_max = max(all_max) if all_max else max_t

    # 降雨機率
    pop_series = _parse_element(weather_elements, "PoP12h")
    all_pop = []
    for period in pop_series:
        start = period.get("startTime", "")
        try:
            start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
            if start_dt.date() == target_date:
                val = period.get("elementValue", [{}])[0].get("value", "")
                if val:
                    all_pop.append(int(val))
        except (ValueError, IndexError):
            pass
    max_pop = max(all_pop) if all_pop else None

    date_label = "今天" if target == "today" else "明天"
    date_str = target_date.strftime("%m/%d")

    return {
        "location": location,
        "date_label": date_label,
        "date": date_str,
        "wx": wx or "無資料",
        "min_t": actual_min,
        "max_t": actual_max,
        "pop": max_pop,
        "description": description,
    }


def format_weather(summary):
    """將天氣摘要格式化為人類可讀文字"""
    if "error" in summary:
        return f"❌ 無法取得天氣資料：{summary['error']}"

    location = summary["location"]
    date_label = summary["date_label"]
    date_str = summary["date"]
    wx = summary["wx"]
    min_t = summary["min_t"]
    max_t = summary["max_t"]
    pop = summary["pop"]

    lines = [f"🌤️ {location}{date_label}（{date_str}）天氣"]
    lines.append(f"天氣：{wx}")

    if min_t is not None and max_t is not None:
        lines.append(f"溫度：{min_t}~{max_t}°C")
    elif min_t is not None:
        lines.append(f"最低溫：{min_t}°C")

    if pop is not None:
        lines.append(f"降雨機率：{pop}%")
        if pop >= 70:
            lines.append("☔ 記得帶傘！")
        elif pop >= 40:
            lines.append("🌂 建議帶把傘以防萬一")

    return "\n".join(lines)


def get_today_weather_text(data_id=DEFAULT_DATA_ID, location=DEFAULT_LOCATION):
    """取得今日天氣的格式化文字（給推播用）"""
    summary = get_weather_summary("today", data_id, location)
    return format_weather(summary)


def get_tomorrow_weather_text(data_id=DEFAULT_DATA_ID, location=DEFAULT_LOCATION):
    """取得明日天氣的格式化文字（給推播用）"""
    summary = get_weather_summary("tomorrow", data_id, location)
    return format_weather(summary)


def get_weather_data_for_notify(target="today", data_id=DEFAULT_DATA_ID, location=DEFAULT_LOCATION):
    """取得天氣摘要字串（給 Claude 組推播訊息用）"""
    summary = get_weather_summary(target, data_id, location)
    if "error" in summary:
        return None

    parts = [f"{summary['location']}{summary['date_label']}天氣：{summary['wx']}"]
    if summary["min_t"] is not None and summary["max_t"] is not None:
        parts.append(f"溫度 {summary['min_t']}~{summary['max_t']}°C")
    if summary["pop"] is not None:
        parts.append(f"降雨機率 {summary['pop']}%")

    return "，".join(parts)