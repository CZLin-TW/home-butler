"""Notion, read only: a member's upcoming tasks from their database.

Returns None when the query did not succeed (no token, an API error, a timeout) and a list when it did,
even an empty one; the two must never be confused, because "Notion has nothing" archives local rows and
"could not ask Notion" must change nothing.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import httpx

from homebutler.domain.todos.notion import Event

log = logging.getLogger("homebutler.notion")
VERSION = "2022-06-28"
BASE = "https://api.notion.com/v1"


def _value(prop: dict):
    kind = prop.get("type", "")
    if kind == "title":
        parts = prop.get("title", [])
        return parts[0]["text"]["content"] if parts else ""
    if kind == "rich_text":
        parts = prop.get("rich_text", [])
        return parts[0]["text"]["content"] if parts else ""
    if kind == "date":
        found = prop.get("date")
        return {"start": found.get("start", ""), "end": found.get("end")} if found else None
    if kind == "people":
        return ", ".join(p.get("name", "") for p in prop.get("people", []))
    if kind in ("status", "select"):
        found = prop.get(kind)
        return found.get("name", "") if found else ""
    if kind == "formula":
        formula = prop.get("formula", {})
        if formula.get("type") == "date":
            found = formula.get("date")
            return {"start": found.get("start", ""), "end": found.get("end")} if found else ""
        return formula.get(formula.get("type", ""), "")
    if kind == "created_by":
        return prop.get("created_by", {}).get("name", "")
    return ""


def parse_filters(text: str) -> list[tuple[str, str]]:
    """"Status:Incoming,person:CZ,類型:!私事" → [(key, value)]; a value starting with ! excludes."""
    out = []
    for part in str(text or "").split(","):
        if ":" in part:
            key, value = part.split(":", 1)
            out.append((key.strip(), value.strip()))
    return out


def matches(item: dict, filters: list[tuple[str, str]]) -> bool:
    for key, value in filters:
        found = item.get(key) or ""
        if not isinstance(found, str):
            continue
        if value.startswith("!"):
            if value[1:].lower() in found.lower():
                return False
        elif value.lower() not in found.lower():
            return False
    return True


def _day(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date() if "T" in text else date.fromisoformat(text)
    except ValueError:
        return None


def to_event(item: dict) -> Event | None:
    start = (item.get("Date") or {}).get("start") if isinstance(item.get("Date"), dict) else None
    if not item.get("Event") or not start or not item.get("_page_id"):
        return None
    if "T" in start:
        try:
            moment = datetime.fromisoformat(start)
        except ValueError:
            return None
        return Event(item["_page_id"], item["Event"], moment.date(), moment.time().replace(second=0, microsecond=0))
    day = _day(start)
    return Event(item["_page_id"], item["Event"], day, None) if day else None


class Notion:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self.token = token
        self.client = client or httpx.Client(timeout=15)

    def upcoming(self, database_id: str, filters: str, today: date) -> list[Event] | None:
        if not self.token or not database_id:
            return None
        headers = {"Authorization": "Bearer " + self.token, "Notion-Version": VERSION}
        pages, cursor = [], None
        try:
            while True:
                body = {"page_size": 100, "filter": {"property": "Date", "date": {"on_or_after": today.isoformat()}}}
                if cursor:
                    body["start_cursor"] = cursor
                reply = self.client.post(f"{BASE}/databases/{database_id}/query", headers=headers, json=body)
                data = reply.json()
                if reply.status_code != 200 or "results" not in data:
                    log.warning("Notion query failed (%s)", reply.status_code)
                    return None
                pages += data["results"]
                if not data.get("has_more"):
                    break
                cursor = data.get("next_cursor")
        except (httpx.HTTPError, ValueError):
            log.warning("Notion query failed (network)")
            return None
        wanted = parse_filters(filters)
        events = []
        for page in pages:
            item = {key: _value(prop) for key, prop in page.get("properties", {}).items()}
            item["_page_id"] = str(page.get("id") or "")
            if not matches(item, wanted):
                continue
            span = item.get("Date") if isinstance(item.get("Date"), dict) else {}
            last = _day(span.get("end")) or _day(span.get("start"))
            if last is None or last < today:
                continue
            event = to_event(item)
            if event is not None:
                events.append(event)
        return sorted(events, key=lambda e: (e.due_date, e.due_time or datetime.min.time()))
