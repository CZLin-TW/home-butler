"""
Notion API 封裝模組（唯讀）
- 查詢 Database
- 支援 Sheet 定義的篩選條件
- 格式化行事曆事件給 Claude
"""

import httpx
import os
from datetime import datetime
import pytz

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_VERSION = "2022-06-28"
BASE_URL = "https://api.notion.com/v1"
TZ = pytz.timezone("Asia/Taipei")


def _headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _extract_property_value(prop):
    """從 Notion property 取出可讀值"""
    prop_type = prop.get("type", "")

    if prop_type == "title":
        titles = prop.get("title", [])
        return titles[0]["text"]["content"] if titles else ""

    elif prop_type == "date":
        date_obj = prop.get("date")
        if not date_obj:
            return None
        return {
            "start": date_obj.get("start", ""),
            "end": date_obj.get("end"),
        }

    elif prop_type == "people":
        people = prop.get("people", [])
        return ", ".join(p.get("name", "") for p in people)

    elif prop_type == "status":
        status = prop.get("status")
        return status.get("name", "") if status else ""

    elif prop_type == "select":
        select = prop.get("select")
        return select.get("name", "") if select else ""

    elif prop_type == "rich_text":
        texts = prop.get("rich_text", [])
        return texts[0]["text"]["content"] if texts else ""

    else:
        return ""


def _parse_page(page):
    """將 Notion page 轉為簡單 dict"""
    properties = page.get("properties", {})
    result = {}
    for key, prop in properties.items():
        result[key] = _extract_property_value(prop)
    return result


def _parse_filters(filters_str):
    """解析 "Status:Incoming,person:CZ" 為 [(key, value)] list"""
    filters = []
    for part in filters_str.split(","):
        part = part.strip()
        if ":" in part:
            key, value = part.split(":", 1)
            filters.append((key.strip(), value.strip()))
    return filters


def _apply_filters(items, filters):
    """在 Python 端套用篩選條件，支援 !排除"""
    result = []
    for item in items:
        match = True
        for key, value in filters:
            item_value = item.get(key, "")
            if item_value is None:
                item_value = ""
            if value.startswith("!"):
                exclude_val = value[1:]
                if isinstance(item_value, str) and exclude_val.lower() in item_value.lower():
                    match = False
                    break
            else:
                if isinstance(item_value, str):
                    if value.lower() not in item_value.lower():
                        match = False
                        break
        if match:
            result.append(item)
    return result


def get_upcoming_events(database_id, filters_str=""):
    """
    取得 Notion 行事曆的未來事件。
    filters_str: Sheet 定義的篩選條件，如 "Status:Incoming,person:CZ"
    """
    if not NOTION_TOKEN or not database_id:
        return []

    try:
        all_pages = []
        has_more = True
        start_cursor = None

        while has_more:
            body = {"page_size": 100}
            if start_cursor:
                body["start_cursor"] = start_cursor

            resp = httpx.post(
                f"{BASE_URL}/databases/{database_id}/query",
                headers=_headers(),
                json=body,
                timeout=15,
            )
            data = resp.json()

            if "results" not in data:
                print(f"[NOTION] API error: {data.get('message', 'unknown')}")
                return []

            all_pages.extend(data["results"])
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")

        items = [_parse_page(page) for page in all_pages]

        if filters_str:
            filters = _parse_filters(filters_str)
            items = _apply_filters(items, filters)

        today = datetime.now(TZ).date()
        upcoming = []
        for item in items:
            date_val = item.get("Date")
            if not date_val or not isinstance(date_val, dict):
                continue

            start_str = date_val.get("start", "")
            if not start_str:
                continue

            try:
                if "T" in start_str:
                    start_date = datetime.fromisoformat(start_str).date()
                else:
                    start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            end_str = date_val.get("end")
            end_date = None
            if end_str:
                try:
                    if "T" in end_str:
                        end_date = datetime.fromisoformat(end_str).date()
                    else:
                        end_date = datetime.strptime(end_str, "%Y-%m-%d").date()
                except ValueError:
                    pass

            effective_end = end_date or start_date
            if effective_end >= today:
                upcoming.append(item)

        def sort_key(item):
            d = item.get("Date", {})
            return d.get("start", "") if isinstance(d, dict) else ""
        upcoming.sort(key=sort_key)

        return upcoming

    except Exception as e:
        print(f"[NOTION] Error: {e}")
        return []


def format_events_for_claude(events):
    """將事件格式化為文字，給 Claude 語意回覆用"""
    if not events:
        return ""

    lines = []
    for item in events:
        name = item.get("Event", "")
        date_val = item.get("Date", {})
        category = item.get("類型", "")

        if not isinstance(date_val, dict):
            continue

        start_str = date_val.get("start", "")
        end_str = date_val.get("end")

        if "T" in start_str:
            try:
                dt = datetime.fromisoformat(start_str)
                date_part = dt.strftime("%Y-%m-%d")
                time_part = dt.strftime("%H:%M")
                if end_str and "T" in end_str:
                    end_dt = datetime.fromisoformat(end_str)
                    if end_dt.date() == dt.date():
                        date_display = f"{date_part} {time_part}~{end_dt.strftime('%H:%M')}"
                    else:
                        date_display = f"{date_part} {time_part} → {end_dt.strftime('%Y-%m-%d %H:%M')}"
                else:
                    date_display = f"{date_part} {time_part}"
            except Exception:
                date_display = start_str
        else:
            if end_str:
                date_display = f"{start_str} → {end_str}"
            else:
                date_display = start_str

        cat_label = f"[{category}]" if category else ""
        lines.append(f"{name}（{date_display}）{cat_label}")

    return "\n".join(lines)