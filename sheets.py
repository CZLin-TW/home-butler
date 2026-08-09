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
  Device ID           str   SwitchBot deviceId / Panasonic gwid / LG ThinQ deviceId
  Auth                str   Panasonic 設備 auth token（Panasonic 除濕機才有；LG 不需要）
  品牌                str   除濕機用："Panasonic" | "LG"。空值預設 Panasonic（向下相容）
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
  最後開機時間        str   "YYYY-MM-DD HH:MM"（開機時錨定、關機清空；防黴算運轉時長用）
  防黴運轉門檻分鐘    int   per-device 覆寫防黴運轉門檻（空=預設 30；0=每次關都送風）
  防黴送風分鐘        int   per-device 覆寫防黴送風時長（空=預設 5）

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
  燈光提醒            str   "TRUE" | "FALSE"
  燈光區域ID          str   Hue grouped_light id（照明提醒用）
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
  來源                str   "使用者" | "自動" | "防黴"
                            ← "自動"=AC 自動關機 timer；"防黴"=關冷氣前送風後的收尾關（params 帶 antimold_final）
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
from gspread.exceptions import APIError, WorksheetNotFound
from gspread.utils import rowcol_to_a1
from google.oauth2.service_account import Credentials
import json
import random
import time
import unicodedata
import requests.exceptions as _req_exc
from config import SPREADSHEET_ID, GOOGLE_CREDENTIALS


def _norm(s):
    return unicodedata.normalize("NFC", str(s or "")).strip()

_sheets_cache_ttl = 60
_spreadsheet = None
_spreadsheet_time = 0


# ── 暫時性錯誤自動重試 ──
# Google Sheets 會偶發回 429 / 5xx（配額瞬時超限、Google 端短暫故障，例如
# 「503 The service is currently unavailable」）。這類錯誤本質上是**下一秒就好**的
# 抖動，但沒有重試的話會直接往上冒：Dashboard 收到 500 + 整頁 ASGI traceback、
# LINE 回「發生未知錯誤」、polling tick 整輪跳過——使用者體感就是「HomeButler 掛了」，
# 其實 process 好好的、Google 幾秒後就恢復。
#
# 重試裝在**兩層**：
#  1. gspread HTTP client 的 GET（見 _install_gspread_get_retry）——一次覆蓋全 repo
#     所有讀取（get_all_records / row_values / values_get / worksheet metadata…），
#     散在 15+ 個模組的呼叫點都不用改。GET 天生冪等，重試零風險。
#  2. 這個檔案裡少數**冪等寫入**（batch_update：寫死絕對 range + 絕對值）明確包
#     _with_retry。
# `append_row` / `add_worksheet` / update_cell 這類非冪等寫入**刻意不重試**——503 有
# 可能是「其實寫進去了只是回應掉了」，重試會多一筆重複資料（重複待辦、重複排程指令），
# 比一次失敗更難收拾。
#
# 為什麼不用 gspread 內建的 BackOffHTTPClient：它 (a) 不分方法一律重試（append 會重複）、
# (b) 退避從 2s 翻倍到 128s，一個 LINE webhook 可能被卡好幾分鐘。我們要的是「短、有界、
# 只吃抖動」。
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_RETRY_ATTEMPTS = 3          # 總嘗試次數（最多 sleep 2 次）
_RETRY_BASE_SLEEP = 0.8      # 0.8s → 1.6s（＋jitter），最壞多花約 2.5s


def _is_transient(e):
    """這個例外值不值得重試？（Google 端抖動 / 連線層問題才算）"""
    if isinstance(e, APIError):
        code = getattr(e, "code", None)
        if code is None:
            code = getattr(getattr(e, "response", None), "status_code", None)
        return code in _RETRY_STATUSES
    return isinstance(e, (_req_exc.ConnectionError, _req_exc.Timeout, _req_exc.ChunkedEncodingError))


def is_transient_error(e):
    """對外版：給上層（HTTP 例外處理、LINE 回覆文案）判斷「這是 Google 抖動，不是我們的 bug」。"""
    return _is_transient(e)


def _with_retry(op, what="sheets"):
    """跑 op()，遇到暫時性 Google 錯誤時短退避重試；非暫時性錯誤原樣拋出。"""
    global _spreadsheet
    last = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            return op()
        except Exception as e:
            if not _is_transient(e):
                raise
            last = e
            if attempt < _RETRY_ATTEMPTS:
                delay = _RETRY_BASE_SLEEP * (2 ** (attempt - 1)) + random.uniform(0, 0.3)
                print(f"[SHEETS RETRY] {what} 第 {attempt} 次失敗（{e}），{delay:.1f}s 後重試")
                time.sleep(delay)
    # 重試用盡：丟掉快取的 spreadsheet，下一次請求重新 authorize + open，
    # 避免壞掉的 session／過期 token 被 60s 快取黏住。
    _spreadsheet = None
    print(f"[SHEETS ERROR] {what} 重試 {_RETRY_ATTEMPTS} 次仍失敗：{last}")
    raise last


def _install_gspread_get_retry():
    """把重試裝進 gspread HTTPClient.request，只對 GET 生效。

    gspread 所有 API 呼叫都收斂到這一個方法，所以在這裡包等於全 repo 的 Sheet 讀取
    都有重試——不必去 15+ 個模組逐一改 get_all_records() 呼叫點。非 GET 原樣放行，
    由呼叫端自己決定要不要重試（見上面「非冪等寫入不重試」）。
    """
    original = gspread.http_client.HTTPClient.request
    if getattr(original, "_home_butler_retry", False):
        return

    def request_with_retry(self, method, endpoint, *args, **kwargs):
        call = lambda: original(self, method, endpoint, *args, **kwargs)
        if str(method).lower() != "get":
            return call()
        return _with_retry(call, f"GET {str(endpoint).rsplit('/', 1)[-1]}")

    request_with_retry._home_butler_retry = True
    gspread.http_client.HTTPClient.request = request_with_retry


_install_gspread_get_retry()


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
        # open_by_key 會實際打一次 metadata API（GET）——正是 Google 503 最常炸的
        # 地方，重試由 _install_gspread_get_retry 那層吸收。
        _spreadsheet = _get_client()
        _spreadsheet_time = now
    return _spreadsheet


def get_sheet(name):
    return _get_spreadsheet().worksheet(name)


def get_sheet_records(name):
    """Read one worksheet as records without loading the full RequestContext."""
    ss = _get_spreadsheet()
    try:
        result = ss.values_get(
            f"'{name}'",
            params={'valueRenderOption': 'FORMATTED_VALUE'},
        )
        return _parse_sheet_values(result.get('values', []))
    except Exception as e:
        print(f"[SHEET READ ERROR] {name}: {e}，改用 get_all_records")
        return ss.worksheet(name).get_all_records()


def get_or_create_sheet(name, headers, rows=100):
    """Return a worksheet, creating it with the given header row if missing."""
    ss = _get_spreadsheet()
    try:
        sheet = ss.worksheet(name)
    except WorksheetNotFound:
        # add_worksheet 刻意不重試（非冪等，重試可能建出重複分頁）。
        sheet = ss.add_worksheet(title=name, rows=rows, cols=max(len(headers), 1))
        end_cell = rowcol_to_a1(1, len(headers))
        sheet.batch_update([{
            "range": f"A1:{end_cell}",
            "values": [headers],
        }], raw=False)
        return sheet

    ensure_columns(sheet, headers)
    return sheet


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
            last_err = None
            ok = 0
            for name in self.BATCH_SHEETS:
                try:
                    # 這條 fallback 刻意**不再重試**：batch 已經退避重試過 3 次了，
                    # 若是 Google 端整段抖動，這裡再對 6 個分頁各重試 3 次只會讓一個
                    # 請求卡到 15s+（LINE webhook 會超時）並加重 Google 的負擔。
                    # 它要救的是「batch 端點特有的失敗」，一次打完見真章。
                    self._records[name] = ss.worksheet(name).get_all_records()
                    ok += 1
                except Exception as e2:
                    print(f"[FALLBACK READ ERROR] {name}: {e2}")
                    self._records[name] = []
                    last_err = e2
            if ok == 0 and last_err is not None:
                # 一個分頁都讀不到＝Sheets 整個不通（Google 掛掉/認證失效），不是
                # 「資料剛好都空的」。這時**要大聲失敗**：靜靜回一堆空 list 會讓 bot
                # 回「沒有待辦事項」、Dashboard 顯示 0 台設備——比一次錯誤更誤導人。
                raise last_err
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


def append_record(sheet, data):
    """Append a dict as one row using the sheet header order."""
    headers = sheet.row_values(1)
    # append_row 刻意不重試：非冪等，Google 503 可能是「寫進去了但回應掉了」，
    # 重試會多出一筆重複資料（例如同一則待辦、同一筆排程指令）。
    sheet.append_row(build_row(headers, data), value_input_option="USER_ENTERED")


def ensure_columns(sheet, columns):
    """Ensure header columns exist, appending missing ones to row 1.

    This keeps schema migrations small and idempotent for optional features.
    """
    headers = sheet.row_values(1)
    missing = [c for c in columns if c not in headers]
    if not missing:
        return headers

    start_col = len(headers) + 1
    target_col_count = len(headers) + len(missing)
    if getattr(sheet, "col_count", target_col_count) < target_col_count:
        sheet.add_cols(target_col_count - sheet.col_count)
    requests = []
    for offset, column in enumerate(missing):
        requests.append({
            "range": rowcol_to_a1(1, start_col + offset),
            "values": [[column]],
        })
    # batch_update 是冪等的（絕對 range + 絕對值），重試最多重寫同樣的值，安全。
    _with_retry(lambda: sheet.batch_update(requests, raw=False), "batch_update(headers)")
    return headers + missing


def update_row_fields(sheet, row_number, updates):
    """Batch update selected fields in a row by header name.

    This keeps multi-field edits to one Google Sheets API call while preserving
    the existing header-name based access pattern used by handlers.
    """
    if not updates:
        return 0

    headers = sheet.row_values(1)
    col_by_header = {h: idx + 1 for idx, h in enumerate(headers)}
    requests = []
    for field, value in updates.items():
        if field not in col_by_header:
            raise KeyError(f"Sheet column not found: {field}")
        requests.append({
            "range": rowcol_to_a1(row_number, col_by_header[field]),
            "values": [[value]],
        })

    if requests:
        # 同 ensure_columns：寫死 range + 寫死值，重試安全。
        _with_retry(lambda: sheet.batch_update(requests, raw=False), "batch_update(fields)")
    return len(requests)


# ── 系統狀態 KV（跨重啟的小狀態，例如每日推播 marker） ──
# 刻意用一個獨立的 2 欄小分頁（鍵/值），而非塞進現有資料表，避免污染業務資料的 schema。
SYSTEM_STATE_SHEET = "系統狀態"
_SYSTEM_STATE_HEADERS = ["鍵", "值"]


def state_get(key, default=""):
    """讀系統狀態 KV 分頁裡某個鍵的值（分頁不存在會自動建立）。找不到回 default。"""
    sheet = get_or_create_sheet(SYSTEM_STATE_SHEET, _SYSTEM_STATE_HEADERS)
    for r in sheet.get_all_records():
        if _norm(r.get("鍵")) == _norm(key):
            return str(r.get("值", "") or "")
    return default


def state_set(key, value):
    """upsert 系統狀態 KV 的一個鍵值：存在就更新該列、否則 append 一筆。"""
    sheet = get_or_create_sheet(SYSTEM_STATE_SHEET, _SYSTEM_STATE_HEADERS)
    records = sheet.get_all_records()
    for i, r in enumerate(records):
        if _norm(r.get("鍵")) == _norm(key):
            update_row_fields(sheet, i + 2, {"值": str(value)})
            return
    append_record(sheet, {"鍵": key, "值": str(value)})
