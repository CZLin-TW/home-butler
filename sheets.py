import gspread
from google.oauth2.service_account import Credentials
import json
import time
import unicodedata
from config import SPREADSHEET_ID, GOOGLE_CREDENTIALS


def _norm(s):
    return unicodedata.normalize("NFC", str(s or "")).strip()

_sheets_cache_ttl = 60
_spreadsheet = None
_spreadsheet_time = 0


def _get_client():
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID)


def _get_spreadsheet():
    global _spreadsheet, _spreadsheet_time
    now = time.time()
    if _spreadsheet is None or (now - _spreadsheet_time) > _sheets_cache_ttl:
        _spreadsheet = _get_client()
        _spreadsheet_time = now
    return _spreadsheet


def get_sheet(name):
    return _get_spreadsheet().worksheet(name)


def _parse_sheet_values(values):
    if not values or len(values) < 2:
        return []
    headers = values[0]
    records = []
    for row in values[1:]:
        padded = list(row) + [''] * max(0, len(headers) - len(row))
        record = {}
        for i, h in enumerate(headers):
            if not h:
                continue
            v = padded[i] if i < len(padded) else ''
            if isinstance(v, str) and v.strip():
                try:
                    v = int(v)
                except ValueError:
                    try:
                        v = float(v)
                    except ValueError:
                        pass
            record[h] = v
        records.append(record)
    return records


class RequestContext:
    BATCH_SHEETS = ["家庭成員", "食品庫存", "待辦事項", "智能居家", "對話暫存", "排程指令"]

    def __init__(self):
        self._records = {}
        self._worksheets = {}
        self._loaded = False

    def load(self):
        ss = _get_spreadsheet()
        ranges = [f"'{name}'" for name in self.BATCH_SHEETS]
        try:
            result = ss.values_batch_get(
                ranges,
                params={'valueRenderOption': 'FORMATTED_VALUE'}
            )
            for vr in result.get('valueRanges', []):
                range_str = vr.get('range', '')
                sheet_name = range_str.split('!')[0].strip("'")
                self._records[sheet_name] = _parse_sheet_values(vr.get('values', []))
            print(f"[BATCH READ] 成功讀取 {len(self._records)} 個分頁")
        except Exception as e:
            print(f"[BATCH READ ERROR] {e}，改用逐一讀取")
            ss = _get_spreadsheet()
            for name in self.BATCH_SHEETS:
                try:
                    ws = ss.worksheet(name)
                    self._records[name] = ws.get_all_records()
                except Exception as e2:
                    print(f"[FALLBACK READ ERROR] {name}: {e2}")
                    self._records[name] = []
        self._loaded = True

    def get(self, sheet_name):
        if not self._loaded:
            self.load()
        return self._records.get(sheet_name, [])

    def set(self, sheet_name, records):
        """手動更新快取（例如 sync 後重新讀取）"""
        self._records[sheet_name] = records

    def get_worksheet(self, name):
        if name not in self._worksheets:
            ss = _get_spreadsheet()
            self._worksheets[name] = ss.worksheet(name)
        return self._worksheets[name]


def get_device_id_by_name(device_name, ctx):
    target = _norm(device_name)
    for r in ctx.get("智能居家"):
        if r.get("狀態") == "啟用" and _norm(r.get("名稱")) == target:
            return r.get("Device ID", "")
    return ""


def get_device_auth_by_name(device_name, ctx):
    target = _norm(device_name)
    for r in ctx.get("智能居家"):
        if r.get("狀態") == "啟用" and _norm(r.get("名稱")) == target:
            return r.get("Auth", ""), r.get("Device ID", "")
    return "", ""


def get_all_devices_by_type(device_type, ctx):
    return [r for r in ctx.get("智能居家") if r.get("狀態") == "啟用" and r.get("類型") == device_type]


def build_row(headers, data):
    """Build a positional row list matching header order from a dict.

    Unknown keys in data are silently ignored (only headers present in the
    sheet matter).  Missing keys default to empty string.
    """
    return [data.get(h, "") for h in headers]
