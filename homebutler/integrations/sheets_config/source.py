"""Reading the spreadsheet: whether it changed, and the rows of some tabs. Read-only scopes."""
from __future__ import annotations

import json
from typing import Protocol

import httpx

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly",
          "https://www.googleapis.com/auth/drive.metadata.readonly"]


class SheetsSource(Protocol):
    def modified_time(self) -> str | None: ...
    def read(self, tabs: list[str]) -> dict[str, list[dict]]: ...


def rows_from_values(values: list[list]) -> list[dict]:
    """Rows as dicts keyed by the header row; blank rows dropped, cells trimmed, short rows padded."""
    if not values:
        return []
    headers = [str(h).strip() for h in values[0]]
    out = []
    for raw in values[1:]:
        cells = [str(c).strip() for c in raw] + [""] * (len(headers) - len(raw))
        row = {h: cells[i] for i, h in enumerate(headers) if h}
        if any(row.values()):
            out.append(row)
    return out


class _HttpxRequest:
    """google-auth's transport interface over httpx, so no extra HTTP library is needed."""
    def __init__(self, client: httpx.Client):
        self.client = client

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **_):
        from google.auth import exceptions
        try:
            reply = self.client.request(method, url, content=body, headers=headers, timeout=timeout or 30)
        except httpx.HTTPError as error:
            raise exceptions.TransportError(str(error)) from error

        class Response:
            status = reply.status_code
            headers = dict(reply.headers)
            data = reply.content
        return Response()


class GoogleSheetsSource:
    def __init__(self, spreadsheet_id: str, credentials_json: str, client: httpx.Client | None = None):
        from google.oauth2.service_account import Credentials
        self.spreadsheet_id = spreadsheet_id
        self.client = client or httpx.Client(timeout=30)
        self.credentials = Credentials.from_service_account_info(json.loads(credentials_json), scopes=SCOPES)

    def _headers(self):
        if not self.credentials.valid:
            self.credentials.refresh(_HttpxRequest(self.client))
        return {"Authorization": "Bearer " + self.credentials.token}

    def modified_time(self) -> str | None:
        reply = self.client.get(f"https://www.googleapis.com/drive/v3/files/{self.spreadsheet_id}",
                                params={"fields": "modifiedTime", "supportsAllDrives": "true"}, headers=self._headers())
        reply.raise_for_status()
        return reply.json().get("modifiedTime")

    def read(self, tabs: list[str]) -> dict[str, list[dict]]:
        reply = self.client.get(f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values:batchGet",
                                params=[("ranges", f"'{tab}'") for tab in tabs] + [("valueRenderOption", "FORMATTED_VALUE")],
                                headers=self._headers())
        reply.raise_for_status()
        found = {}
        for item in reply.json().get("valueRanges", []):
            tab = item.get("range", "").split("!")[0].strip("'")
            found[tab] = rows_from_values(item.get("values", []))
        missing = [tab for tab in tabs if tab not in found]
        if missing:
            raise LookupError("Tabs not found: " + ", ".join(missing))
        return found
