from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
import pytz
import os
import json
import traceback
import time
import threading
import anthropic
import httpx
import re
import switchbot_api
import panasonic_api
import weather_api

app = FastAPI()

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
panasonic_api.PANASONIC_ACCOUNT = os.environ.get("PANASONIC_ACCOUNT", "")
panasonic_api.PANASONIC_PASSWORD = os.environ.get("PANASONIC_PASSWORD", "")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
TZ = pytz.timezone('Asia/Taipei')

SYSTEM_PROMPT = """你是家庭專屬管家，管理食品庫存、待辦事項和智能居家設備。
語氣有禮簡潔，帶管家從容感，適度用 emoji（🥛📋🌡️❄️ 等）但不過度。查詢時主動補充貼心提醒。

家庭成員：{family_info}
現在傳訊息的人是「{current_user}」。add_todo 若未指定 person，預設填「{current_user}」。

目前庫存：{food_info}
目前待辦：{todo_info}
智能設備：{device_info}
IR 按鈕：{ir_device_info}
今天 {today}，現在 {now_time}。

永遠只回傳 JSON：{{"actions": [...], "reply": "回覆文字"}}

action 定義：
- add_food：name, quantity(預設1), unit(預設「個」), expiry(YYYY-MM-DD)
- delete_food：name
- modify_food：name, 只填要改的欄位(name_new/quantity/unit/expiry)。quantity 為更新後數量，自行計算
- query_food：無參數
- add_todo：item, date(YYYY-MM-DD), 選填 time(HH:MM), person(留空=自動填), type(「私人」或「公開」，預設私人)
- modify_todo：item, 只填要改的欄位(item_new/date/time/person/type)
- delete_todo：item
- query_todo：無參數
- control_ac：device_name, 選填 power(on/off), temperature(16-30), mode(cool/heat/dry/fan/auto), fan_speed(auto/low/medium/high)。只說溫度或模式時預設 power=on。唯一一台冷氣時可省略 device_name
- query_sensor：device_name。唯一感應器時可省略
- control_ir：device_name, button。開關用 button="開"/"關"，其他填實際按鈕名稱（須完全一致）。唯一設備時可省略 device_name
- control_dehumidifier：device_name, 選填 power(on/off), mode(連續除濕/自動除濕/防黴/送風/目標濕度/空氣清淨/AI舒適/省電/快速除濕/靜音除濕), humidity(40/45/50/55/60/65/70)。只說模式或濕度時預設 power=on。唯一除濕機時可省略 device_name
- query_dehumidifier：device_name。唯一除濕機時可省略
- query_devices：無參數
- query_weather：選填 date（YYYY-MM-DD，自行根據今天日期計算，如「這週末」算出週六日期，「後天」算出具體日期，不指定則查今天，最多未來 7 天）, 選填 location（完整地名如「雲林縣莿桐鄉」，不指定則查竹北市）
- unclear：message(反問內容)

規則：
- 可一次多個 action
- 有上下文先推斷，真的模糊才用 unclear 反問
- modify_todo 不要用 delete+add 替代

範例：
{{"actions": [{{"action": "add_food", "name": "牛奶", "quantity": 1, "unit": "瓶", "expiry": "2026-03-25"}}], "reply": "好的，牛奶已登記，過期日 3/25 🥛"}}
{{"actions": [{{"action": "query_food"}}], "reply": "目前庫存如下：\\n🥛 鮮奶 1瓶（3/23）\\n🍰 草莓生乳捲 1個（3/17）\\n\\n⚠️ 草莓生乳捲後天到期，建議盡快享用！"}}
{{"actions": [{{"action": "add_todo", "item": "看牙醫", "date": "2026-04-24", "time": "14:00"}}], "reply": "好的，4/24 下午 2 點看牙醫已記下 🦷"}}
{{"actions": [{{"action": "control_ac", "device_name": "客廳冷氣", "power": "on", "temperature": 26}}], "reply": "好的，冷氣已開啟，26 度 ❄️"}}
{{"actions": [{{"action": "query_weather", "date": "2026-03-22", "location": "臺北市信義區"}}], "reply": "為您查詢臺北信義區週六天氣。"}}
{{"actions": [{{"action": "unclear", "message": "請問是哪個品項？"}}], "reply": "請問是哪個品項？"}}
{{"actions": [], "reply": "了解，有需要再跟我說 😊"}}
"""

def now_taipei():
    return datetime.now(TZ)


# ══════════════════════════════════════════
# Google Sheets 連線 & 快取
# ══════════════════════════════════════════

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
    """取得快取的 spreadsheet 物件（60 秒 TTL）"""
    global _spreadsheet, _spreadsheet_time
    now = time.time()
    if _spreadsheet is None or (now - _spreadsheet_time) > _sheets_cache_ttl:
        _spreadsheet = _get_client()
        _spreadsheet_time = now
    return _spreadsheet

def get_sheet(name):
    """取得單一 worksheet（僅用於寫入操作和 notify 端點）"""
    return _get_spreadsheet().worksheet(name)


# ══════════════════════════════════════════
# RequestContext：批次讀取，一次 API 呼叫
# ══════════════════════════════════════════

def _parse_sheet_values(values):
    """將 Sheets API 回傳的 2D array 轉為 list of dict（模擬 get_all_records 行為）"""
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
            # 模擬 gspread 的自動型別轉換
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
    """
    單次請求的 Sheets 資料快取。
    
    用 values_batch_get 一次讀取所有分頁，取代原本 7~8 次個別 API 呼叫。
    寫入操作仍使用個別 worksheet，但 worksheet 物件也做快取。
    """
    
    BATCH_SHEETS = ["家庭成員", "食品庫存", "待辦事項", "智能居家", "對話暫存"]
    
    def __init__(self):
        self._records = {}
        self._worksheets = {}
        self._loaded = False
    
    def load(self):
        """一次 API 呼叫讀取所有分頁"""
        ss = _get_spreadsheet()
        ranges = [f"'{name}'" for name in self.BATCH_SHEETS]
        try:
            result = ss.values_batch_get(
                ranges,
                params={'valueRenderOption': 'FORMATTED_VALUE'}
            )
            for vr in result.get('valueRanges', []):
                range_str = vr.get('range', '')
                # "'家庭成員'!A1:Z1000" → "家庭成員"
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
        """取得分頁資料（從快取）"""
        if not self._loaded:
            self.load()
        return self._records.get(sheet_name, [])
    
    def get_worksheet(self, name):
        """取得 worksheet 物件（用於寫入，快取避免重複 metadata 查詢）"""
        if name not in self._worksheets:
            ss = _get_spreadsheet()
            self._worksheets[name] = ss.worksheet(name)
        return self._worksheets[name]


# ══════════════════════════════════════════
# 資料讀取函數（全部改用 ctx 快取）
# ══════════════════════════════════════════

def get_user_name(user_id, ctx):
    for row in ctx.get("家庭成員"):
        if row.get("Line User ID") == user_id and row.get("狀態") == "啟用":
            return row.get("名稱", user_id)
    return user_id

def get_family_members_info(ctx):
    members = []
    for row in ctx.get("家庭成員"):
        if row.get("狀態") == "啟用":
            members.append(f"{row.get('名稱')}（稱謂：{row.get('稱謂', '')}）")
    return "、".join(members)

def get_current_food(ctx):
    valid = [r for r in ctx.get("食品庫存") if r.get("狀態") == "有效"]
    if not valid:
        return "目前庫存是空的"
    lines = [f"{r['品名']} {r['數量']}{r['單位']}（過期日 {r['過期日']}）" for r in valid]
    return "、".join(lines)

def get_current_todo(ctx):
    valid = [r for r in ctx.get("待辦事項") if r.get("狀態") == "待辦"]
    if not valid:
        return "目前沒有待辦事項"
    lines = []
    for r in valid:
        time_part = f" {r['時間']}" if r.get("時間") else ""
        type_part = "（私人）" if r.get("類型") == "私人" else "（公開）"
        lines.append(f"{r['事項']}／{r['負責人']}／{r['日期']}{time_part}{type_part}")
    return "、".join(lines)

def get_device_info(ctx):
    valid = [r for r in ctx.get("智能居家") if r.get("狀態") == "啟用"]
    if not valid:
        return "目前沒有已設定的智能居家設備"
    lines = []
    for r in valid:
        buttons = r.get("按鈕", "")
        if buttons:
            lines.append(f"{r['名稱']}（類型：{r['類型']}，位置：{r.get('位置', '')}，按鈕：{buttons}）")
        else:
            lines.append(f"{r['名稱']}（類型：{r['類型']}，位置：{r.get('位置', '')}）")
    return "、".join(lines)

def get_ir_device_info(ctx):
    ir_devices = [r for r in ctx.get("智能居家") if r.get("狀態") == "啟用" and r.get("按鈕")]
    if not ir_devices:
        return "目前沒有 IR 設備"
    lines = [f"{r['名稱']}：可用按鈕為 {r['按鈕']}" for r in ir_devices]
    return "；".join(lines)

def get_device_id_by_name(device_name, ctx):
    for r in ctx.get("智能居家"):
        if r.get("狀態") == "啟用" and r.get("名稱") == device_name:
            return r.get("Device ID", "")
    return ""

def get_device_auth_by_name(device_name, ctx):
    for r in ctx.get("智能居家"):
        if r.get("狀態") == "啟用" and r.get("名稱") == device_name:
            return r.get("Auth", ""), r.get("Device ID", "")
    return "", ""

def get_all_devices_by_type(device_type, ctx):
    return [r for r in ctx.get("智能居家") if r.get("狀態") == "啟用" and r.get("類型") == device_type]


# ══════════════════════════════════════════
# 對話紀錄（讀取用 ctx，寫入用 worksheet）
# ══════════════════════════════════════════

def log_message(user_id, message):
    def _log():
        try:
            sheet = get_sheet("訊息紀錄")
            now = now_taipei().strftime("%Y-%m-%d %H:%M:%S")
            sheet.append_row([now, user_id, message])
        except Exception as e:
            print(f"[LOG ERROR] {e}")
    threading.Thread(target=_log, daemon=True).start()

def save_conversation(user_id, role, content):
    """同步寫入對話暫存（由呼叫端決定是否背景執行）"""
    try:
        sheet = get_sheet("對話暫存")
        now = now_taipei().strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([user_id, role, content, now])
    except Exception as e:
        print(f"[SAVE CONV ERROR] {e}")

def cleanup_conversation(user_id, limit=6):
    """清理對話暫存，保留最近 limit 則，其餘封存（背景執行）"""
    def _cleanup():
        try:
            sheet = get_sheet("對話暫存")
            archive = get_sheet("對話封存")
            records = sheet.get_all_records()
            user_records = [(i, r) for i, r in enumerate(records) if r.get("Line User ID") == user_id]
            if len(user_records) <= limit:
                return
            old_records = user_records[:-limit]
            rows_to_delete = []
            for i, r in old_records:
                archive.append_row([r.get("Line User ID"), r.get("角色"), r.get("內容"), r.get("時間")])
                rows_to_delete.append(i + 2)
            for row_num in sorted(rows_to_delete, reverse=True):
                sheet.delete_rows(row_num)
            print(f"[CLEANUP] 已封存 {len(old_records)} 則對話（{user_id}）")
        except Exception as e:
            print(f"[CLEANUP ERROR] {e}")
    threading.Thread(target=_cleanup, daemon=True).start()

def get_recent_conversation(user_id, ctx, limit=6):
    """從 ctx 快取讀取對話紀錄"""
    records = ctx.get("對話暫存")
    user_records = [(i, r) for i, r in enumerate(records) if r.get("Line User ID") == user_id]
    recent = user_records[-limit:]
    return [{"role": r["角色"], "content": r["內容"]} for _, r in recent if r.get("內容")]


# ══════════════════════════════════════════
# Claude API
# ══════════════════════════════════════════

def ask_claude(user_id, user_message, user_name, ctx):
    today = now_taipei().strftime("%Y-%m-%d")
    now_time = now_taipei().strftime("%H:%M")
    prompt = SYSTEM_PROMPT.format(
        today=today, now_time=now_time,
        family_info=get_family_members_info(ctx),
        food_info=get_current_food(ctx),
        todo_info=get_current_todo(ctx),
        device_info=get_device_info(ctx),
        ir_device_info=get_ir_device_info(ctx),
        current_user=user_name
    )
    history = get_recent_conversation(user_id, ctx)
    messages = history + [{"role": "user", "content": user_message}]
    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=prompt,
        messages=messages
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    print(f"[DEBUG] Claude raw: {repr(text)}")
    return text

def generate_notify_message(data_summary):
    try:
        today = now_taipei().strftime("%Y-%m-%d")
        now_time = now_taipei().strftime("%H:%M")
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system=f"你是家庭專屬管家。現在是 {today} {now_time}。請根據以下資料，用溫暖簡潔的管家語氣整理成一則推播訊息。適度使用 emoji，主動補充貼心提醒（快過期的催促、今天的待辦提醒注意時間、天氣提醒帶傘或注意溫差等）。不要加開頭問候語如「早安」，直接進入內容。只回傳推播文字，不要 JSON。",
            messages=[{"role": "user", "content": data_summary}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"[NOTIFY CLAUDE ERROR] {e}")
        return None


# ══════════════════════════════════════════
# Action Handlers（讀取用 ctx，寫入用 ctx.get_worksheet）
# ══════════════════════════════════════════

def handle_add(data, user_name, ctx):
    sheet = ctx.get_worksheet("食品庫存")
    today = now_taipei().strftime("%Y-%m-%d")
    sheet.append_row([
        data.get("name", ""),
        data.get("quantity", 1),
        data.get("unit", ""),
        data.get("expiry", ""),
        today,
        user_name,
        "有效"
    ])
    return f"✅ 已新增 {data.get('name')}，過期日 {data.get('expiry')}"

def handle_delete(data, ctx):
    sheet = ctx.get_worksheet("食品庫存")
    archive = ctx.get_worksheet("食品封存")
    records = ctx.get("食品庫存")
    for i, row in enumerate(records):
        if row.get("品名") == data.get("name") and row.get("狀態") == "有效":
            archive.append_row([
                row.get("品名"), row.get("數量"), row.get("單位"),
                row.get("過期日"), row.get("新增日"), row.get("新增者"), "已消耗"
            ])
            sheet.delete_rows(i + 2)
            records.pop(i)
            return f"✅ 已標記 {data.get('name')} 為已消耗"
    return f"❌ 找不到 {data.get('name')}"

def handle_modify(data, ctx):
    sheet = ctx.get_worksheet("食品庫存")
    archive = ctx.get_worksheet("食品封存")
    records = ctx.get("食品庫存")
    for i, row in enumerate(records):
        if row.get("品名") == data.get("name") and row.get("狀態") == "有效":
            # 數量歸零 → 封存
            if data.get("quantity") is not None and int(data.get("quantity", 1)) <= 0:
                archive.append_row([
                    row.get("品名"), row.get("數量"), row.get("單位"),
                    row.get("過期日"), row.get("新增日"), row.get("新增者"), "已消耗"
                ])
                sheet.delete_rows(i + 2)
                records.pop(i)
                return f"✅ {data.get('name')} 已全部消耗"
            # 逐欄更新
            if data.get("name_new"):
                sheet.update_cell(i + 2, 1, data.get("name_new"))
            if data.get("quantity") is not None:
                sheet.update_cell(i + 2, 2, int(data.get("quantity")))
            if data.get("unit"):
                sheet.update_cell(i + 2, 3, data.get("unit"))
            if data.get("expiry"):
                sheet.update_cell(i + 2, 4, data.get("expiry"))
            return f"✅ {data.get('name')} 已更新"
    return f"❌ 找不到 {data.get('name')}"

def handle_query(ctx):
    valid = [r for r in ctx.get("食品庫存") if r.get("狀態") == "有效"]
    if not valid:
        return "目前庫存是空的"
    lines = [f"• {r['品名']} {r['數量']}{r['單位']}（{r['過期日']}）" for r in valid]
    return "目前庫存：\n" + "\n".join(lines)

def handle_add_todo(data, user_name, ctx):
    sheet = ctx.get_worksheet("待辦事項")
    person = data.get("person") or user_name
    todo_type = data.get("type", "私人")
    sheet.append_row([
        data.get("item", ""),
        data.get("date", ""),
        data.get("time", ""),
        person,
        "待辦",
        todo_type
    ])
    date_str = data.get("date", "")
    time_str = data.get("time", "")
    time_part = f" {time_str}" if time_str else ""
    type_label = "🔒 私人" if todo_type == "私人" else "📢 公開"
    return f"✅ 已新增待辦：{data.get('item')}（{date_str}{time_part}）{type_label}"

def handle_modify_todo(data, ctx):
    sheet = ctx.get_worksheet("待辦事項")
    records = ctx.get("待辦事項")
    for i, row in enumerate(records):
        if row.get("事項") == data.get("item") and row.get("狀態") == "待辦":
            if data.get("item_new"):
                sheet.update_cell(i + 2, 1, data.get("item_new"))
            if data.get("date"):
                sheet.update_cell(i + 2, 2, data.get("date"))
            if data.get("time") is not None:
                sheet.update_cell(i + 2, 3, data.get("time"))
            if data.get("person"):
                sheet.update_cell(i + 2, 4, data.get("person"))
            if data.get("type"):
                sheet.update_cell(i + 2, 6, data.get("type"))
            return f"✅ 已更新「{data.get('item')}」"
    return f"❌ 找不到「{data.get('item')}」"

def handle_delete_todo(data, ctx):
    sheet = ctx.get_worksheet("待辦事項")
    archive = ctx.get_worksheet("待辦封存")
    records = ctx.get("待辦事項")
    for i, row in enumerate(records):
        if row.get("事項") == data.get("item") and row.get("狀態") == "待辦":
            archive.append_row([
                row.get("事項"), row.get("日期"), row.get("時間"),
                row.get("負責人"), "已完成", row.get("類型")
            ])
            sheet.delete_rows(i + 2)
            records.pop(i)
            return f"✅ 已標記「{data.get('item')}」為已完成"
    return f"❌ 找不到「{data.get('item')}」"

def handle_query_todo(user_name, ctx):
    valid = [r for r in ctx.get("待辦事項") if r.get("狀態") == "待辦"]
    if not valid:
        return "目前沒有待辦事項"
    lines = []
    for r in valid:
        todo_type = r.get("類型", "公開")
        person = r.get("負責人", "")
        if todo_type == "私人" and person != user_name:
            continue
        time_part = f" {r['時間']}" if r.get("時間") else ""
        lines.append(f"• {r['事項']}（{r['日期']}{time_part}）")
    if not lines:
        return "目前沒有待辦事項"
    return "待辦事項：\n" + "\n".join(lines)


# ── 智能居家 handlers ──

def handle_control_ac(data, ctx):
    device_name = data.get("device_name", "")
    device_id = get_device_id_by_name(device_name, ctx)

    if not device_id:
        ac_devices = get_all_devices_by_type("冷氣", ctx)
        if len(ac_devices) == 1:
            device_id = ac_devices[0].get("Device ID", "")
            device_name = ac_devices[0].get("名稱", device_name)
        elif len(ac_devices) > 1:
            names = "、".join([d.get("名稱") for d in ac_devices])
            return f"❌ 有多台冷氣（{names}），請指定要控制哪一台"
        else:
            return "❌ 找不到冷氣設備，請先在「智能居家」分頁設定"

    power = data.get("power", "on")
    if power == "off":
        result = switchbot_api.ac_turn_off(device_id)
    else:
        temperature = int(data.get("temperature", 26))
        mode_str = data.get("mode", "cool")
        fan_str = data.get("fan_speed", "auto")
        mode = switchbot_api.AC_MODE_MAP.get(mode_str, 2)
        fan = switchbot_api.AC_FAN_MAP.get(fan_str, 1)
        result = switchbot_api.ac_set_all(device_id, temperature, mode, fan, "on")

    if result.get("success"):
        return f"✅ {device_name} 指令已送出"
    else:
        return f"❌ {device_name} 控制失敗：{result.get('error', '未知錯誤')}"


def handle_control_ir(data, ctx):
    device_name = data.get("device_name", "")
    button = data.get("button", "")
    device_id = get_device_id_by_name(device_name, ctx)

    if not device_id:
        ir_devices = get_all_devices_by_type("IR", ctx)
        if len(ir_devices) == 1:
            device_id = ir_devices[0].get("Device ID", "")
            device_name = ir_devices[0].get("名稱", device_name)
        else:
            return f"❌ 找不到「{device_name}」，請確認設備名稱"

    if not button:
        return "❌ 請指定要按哪個按鈕"

    result = switchbot_api.ir_control(device_id, button)
    if result.get("success"):
        return f"✅ {device_name}「{button}」指令已送出"
    else:
        return f"❌ {device_name} 控制失敗：{result.get('error', '未知錯誤')}"


def handle_query_sensor(data, ctx):
    device_name = data.get("device_name", "")
    device_id = get_device_id_by_name(device_name, ctx)

    if not device_id:
        sensor_devices = get_all_devices_by_type("感應器", ctx)
        if len(sensor_devices) == 1:
            device_id = sensor_devices[0].get("Device ID", "")
            device_name = sensor_devices[0].get("名稱", device_name)
        elif len(sensor_devices) > 1:
            names = "、".join([d.get("名稱") for d in sensor_devices])
            return f"❌ 有多個感應器（{names}），請指定要查詢哪一個"
        else:
            return "❌ 找不到感應器設備，請先在「智能居家」分頁設定"

    result = switchbot_api.get_hub_sensor(device_id)
    if "error" in result:
        return f"❌ 讀取 {device_name} 失敗：{result['error']}"

    temp = result.get("temperature", "N/A")
    humidity = result.get("humidity", "N/A")
    return f"🌡️ {device_name}：溫度 {temp}°C，濕度 {humidity}%"


def handle_query_devices(ctx):
    valid = [r for r in ctx.get("智能居家") if r.get("狀態") == "啟用"]
    if not valid:
        return "目前沒有已設定的智能居家設備"
    lines = [f"• {r['名稱']}（{r['類型']}，{r.get('位置', '未設定')}）" for r in valid]
    return "已設定的設備：\n" + "\n".join(lines)


def handle_control_dehumidifier(data, ctx):
    device_name = data.get("device_name", "")
    auth, gwid = get_device_auth_by_name(device_name, ctx)

    if not auth:
        dh_devices = get_all_devices_by_type("除濕機", ctx)
        if len(dh_devices) == 1:
            auth = dh_devices[0].get("Auth", "")
            gwid = dh_devices[0].get("Device ID", "")
            device_name = dh_devices[0].get("名稱", device_name)
        elif len(dh_devices) > 1:
            names = "、".join([d.get("名稱") for d in dh_devices])
            return f"❌ 有多台除濕機（{names}），請指定要控制哪一台"
        else:
            return "❌ 找不到除濕機設備，請先在「智能居家」分頁設定"

    power = data.get("power", "")
    mode = data.get("mode", "")
    humidity = data.get("humidity", "")

    if power == "off":
        result = panasonic_api.dehumidifier_turn_off(auth, gwid)
    elif power == "on" and not mode and not humidity:
        result = panasonic_api.dehumidifier_turn_on(auth, gwid)
    else:
        panasonic_api.dehumidifier_turn_on(auth, gwid)
        result = {"success": True}
        if mode:
            result = panasonic_api.dehumidifier_set_mode(auth, mode)
            if not result.get("success"):
                return f"❌ {device_name} 模式設定失敗：{result.get('error')}"
        if humidity:
            result = panasonic_api.dehumidifier_set_humidity(auth, int(humidity))

    if result.get("success"):
        return f"✅ {device_name} 指令已送出"
    else:
        return f"❌ {device_name} 控制失敗：{result.get('error', '未知錯誤')}"


def handle_query_dehumidifier(data, ctx):
    device_name = data.get("device_name", "")
    auth, gwid = get_device_auth_by_name(device_name, ctx)

    if not auth:
        dh_devices = get_all_devices_by_type("除濕機", ctx)
        if len(dh_devices) == 1:
            auth = dh_devices[0].get("Auth", "")
            gwid = dh_devices[0].get("Device ID", "")
            device_name = dh_devices[0].get("名稱", device_name)
        else:
            return "❌ 找不到除濕機設備"

    status = panasonic_api.get_dehumidifier_status(auth, gwid)
    return panasonic_api.format_dehumidifier_status(status, device_name)


def handle_query_weather(data):
    date_str = data.get("date", "today")
    location = data.get("location", None)
    summary = weather_api.get_weather_summary(date_str, location)
    return weather_api.format_weather(summary)


# ══════════════════════════════════════════
# HTTP 端點
# ══════════════════════════════════════════

@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"status": "ok"}

@app.get("/switchbot/devices")
def list_switchbot_devices():
    result = switchbot_api.get_devices()
    if "error" in result:
        return {"status": "error", "message": result["error"]}
    devices = []
    for d in result.get("physical", []):
        devices.append({
            "名稱": d.get("deviceName", ""),
            "類型": d.get("deviceType", ""),
            "Device ID": d.get("deviceId", ""),
            "分類": "物理設備",
        })
    for d in result.get("infrared", []):
        devices.append({
            "名稱": d.get("deviceName", ""),
            "類型": d.get("remoteType", ""),
            "Device ID": d.get("deviceId", ""),
            "Hub ID": d.get("hubDeviceId", ""),
            "分類": "紅外線虛擬設備（IR）",
        })
    return {"status": "ok", "設備數量": len(devices), "設備列表": devices}

@app.get("/switchbot/test/{device_id}/{button_name}")
def test_switchbot_command(device_id: str, button_name: str):
    print(f"[TEST] device_id={device_id}, button={button_name}")
    result = switchbot_api.send_command(device_id, button_name, "default", "customize")
    print(f"[TEST] customize result: {result}")
    return {
        "status": "ok" if result.get("success") else "error",
        "device_id": device_id,
        "button": button_name,
        "command_type": "customize",
        "result": result
    }

@app.get("/switchbot/test_turnon/{device_id}")
def test_switchbot_turnon(device_id: str):
    result = switchbot_api.send_command(device_id, "turnOn", "default", "command")
    print(f"[TEST] turnOn result: {result}")
    return {"status": "ok" if result.get("success") else "error", "result": result}


# ══════════════════════════════════════════
# 推播端點（notify 系列使用獨立 ctx）
# ══════════════════════════════════════════

@app.post("/notify")
async def notify():
    try:
        ctx = RequestContext()
        ctx.load()

        today = now_taipei().date()

        # ── 食品過期檢查 ──
        expired = []
        soon = []
        this_week = []
        for r in ctx.get("食品庫存"):
            if r.get("狀態") != "有效":
                continue
            expiry_str = r.get("過期日", "")
            if not expiry_str:
                continue
            try:
                expiry = datetime.strptime(str(expiry_str), "%Y-%m-%d").date()
            except:
                continue
            days_left = (expiry - today).days
            label = f"{r['品名']}（{expiry_str}）"
            if days_left <= 0:
                expired.append(label)
            elif days_left <= 3:
                soon.append(label)
            elif days_left <= 7:
                this_week.append(label)

        members = ctx.get("家庭成員")

        # ── 溫濕度 ──
        sensor_lines = []
        try:
            sensor_devices = get_all_devices_by_type("感應器", ctx)
            for dev in sensor_devices:
                dev_id = dev.get("Device ID", "")
                dev_name = dev.get("名稱", "感應器")
                if dev_id:
                    result = switchbot_api.get_hub_sensor(dev_id)
                    if "error" not in result:
                        temp = result.get("temperature", "N/A")
                        humidity = result.get("humidity", "N/A")
                        sensor_lines.append(f"{dev_name}：{temp}°C / {humidity}%")
        except:
            pass

        # ── 今日天氣 ──
        weather_text = None
        try:
            weather_text = weather_api.get_weather_data_for_notify("today")
        except Exception as e:
            print(f"[NOTIFY WEATHER ERROR] {e}")

        # ── 待辦事項 ──
        todo_public = []
        todo_private = {}
        for r in ctx.get("待辦事項"):
            if r.get("狀態") != "待辦":
                continue
            date_str = r.get("日期", "")
            if not date_str:
                continue
            try:
                todo_date = datetime.strptime(str(date_str), "%Y-%m-%d").date()
            except:
                continue
            days_left = (todo_date - today).days
            if days_left <= 7:
                time_part = f" {r['時間']}" if r.get("時間") else ""
                overdue_mark = "⚠️ 未完成 " if days_left < 0 else ""
                label = f"{overdue_mark}{r['事項']}（{date_str}{time_part}）"
                if r.get("類型") == "私人":
                    person = r.get("負責人", "")
                    if person not in todo_private:
                        todo_private[person] = []
                    todo_private[person].append(label)
                else:
                    todo_public.append(label)

        # ── 組合並推播 ──
        has_content = expired or soon or this_week or sensor_lines or weather_text or todo_public or todo_private

        if has_content:
            for member in members:
                if member.get("狀態") != "啟用":
                    continue
                user_id = member.get("Line User ID")
                member_name = member.get("名稱", "")
                if not user_id:
                    continue

                data_parts = []
                if weather_text:
                    data_parts.append(f"今日天氣：{weather_text}")
                if expired:
                    data_parts.append("今天到期：" + "、".join(expired))
                if soon:
                    data_parts.append("3天內到期：" + "、".join(soon))
                if this_week:
                    data_parts.append("本週到期：" + "、".join(this_week))
                if sensor_lines:
                    data_parts.append("室內溫濕度：" + "、".join(sensor_lines))
                if todo_public:
                    data_parts.append("本週公開待辦：" + "、".join(todo_public))
                if member_name in todo_private:
                    data_parts.append("您的私人待辦：" + "、".join(todo_private[member_name]))

                if not data_parts:
                    continue

                data_summary = "\n".join(data_parts)
                message = generate_notify_message(data_summary)
                if not message:
                    message = data_summary

                line_bot_api.push_message(user_id, TextSendMessage(text=message))
                save_conversation(user_id, "assistant", message)
                cleanup_conversation(user_id)
                

        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/notify_weather")
async def notify_weather():
    try:
        today_weather = weather_api.get_weather_data_for_notify("today")
        tomorrow_weather = weather_api.get_weather_data_for_notify("tomorrow")
        if not tomorrow_weather:
            return {"status": "ok", "message": "無天氣資料，跳過推播"}

        ctx = RequestContext()
        ctx.load()
        members = ctx.get("家庭成員")

        data_parts = []
        data_parts.append(f"【重點】明日天氣預報：{tomorrow_weather}")
        if today_weather:
            data_parts.append(f"（參考）今日天氣：{today_weather}")
        data_parts.append("請以明日天氣為主，今日僅供比較溫差變化。如果明天比今天冷很多或會下雨，主動提醒。")
        data_summary = "\n".join(data_parts)
        message = generate_notify_message(data_summary)
        if not message:
            message = weather_api.get_tomorrow_weather_text()

        for member in members:
            if member.get("狀態") != "啟用":
                continue
            user_id = member.get("Line User ID")
            if not user_id:
                continue
            line_bot_api.push_message(user_id, TextSendMessage(text=message))
            save_conversation(user_id, "assistant", message)
            cleanup_conversation(user_id)

        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/notify_realtime")
async def notify_realtime():
    try:
        now = now_taipei()
        today = now.date()
        window_start = now
        window_end = now + timedelta(minutes=20)
        is_near_hour = now.minute <= 4 or now.minute >= 55

        ctx = RequestContext()
        ctx.load()
        todo_records = ctx.get("待辦事項")
        members = ctx.get("家庭成員")

        def push_to_member(person, todo_type, message):
            if todo_type == "私人":
                for member in members:
                    if member.get("狀態") == "啟用" and member.get("名稱") == person:
                        user_id = member.get("Line User ID")
                        if user_id:
                            line_bot_api.push_message(user_id, TextSendMessage(text=message))
                            save_conversation(user_id, "assistant", message)
                            cleanup_conversation(user_id)
            else:
                for member in members:
                    if member.get("狀態") == "啟用":
                        user_id = member.get("Line User ID")
                        if user_id:
                            line_bot_api.push_message(user_id, TextSendMessage(text=message))
                            save_conversation(user_id, "assistant", message)
                            cleanup_conversation(user_id)

        for r in todo_records:
            if r.get("狀態") != "待辦":
                continue
            date_str = r.get("日期", "")
            time_str = r.get("時間", "")
            person = r.get("負責人", "")
            todo_type = r.get("類型", "公開")

            if not date_str or not time_str:
                continue

            try:
                todo_dt = TZ.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
            except:
                continue

            if window_start <= todo_dt <= window_end:
                data_summary = f"即時提醒：{r['事項']}，時間 {time_str}"
                message = generate_notify_message(data_summary)
                if not message:
                    message = f"⏰ 提醒：{r['事項']}（{time_str}）"
                push_to_member(person, todo_type, message)

            elif is_near_hour and todo_dt.date() == today and todo_dt < now:
                data_summary = f"未完成提醒：{r['事項']} 原訂 {time_str}，尚未完成"
                message = generate_notify_message(data_summary)
                if not message:
                    message = f"⚠️ 未完成：{r['事項']}（原訂 {time_str}）"
                push_to_member(person, todo_type, message)

        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ══════════════════════════════════════════
# LINE Webhook
# ══════════════════════════════════════════

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    try:
        handler.handle(body.decode(), signature)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text
    reply = "抱歉，發生未知錯誤。"

    try:
        print(f"[1] user_id={user_id}, text={text}")
        log_message(user_id, text)
        
        # 廣播功能
        if text.strip().startswith("@all"):
            broadcast_msg = text.strip()[4:].strip()
            if broadcast_msg:
                members_sheet = get_sheet("家庭成員")
                members = members_sheet.get_all_records()
                sender_name = user_id
                for m in members:
                    if m.get("Line User ID") == user_id and m.get("狀態") == "啟用":
                        sender_name = m.get("名稱", user_id)
                        break
                push_text = f"📢 {sender_name}：{broadcast_msg}"
                for member in members:
                    if member.get("狀態") == "啟用":
                        mid = member.get("Line User ID")
                        if mid:
                            line_bot_api.push_message(mid, TextSendMessage(text=push_text))
                            save_conversation(mid, "assistant", push_text)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已廣播給全體成員"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請在 @all 後面輸入廣播內容"))
            return
            
        try:
            loading_resp = httpx.post(
                "https://api.line.me/v2/bot/chat/loading/start",
                headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"},
                json={"chatId": user_id, "loadingSeconds": 60}
            )
            print(f"[LOADING] status={loading_resp.status_code}, body={loading_resp.text}")
        except Exception as e:
            print(f"[LOADING ERROR] {e}")

        # ★ 一次批次讀取所有分頁（1 次 API 呼叫取代原本 7~8 次）
        ctx = RequestContext()
        ctx.load()

        user_name = get_user_name(user_id, ctx)
        print(f"[2] user_name={user_name}")
        result = ask_claude(user_id, text, user_name, ctx)
        print(f"[3] result={repr(result)}")

        if not result or not result.strip():
            print("[WARN] Claude returned empty response")
            reply = "抱歉，我沒有理解您的意思，可以再說一次嗎？"
        else:
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError as je:
                print(f"[WARN] JSON parse failed: {je}, raw: {repr(result)}")
                # 嘗試從回傳內容中提取 JSON（Claude 有時會在 JSON 前後加文字）
                json_match = re.search(r'\{.*\}', result, re.DOTALL)
                if json_match:
                    try:
                        parsed = json.loads(json_match.group())
                        print(f"[WARN] JSON extracted from partial response")
                    except json.JSONDecodeError:
                        parsed = None
                        reply = "抱歉，系統處理時發生了一點問題，請再試一次。"
                else:
                    # 純文字回覆，當作閒聊
                    reply = result
                    parsed = None

            if parsed is not None:
                print(f"[4] parsed type={type(parsed)}, value={parsed}")

                if isinstance(parsed, list):
                    actions = parsed
                    claude_reply = ""
                else:
                    actions = parsed.get("actions", [])
                    claude_reply = parsed.get("reply", "")

                print(f"[5] actions={actions}, claude_reply={claude_reply}")

                results = []
                for data in actions:
                    action = data.get("action")
                    if action == "add_food":
                        results.append(handle_add(data, user_name, ctx))
                    elif action == "delete_food":
                        results.append(handle_delete(data, ctx))
                    elif action == "modify_food":
                        results.append(handle_modify(data, ctx))
                    elif action == "query_food":
                        results.append(handle_query(ctx))
                    elif action == "add_todo":
                        results.append(handle_add_todo(data, user_name, ctx))
                    elif action == "modify_todo":
                        results.append(handle_modify_todo(data, ctx))
                    elif action == "delete_todo":
                        results.append(handle_delete_todo(data, ctx))
                    elif action == "query_todo":
                        results.append(handle_query_todo(user_name, ctx))
                    elif action == "control_ac":
                        results.append(handle_control_ac(data, ctx))
                    elif action == "control_ir":
                        results.append(handle_control_ir(data, ctx))
                    elif action == "query_sensor":
                        results.append(handle_query_sensor(data, ctx))
                    elif action == "control_dehumidifier":
                        results.append(handle_control_dehumidifier(data, ctx))
                    elif action == "query_dehumidifier":
                        results.append(handle_query_dehumidifier(data, ctx))
                    elif action == "query_devices":
                        results.append(handle_query_devices(ctx))
                    elif action == "query_weather":
                        results.append(handle_query_weather(data))
                    elif action == "unclear":
                        pass

                has_error = any("❌" in r for r in results if r)
                realtime_actions = {"query_devices", "query_dehumidifier"}
                has_realtime = any(d.get("action") in realtime_actions for d in actions)
                semantic_actions = {"query_weather", "query_sensor", "query_food", "query_todo"}
                has_semantic = any(d.get("action") in semantic_actions for d in actions)

                if has_error:
                    reply = "\n".join(results)
                elif has_semantic and not has_realtime:
                    raw_data = "\n".join(r for r in results if r and "❌" not in r)
                    if raw_data:
                        # 根據 action 類型選擇不同的 system prompt
                        action_types = {d.get("action") for d in actions}
                        if action_types & {"query_todo"}:
                            semantic_system = f"你是家庭管家。今天是 {now_taipei().strftime('%Y-%m-%d')}。根據以下待辦事項數據回覆。依日期排序，格式緊湊：每項一行，格式為「emoji 事項（日期 時間）」。不要用 markdown 標題或分隔線。只在今天或過期的事項補一句簡短提醒，其餘不加評語。最後可用一句話總結。"
                            semantic_max_tokens = 500
                        elif action_types & {"query_food"}:
                            semantic_system = f"你是家庭管家。今天是 {now_taipei().strftime('%Y-%m-%d')}。根據以下庫存數據回覆。每項一行，格式為「emoji 品名 數量單位（過期日）」。不要用 markdown 標題或分隔線。只在快過期（3天內）或已過期的品項補簡短提醒，其餘不加評語。"
                            semantic_max_tokens = 500
                        else:
                            semantic_system = "你是家庭管家。根據以下數據，用自然、簡潔、有溫度的語氣回覆使用者的問題。適度用 emoji。不要重複列出所有數據，挑重點回答。如果使用者問的是「冷嗎」「會下雨嗎」「濕度高嗎」這類問題，直接回答並給建議。"
                            semantic_max_tokens = 300
                        try:
                            semantic_reply = claude.messages.create(
                                model="claude-sonnet-4-6",
                                max_tokens=semantic_max_tokens,
                                system=semantic_system,
                                messages=[
                                    {"role": "user", "content": f"使用者問：{text}\n\n數據：\n{raw_data}"}
                                ]
                            )
                            reply = semantic_reply.content[0].text.strip()
                        except Exception as e:
                            print(f"[SEMANTIC CLAUDE ERROR] {e}")
                            reply = raw_data
                    else:
                        reply = claude_reply or "\n".join(results)
                elif has_realtime:
                    reply = "\n".join(results)
                elif claude_reply:
                    reply = claude_reply
                else:
                    reply = "\n".join(results)

        print(f"[6] reply={reply}")

    except Exception as e:
        print(f"[ERROR] {traceback.format_exc()}")
        reply = "抱歉，系統暫時出了點問題，請稍後再試。"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

    def _save():
        try:
            save_conversation(user_id, "user", text)
            save_conversation(user_id, "assistant", reply)
            cleanup_conversation(user_id)
        except Exception as e:
            print(f"[SAVE ERROR] {e}")
    threading.Thread(target=_save, daemon=True).start()