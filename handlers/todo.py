import threading
from linebot.models import TextSendMessage
from config import line_bot_api, date_with_weekday
from conversation import save_conversation, cleanup_conversation
from calendar_sync import sync_external_events
from sheets import append_record, update_row_fields


def handle_add_todo(data, user_name, ctx):
    sheet = ctx.get_worksheet("待辦事項")
    person = data.get("person") or user_name
    todo_type = data.get("type", "私人")
    append_record(sheet, {
        "事項": data.get("item", ""),
        "日期": data.get("date", ""),
        "時間": data.get("time", ""),
        "負責人": person,
        "狀態": "待辦",
        "類型": todo_type,
        "來源": "本地",
        "屬性": "讀寫",
    })
    date_str = data.get("date", "")
    time_str = data.get("time", "")
    time_part = f" {time_str}" if time_str else ""
    type_label = "🔒 私人" if todo_type == "私人" else "📢 公開"
    if person != user_name:
        def _notify():
            for member in ctx.get("家庭成員"):
                if member.get("名稱") == person and member.get("狀態") == "啟用":
                    mid = member.get("Line User ID")
                    if mid:
                        notify_text = f"📋 {user_name} 指派了一項待辦給你：\n{data.get('item')}（{date_str}{time_part}）"
                        line_bot_api.push_message(mid, TextSendMessage(text=notify_text))
                        save_conversation(mid, "assistant", notify_text)
                        cleanup_conversation(mid)
                    break
        threading.Thread(target=_notify, daemon=True).start()
    return f"✅ 已新增待辦：{data.get('item')}（{date_str}{time_part}）{type_label}"


def _matches_todo(row, item_name, date_orig, time_orig):
    """三元組定位 row：(事項, 日期, 時間)。
    向後兼容：date_orig / time_orig 為空字串或 None 時跳過該欄比對（fallback 找第一筆）。
    狀態必須是「待辦」。
    """
    if row.get("事項") != item_name:
        return False
    if row.get("狀態") != "待辦":
        return False
    if date_orig and row.get("日期") != date_orig:
        return False
    if time_orig and str(row.get("時間", "")) != time_orig:
        return False
    return True


def handle_modify_todo(data, user_name, ctx):
    sheet = ctx.get_worksheet("待辦事項")
    records = ctx.get("待辦事項")
    item_name = data.get("item", "")
    date_orig = data.get("date_orig") or ""
    time_orig = data.get("time_orig") or ""
    for i, row in enumerate(records):
        if _matches_todo(row, item_name, date_orig, time_orig):
            # 檢查屬性：唯讀項目不可修改
            prop = str(row.get("屬性", "")).strip()
            if prop == "唯讀":
                return f"「{data.get('item')}」是外部行事曆的項目，請到原本的日曆上操作"
            updates = {}
            old_person = row.get("負責人")
            if data.get("item_new"):
                updates["事項"] = data.get("item_new")
            if data.get("date"):
                updates["日期"] = data.get("date")
            if data.get("time") is not None:
                updates["時間"] = data.get("time")
            if data.get("person"):
                updates["負責人"] = data.get("person")
            if data.get("type"):
                updates["類型"] = data.get("type")
            update_count = update_row_fields(sheet, i + 2, updates)
            row.update(updates)
            new_person = data.get("person")
            if new_person and new_person != old_person and new_person != user_name:
                # default-arg pattern：把當下的值「凍結」進函式簽名，避免 thread 起跑時
                # closure 抓到的是已被覆寫的變數。這個請求可能是「一次多 action」，
                # 例如 [modify_todo A, modify_todo B] 共用同一個 data dict──
                # 若 thread 直接 closure data，等它真正 run 時 data 可能已被改成 B 的內容。
                def _notify(person=new_person, item=data.get("item_new") or data.get("item"), date_str=data.get("date") or row.get("日期")):
                    for member in ctx.get("家庭成員"):
                        if member.get("名稱") == person and member.get("狀態") == "啟用":
                            mid = member.get("Line User ID")
                            if mid:
                                notify_text = f"📋 {user_name} 將一項待辦指派給你：\n{item}（{date_str}）"
                                line_bot_api.push_message(mid, TextSendMessage(text=notify_text))
                                save_conversation(mid, "assistant", notify_text)
                                cleanup_conversation(mid)
                            break
                threading.Thread(target=_notify, daemon=True).start()
            if update_count == 0:
                return f"❌ 找到「{data.get('item')}」但沒收到任何要更新的欄位（收到參數：{list(data.keys())}）"
            return f"✅ 已更新「{data.get('item')}」"
    return f"❌ 找不到「{data.get('item')}」"


def handle_delete_todo(data, ctx):
    sheet = ctx.get_worksheet("待辦事項")
    archive = ctx.get_worksheet("待辦封存")
    records = ctx.get("待辦事項")
    item_name = data.get("item", "")
    date_orig = data.get("date_orig") or ""
    time_orig = data.get("time_orig") or ""
    for i, row in enumerate(records):
        if _matches_todo(row, item_name, date_orig, time_orig):
            prop = str(row.get("屬性", "")).strip()
            if prop == "唯讀":
                # 唯讀項目：只改狀態為已完成，不刪除不封存
                update_row_fields(sheet, i + 2, {"狀態": "已完成"})
                row["狀態"] = "已完成"
                return f"✅ 已標記「{data.get('item')}」為已完成（下次同步後不再顯示）"
            append_record(archive, {**row, "狀態": "已完成"})
            sheet.delete_rows(i + 2)
            records.pop(i)
            return f"✅ 已標記「{data.get('item')}」為已完成"
    return f"❌ 找不到「{data.get('item')}」"


def handle_query_todo(user_name, ctx):
    # 先同步外部行事曆到 Sheet
    sync_external_events(ctx)

    valid = [r for r in ctx.get("待辦事項") if r.get("狀態") == "待辦"]
    lines = []
    for r in valid:
        todo_type = r.get("類型", "公開")
        person = r.get("負責人", "")
        if todo_type == "私人" and person != user_name:
            continue
        time_part = f" {r['時間']}" if r.get("時間") else ""
        # 日期附上 Python 算好的中文星期，避免 semantic 回覆時 LLM 自己推算星期算錯
        lines.append(f"• {r['事項']}（{date_with_weekday(r['日期'])}{time_part}）")

    if not lines:
        return "目前沒有待辦事項"

    return "待辦事項：\n" + "\n".join(lines)
