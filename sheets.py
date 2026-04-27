"""
Google Sheets 存取層。

─────────────────────────────────────────────────
Sheet 欄位對照表（資料來源、欄位名稱、值的範例）
─────────────────────────────────────────────────

【家庭成員】（BATCH_SHEETS）
  名稱                str   "CZ"
  稱謂                str   "爸爸"
  Line User ID        str   "U1234abcd..."
  狀態                str   "啟用" | "停用"
  管家風格            str   自訂 prompt 片段，可空
  Notion Database ID  str   可空（不啟用 Notion 同步就空白）
  Notion 篩選         str   "Status:Incoming,person:CZ"，逗號分隔，可加 ! 排除
  Notion 權限         str   "唯讀" | "讀寫"

【智能居家】（BATCH_SHEETS）
  名稱                str   "客廳空調"
  類型                str   "空調" | "IR" | "感應器" | "除濕機"
  位置                str   "客廳"
  Device ID           str   SwitchBot deviceId 或 Panasonic gwid
  Auth                str   Panasonic 設備 auth token（除濕機才有）
  按鈕                str   IR 設備自訂按鈕，逗號分隔
  控制類型            str   "command" | "customize"
  狀態                str   "啟用" | "停用"
  溫度補償            float 感測器溫度 offset，會加到讀值上（負值=讀數偏高）
  濕度補償            float 感測器濕度 offset，clamp 到 [0,100]
  自動關機小時數      int   AC 自動關機 timer，0 = 停用
  最後電源            str   "on" | "off" | ""（AC 用，由 handlers/device.py 維護）
  最後溫度            int   16~30
  最後模式            str   "自動" | "冷氣" | "除濕" | "送風" | "暖氣"
  最後風速            str   "自動" | "低" | "中" | "高"
  最後更新時間        str   "YYYY-MM-DD HH:MM"

【食品庫存】（BATCH_SHEETS）
  品名 / 數量 / 單位 / 過期日(YYYY-MM-DD) / 新增日 / 新增者
  狀態                str   "有效"（封存表用「已消耗」）
  封存表：食品封存（同欄位）

【待辦事項】（BATCH_SHEETS）
  事項 / 日期(YYYY-MM-DD) / 時間(HH:MM,可空) / 負責人
  狀態                str   "待辦" | "已完成"
  類型                str   "私人" | "公開"
  來源                str   "本地" | "Notion"（外部行事曆）
  屬性                str   "讀寫" | "唯讀"   ← 唯讀項目（外部行事曆）不可 modify
  封存表：待辦封存（同欄位）

【對話暫存】（BATCH_SHEETS）
  Line User ID / 角色("user"|"assistant") / 內容 / 時間(YYYY-MM-DD HH:MM:SS)
  封存表：對話封存（同欄位）；超過 6 則自動搬封存

【排程指令】（BATCH_SHEETS）
  設備名稱 / 動作("control_ac"|"control_ir"|"control_dehumidifier")
  參數                str   JSON 字串，例如 {"power":"off"} 或 {"temperature":27}
  觸發時間            str   "YYYY-MM-DD HH:MM"
  建立者 / 建立時間
  狀態                str   "待執行" | "已執行" | "已過期" | "已取消"
  來源                str   "使用者" | "自動"   ← "自動" 是 AC 自動關機 timer 產生
  封存表：排程封存（同欄位）

─────────────────────────────────────────────────
TODO: 後續可升級成 TypedDict / dataclass 取得 IDE 自動完成 + 拼字保護。
寫法範例：
  SmartHomeRow = TypedDict("SmartHomeRow", {
      "名稱": str,
      "類型": Literal["空調", "IR", "感應器", "除濕機"],
      ...
  }, total=False)  # total=False 因為 Sheet 欄位常缺值
然後把 RequestContext.get("智能居家") 的回傳型別標成 list[SmartHomeRow]。
中文 key 必須用 functional syntax（上面這種寫法）。
─────────────────────────────────────────────────
"""

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
