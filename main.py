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

SYSTEM_PROMPT = """你是一個家庭 AI 管家，專門幫助管理家庭食品庫存。

當使用者傳訊息給你，請分析內容並回傳 JSON 格式的指令。

可能的 action：
- add：新增食品（需要 name, quantity, unit, expiry）
- delete：刪除食品（需要 name）
- query：查詢庫存（不需要額外欄位）
- unclear：語意不清，需要反問（需要 message 說明要問什麼）

今天的日期是 {today}。

請只回傳 JSON，不要有其他文字。範例：
{{"action": "add", "name": "牛奶", "quantity": 2, "unit": "瓶", "expiry": "2026-03-25"}}
{{"action": "delete", "name": "牛奶"}}
{{"action": "query"}}
{{"action": "unclear", "message": "請問是哪個品項喝完了？"}}
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

def ask_claude(user_message):
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = SYSTEM_PROMPT.format(today=today)
    response = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=500,
        system=prompt,
        messages=[{"role": "user", "content": user_message}]
    )
    return response.content[0].text

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

@app.get("/")
def root():
    return {"status": "ok"}

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
        result = ask_claude(text)
        data = json.loads(result)
        action = data.get("action")

        if action == "add":
            reply = handle_add(data, user_name)
        elif action == "delete":
            reply = handle_delete(data)
        elif action == "query":
            reply = handle_query()
        elif action == "unclear":
            reply = data.get("message", "請問您的意思是？")
        else:
            reply = "抱歉，我不太理解您的意思。"
    except Exception as e:
        reply = f"系統錯誤：{str(e)}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )