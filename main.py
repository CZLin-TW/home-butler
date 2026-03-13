from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import os
import json
import anthropic

app = FastAPI()

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """你是一個家庭 AI 管家，幫助管理家庭食品庫存和待辦事項。

當使用者傳訊息給你，請分析內容並回傳 JSON 陣列格式的指令，可以一次包含多個動作。

可能的 action：
- add_food：新增食品（需要 name, quantity, unit, expiry）
- delete_food：刪除食品（需要 name）
- query_food：查詢食品庫存（不需要額外欄位）
- add_todo：新增待辦事項（需要 item, date, time（選填）, person（選填））
- delete_todo：刪除待辦事項（需要 item）
- query_todo：查詢待辦事項（不需要額外欄位）
- unclear：語意不清，需要反問（需要 message）

今天的日期是 {today}。

規則：
- 食品數量若未指定，預設為 1
- 食品單位若未指定，預設為「個」
- 可以一次新增多個品項
- 待辦事項的 date 格式為 YYYY-MM-DD
- 待辦事項的 time 格式為 HH:MM，若未指定則留空
- 如果對話有上下文，請根據上下文推斷使用者的意思

請只回傳 JSON 陣列，不要有其他文字。範例：
[{{"action": "add_food", "name": "牛奶", "quantity": 1, "unit": "瓶", "expiry": "2026-03-25"}}]
[{{"action": "delete_food", "name": "牛奶"}}]
[{{"action": "query_food"}}]
[{{"action": "add_todo", "item": "看牙醫", "date": "2026-04-24", "time": "14:00", "person": "爸爸"}}]
[{{"action": "delete_todo", "item": "看牙醫"}}]
[{{"action": "query_todo"}}]
[{{"action": "unclear", "message": "請問是哪個品項喝完了？"}}]
"""

def get_sheet(name):
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    return spreadsheet.worksheet(name)

def log_message(user_id, message):
    sheet = get_sheet("訊息紀錄")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sheet.append_row([now, user_id, message])

def save_conversation(user_id, role, content):
    sheet = get_sheet("對話暫存")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sheet.append_row([user_id, role, content, now])

def get_recent_conversation(user_id, limit=6):
    sheet = get_sheet("對話暫存")
    records = sheet.get_all_records()
    user_records = [r for r in records if r.get("Line User ID") == user_id]
    recent = user_records[-limit:]
    return [{"role": r["角色"], "content": r["內容"]} for r in recent]

def ask_claude(user_id, user_message):
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = SYSTEM_PROMPT.format(today=today)
    history = get_recent_conversation(user_id)
    messages = history + [{"role": "user", "content": user_message}]
    response = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=500,
        system=prompt,
        messages=messages
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()

def get_user_name(user_id):
    try:
        sheet = get_sheet("家庭成員")
        records = sheet.get_all_records()
        for row in records:
            if row.get("Line User ID") == user_id and row.get("狀態") == "啟用":
                return row.get("名稱", user_id)
    except:
        pass
    return user_id

def handle_add(data, user_name):
    sheet = get_sheet("食品庫存")
    today = datetime.now().strftime("%Y-%m-%d")
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

def handle_delete(data):
    sheet = get_sheet("食品庫存")
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if row.get("品名") == data.get("name") and row.get("狀態") == "有效":
            sheet.update_cell(i + 2, 7, "已消耗")
            return f"✅ 已標記 {data.get('name')} 為已消耗"
    return f"❌ 找不到 {data.get('name')}"

def handle_query():
    sheet = get_sheet("食品庫存")
    records = sheet.get_all_records()
    valid = [r for r in records if r.get("狀態") == "有效"]
    if not valid:
        return "目前庫存是空的"
    lines = [f"• {r['品名']} {r['數量']}{r['單位']}（{r['過期日']}）" for r in valid]
    return "目前庫存：\n" + "\n".join(lines)

def handle_add_todo(data, user_name):
    sheet = get_sheet("待辦事項")
    person = data.get("person", user_name)
    sheet.append_row([
        data.get("item", ""),
        data.get("date", ""),
        data.get("time", ""),
        person,
        "待辦"
    ])
    date_str = data.get("date", "")
    time_str = data.get("time", "")
    time_part = f" {time_str}" if time_str else ""
    return f"✅ 已新增待辦：{data.get('item')}（{date_str}{time_part}）"

def handle_delete_todo(data):
    sheet = get_sheet("待辦事項")
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if row.get("事項") == data.get("item") and row.get("狀態") == "待辦":
            sheet.update_cell(i + 2, 5, "已完成")
            return f"✅ 已標記「{data.get('item')}」為已完成"
    return f"❌ 找不到「{data.get('item')}」"

def handle_query_todo():
    sheet = get_sheet("待辦事項")
    records = sheet.get_all_records()
    valid = [r for r in records if r.get("狀態") == "待辦"]
    if not valid:
        return "目前沒有待辦事項"
    lines = []
    for r in valid:
        time_part = f" {r['時間']}" if r.get("時間") else ""
        lines.append(f"• {r['事項']}（{r['日期']}{time_part}）")
    return "待辦事項：\n" + "\n".join(lines)

@app.get("/")
def root():
    return {"status": "ok"}

@app.post("/notify")
async def notify():
    try:
        sheet = get_sheet("食品庫存")
        records = sheet.get_all_records()
        today = datetime.now().date()

        expired = []
        soon = []
        this_week = []

        for r in records:
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

        if not expired and not soon and not this_week:
            return {"status": "no expiring items"}

        lines = []
        if expired:
            lines.append("🔴 今天到期：" + "、".join(expired))
        if soon:
            lines.append("🟡 3天內到期：" + "、".join(soon))
        if this_week:
            lines.append("🟢 本週到期：" + "、".join(this_week))

        message = "\n".join(lines)

        members_sheet = get_sheet("家庭成員")
        members = members_sheet.get_all_records()
        for member in members:
            if member.get("狀態") == "啟用":
                user_id = member.get("Line User ID")
                if user_id:
                    line_bot_api.push_message(user_id, TextSendMessage(text=message))

        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

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
    log_message(user_id, text)
    user_name = get_user_name(user_id)

    try:
        result = ask_claude(user_id, text)
        actions = json.loads(result)
        if not isinstance(actions, list):
            actions = [actions]

        replies = []
        for data in actions:
            action = data.get("action")
            if action == "add_food":
                replies.append(handle_add(data, user_name))
            elif action == "delete_food":
                replies.append(handle_delete(data))
            elif action == "query_food":
                replies.append(handle_query())
            elif action == "add_todo":
                replies.append(handle_add_todo(data, user_name))
            elif action == "delete_todo":
                replies.append(handle_delete_todo(data))
            elif action == "query_todo":
                replies.append(handle_query_todo())
            elif action == "unclear":
                replies.append(data.get("message", "請問您的意思是？"))
            else:
                replies.append("抱歉，我不太理解您的意思。")

        reply = "\n".join(replies)
    except json.JSONDecodeError:
        reply = "抱歉，請再說清楚一點。"
    except Exception as e:
        reply = f"系統錯誤：{str(e)}"

    save_conversation(user_id, "user", text)
    save_conversation(user_id, "assistant", reply)

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )