from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import pytz
import os
import json
import traceback
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
TZ = pytz.timezone('Asia/Taipei')

SYSTEM_PROMPT = """你是這個家庭的專屬管家，負責管理食品庫存與待辦事項。
你說話有禮、簡潔、帶有一點管家的從容感，偶爾會貼心提醒或關心，但不會囉嗦。
回覆確認動作時，用自然的語氣說明，而不是只有 ✅ 加一句話。
例如「好的，牛奶已為您登記，過期日 3/25。」而非「✅ 已新增牛奶，過期日 2026-03-25」。

家庭成員：{family_info}
發訊息的人說「我」時，程式會自動填入正確名稱，不需要你處理。
當提到稱謂時（例如「老婆」、「爸爸」），請根據上方家庭成員資料判斷對應的名稱填入 person 欄位。

目前食品庫存：{food_info}
目前待辦事項：{todo_info}

當使用者傳訊息給你，請分析內容並回傳 JSON 物件，可以一次包含多個動作。

可能的 action：
- add_food：新增食品（需要 name, quantity, unit, expiry）
- delete_food：食品全部用完時刪除（需要 name）
- modify_food：修改食品數量（需要 name, quantity）；數量變為 0 時程式會自動標記已消耗
  使用時機：「吃掉幾個」、「還剩幾個」、「改成幾個」等調整數量的情境
  quantity 填更新後的數量，請根據目前食品庫存自行計算
- query_food：查詢食品庫存（不需要額外欄位）
- add_todo：新增待辦事項（需要 item, date，選填：time, person, type）
  type 規則：
  - 使用者說「提醒我」、「我要」或未指定負責人 → 填「私人」
  - 使用者說「提醒大家」、「提醒全家」或明確說「公開」 → 填「公開」
  - 預設為「私人」
  person 填負責人名稱，若使用者說「我」或未指定，則留空（程式會自動填入）
- modify_todo：修改待辦事項（需要 item，選填：date, time, person, type，只填要修改的欄位）
- delete_todo：刪除待辦事項（需要 item）
- query_todo：查詢待辦事項（不需要額外欄位）
- unclear：語意不清，需要反問（需要 message）

今天的日期是 {today}，現在時間是 {now_time}。

規則：
- 食品數量若未指定，預設為 1
- 食品單位若未指定，預設為「個」
- 可以一次新增多個品項
- 待辦事項的 date 格式為 YYYY-MM-DD
- 待辦事項的 time 格式為 HH:MM，若未指定則留空
- 如果對話有上下文，請根據上下文推斷使用者的意思
- 修改待辦事項時，使用 modify_todo 而不是 delete_todo + add_todo，只填要修改的欄位

你必須永遠只回傳 JSON 物件，絕對不可以回傳其他任何文字、說明或確認訊息。格式如下：
{{"actions": [...], "reply": "用管家語氣寫給使用者看的回覆，自然、簡潔、有禮"}}

範例：
{{"actions": [{{"action": "add_food", "name": "牛奶", "quantity": 1, "unit": "瓶", "expiry": "2026-03-25"}}], "reply": "好的，牛奶已為您登記，過期日 3 月 25 日。"}}
{{"actions": [{{"action": "delete_food", "name": "牛奶"}}], "reply": "了解，牛奶已從庫存中移除。"}}
{{"actions": [{{"action": "modify_food", "name": "橘子", "quantity": 2}}], "reply": "好的，橘子已更新為 2 個。"}}
{{"actions": [{{"action": "query_food"}}], "reply": "為您查詢目前庫存。"}}
{{"actions": [{{"action": "add_todo", "item": "看牙醫", "date": "2026-04-24", "time": "14:00", "person": "爸爸"}}], "reply": "好的，4 月 24 日下午 2 點看牙醫已為您記下。"}}
{{"actions": [{{"action": "modify_todo", "item": "看牙醫", "date": "2026-04-25"}}], "reply": "好的，看牙醫已改到 4 月 25 日。"}}
{{"actions": [{{"action": "unclear", "message": "請問是哪個品項喝完了？"}}], "reply": "請問是哪個品項喝完了？"}}
"""

def now_taipei():
    return datetime.now(TZ)

def get_sheet(name):
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    return spreadsheet.worksheet(name)

def log_message(user_id, message):
    sheet = get_sheet("訊息紀錄")
    now = now_taipei().strftime("%Y-%m-%d %H:%M:%S")
    sheet.append_row([now, user_id, message])

def save_conversation(user_id, role, content):
    sheet = get_sheet("對話暫存")
    now = now_taipei().strftime("%Y-%m-%d %H:%M:%S")
    sheet.append_row([user_id, role, content, now])

def get_recent_conversation(user_id, limit=6):
    sheet = get_sheet("對話暫存")
    records = sheet.get_all_records(expected_headers=["Line User ID", "角色", "內容", "時間"])
    user_records = [r for r in records if r.get("Line User ID") == user_id]
    recent = user_records[-limit:]
    return [{"role": r["角色"], "content": r["內容"]} for r in recent]

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

def get_current_food():
    try:
        sheet = get_sheet("食品庫存")
        records = sheet.get_all_records()
        valid = [r for r in records if r.get("狀態") == "有效"]
        if not valid:
            return "目前庫存是空的"
        lines = [f"{r['品名']} {r['數量']}{r['單位']}（過期日 {r['過期日']}）" for r in valid]
        return "、".join(lines)
    except:
        return ""

def get_current_todo():
    try:
        sheet = get_sheet("待辦事項")
        records = sheet.get_all_records()
        valid = [r for r in records if r.get("狀態") == "待辦"]
        if not valid:
            return "目前沒有待辦事項"
        lines = []
        for r in valid:
            time_part = f" {r['時間']}" if r.get("時間") else ""
            type_part = "（私人）" if r.get("類型") == "私人" else "（公開）"
            lines.append(f"{r['事項']}／{r['負責人']}／{r['日期']}{time_part}{type_part}")
        return "、".join(lines)
    except:
        return ""

def ask_claude(user_id, user_message):
    today = now_taipei().strftime("%Y-%m-%d")
    now_time = now_taipei().strftime("%H:%M")
    family_info = get_family_members_info()
    food_info = get_current_food()
    todo_info = get_current_todo()
    prompt = SYSTEM_PROMPT.format(today=today, now_time=now_time, family_info=family_info, food_info=food_info, todo_info=todo_info)
    history = get_recent_conversation(user_id)
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

def handle_delete(data):
    sheet = get_sheet("食品庫存")
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if row.get("品名") == data.get("name") and row.get("狀態") == "有效":
            sheet.update_cell(i + 2, 7, "已消耗")
            return f"✅ 已標記 {data.get('name')} 為已消耗"
    return f"❌ 找不到 {data.get('name')}"

def handle_modify(data):
    sheet = get_sheet("食品庫存")
    records = sheet.get_all_records()
    new_quantity = int(data.get("quantity", 0))
    for i, row in enumerate(records):
        if row.get("品名") == data.get("name") and row.get("狀態") == "有效":
            if new_quantity <= 0:
                sheet.update_cell(i + 2, 7, "已消耗")
                return f"✅ {data.get('name')} 已全部消耗"
            else:
                sheet.update_cell(i + 2, 2, new_quantity)
                return f"✅ {data.get('name')} 數量已更新為 {new_quantity}"
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

def handle_modify_todo(data):
    sheet = get_sheet("待辦事項")
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if row.get("事項") == data.get("item") and row.get("狀態") == "待辦":
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

def handle_delete_todo(data):
    sheet = get_sheet("待辦事項")
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if row.get("事項") == data.get("item") and row.get("狀態") == "待辦":
            sheet.update_cell(i + 2, 5, "已完成")
            return f"✅ 已標記「{data.get('item')}」為已完成"
    return f"❌ 找不到「{data.get('item')}」"

def handle_query_todo(user_name):
    sheet = get_sheet("待辦事項")
    records = sheet.get_all_records()
    valid = [r for r in records if r.get("狀態") == "待辦"]
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

@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"status": "ok"}

@app.post("/notify")
async def notify():
    try:
        sheet = get_sheet("食品庫存")
        records = sheet.get_all_records()
        today = now_taipei().date()

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
        now = now_taipei()
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
                todo_dt = TZ.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
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
    reply = "抱歉，發生未知錯誤。"

    try:
        print(f"[1] user_id={user_id}, text={text}")
        log_message(user_id, text)
        print(f"[2] log_message done")
        user_name = get_user_name(user_id)
        print(f"[3] user_name={user_name}")
        result = ask_claude(user_id, text)
        print(f"[4] result={repr(result)}")
        parsed = json.loads(result)
        print(f"[5] parsed type={type(parsed)}, value={parsed}")

        if isinstance(parsed, list):
            actions = parsed
            claude_reply = ""
        else:
            actions = parsed.get("actions", [])
            claude_reply = parsed.get("reply", "")

        print(f"[6] actions={actions}, claude_reply={claude_reply}")

        results = []
        for data in actions:
            action = data.get("action")
            if action == "add_food":
                results.append(handle_add(data, user_name))
            elif action == "delete_food":
                results.append(handle_delete(data))
            elif action == "modify_food":
                results.append(handle_modify(data))
            elif action == "query_food":
                results.append(handle_query())
            elif action == "add_todo":
                results.append(handle_add_todo(data, user_name))
            elif action == "modify_todo":
                results.append(handle_modify_todo(data))
            elif action == "delete_todo":
                results.append(handle_delete_todo(data))
            elif action == "query_todo":
                results.append(handle_query_todo(user_name))
            elif action == "unclear":
                pass

        query_actions = {"query_food", "query_todo"}
        has_query = any(d.get("action") in query_actions for d in actions)

        if has_query:
            reply = "\n".join(results)
        else:
            reply = claude_reply if claude_reply else "\n".join(results)

        print(f"[7] reply={reply}")

    except Exception as e:
        reply = f"系統錯誤：{traceback.format_exc()}"
        print(f"[ERROR] {traceback.format_exc()}")

    save_conversation(user_id, "user", text)
    save_conversation(user_id, "assistant", reply)

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )