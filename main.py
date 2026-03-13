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

家庭成員：{family_info}
發訊息的人說「我」時，程式會自動填入正確名稱，不需要你處理。
當提到稱謂時（例如「老婆」、「爸爸」），請根據上方家庭成員資料判斷對應的名稱填入 person 欄位。

當使用者傳訊息給你，請分析內容並回傳 JSON 陣列格式的指令，可以一次包含多個動作。

可能的 action：
- add_food：新增食品（需要 name, quantity, unit, expiry）
- delete_food：刪除食品（需要 name）
- query_food：查詢食品庫存（不需要額外欄位）
- add_todo：新增待辦事項（需要 item, date，選填：time, person, type）
  type 規則：
  - 使用者說「提醒我」、「我要」或未指定負責人 → 填「私人」
  - 使用者說「提醒大家」、「提醒全家」或明確說「公開」 → 填「公開」
  - 預設為「私人」
  person 填負責人名稱，若使用者說「我」或未指定，則留空（程式會自動填入）
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
    family_info = get_family_members_info()
    prompt = SYSTEM_PROMPT.format(today=today, family_info=family_info)
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

def get_family_members_info():
    try:
        sheet = get_sheet("家庭成員")
        records = sheet.get_all_records()
        members = []
        for row in records:
            if row.get("狀態") == "啟用":
                members.append(f"{row.get('名稱')}（稱謂：{row.get('稱謂', '')}）")
        return "、".join(members)
    except:
        return ""
    
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
    person = data.get("person") or user_name
    todo_type = data.get("type", "公開")
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

        members_sheet = get_sheet("家庭成員")
        members = members_sheet.get_all_records()

        # 食品推播
        food_lines = []
        if expired:
            food_lines.append("🔴 今天到期：" + "、".join(expired))
        if soon:
            food_lines.append("🟡 3天內到期：" + "、".join(soon))
        if this_week:
            food_lines.append("🟢 本週到期：" + "、".join(this_week))

        if food_lines:
            food_message = "\n".join(food_lines)
            for member in members:
                if member.get("狀態") == "啟用":
                    user_id = member.get("Line User ID")
                    if user_id:
                        line_bot_api.push_message(user_id, TextSendMessage(text=food_message))

        # 待辦推播
        todo_sheet = get_sheet("待辦事項")
        todo_records = todo_sheet.get_all_records()

        todo_lines_public = []
        todo_private = {}

        for r in todo_records:
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
            if 0 <= days_left <= 7:
                time_part = f" {r['時間']}" if r.get("時間") else ""
                label = f"• {r['事項']}（{date_str}{time_part}）"
                if r.get("類型") == "私人":
                    person = r.get("負責人", "")
                    if person not in todo_private:
                        todo_private[person] = []
                    todo_private[person].append(label)
                else:
                    todo_lines_public.append(label)

        if todo_lines_public:
            todo_message = "📋 本週待辦：\n" + "\n".join(todo_lines_public)
            for member in members:
                if member.get("狀態") == "啟用":
                    user_id = member.get("Line User ID")
                    if user_id:
                        line_bot_api.push_message(user_id, TextSendMessage(text=todo_message))

        for person_name, items in todo_private.items():
            todo_message = "🔒 您的私人待辦：\n" + "\n".join(items)
            for member in members:
                if member.get("狀態") == "啟用" and member.get("名稱") == person_name:
                    user_id = member.get("Line User ID")
                    if user_id:
                        line_bot_api.push_message(user_id, TextSendMessage(text=todo_message))

        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/notify_realtime")
async def notify_realtime():
    try:
        from datetime import timedelta
        now = datetime.now()
        window_start = now
        window_end = now + timedelta(minutes=15)

        todo_sheet = get_sheet("待辦事項")
        todo_records = todo_sheet.get_all_records()
        members_sheet = get_sheet("家庭成員")
        members = members_sheet.get_all_records()

        for r in todo_records:
            if r.get("狀態") != "待辦":
                continue
            date_str = r.get("日期", "")
            time_str = r.get("時間", "")
            if not date_str or not time_str:
                continue
            try:
                todo_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            except:
                continue
            if window_start <= todo_dt <= window_end:
                message = f"⏰ 提醒：{r['事項']}（{time_str}）"
                person = r.get("負責人", "")
                todo_type = r.get("類型", "公開")
                if todo_type == "私人":
                    for member in members:
                        if member.get("狀態") == "啟用" and member.get("名稱") == person:
                            user_id = member.get("Line User ID")
                            if user_id:
                                line_bot_api.push_message(user_id, TextSendMessage(text=message))
                else:
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