import threading
from linebot.models import TextSendMessage
from config import line_bot_api
from conversation import save_conversation, cleanup_conversation
from calendar_sync import sync_external_events
from sheets import build_row


def handle_add_todo(data, user_name, ctx):
    sheet = ctx.get_worksheet("待辦事項")
    person = data.get("person") or user_name
    todo_type = data.get("type", "私人")
    headers = sheet.row_values(1)
    sheet.append_row(build_row(headers, {
        "事項": data.get("item", ""),
        "日期": data.get("date", ""),
        "時間": data.get("time", ""),
        "負責人": person,
        "狀態": "待辦",
        "類型": todo_type,
        "來源": "本地",
        "屬性": "讀寫",
    }))
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


def handle_modify_todo(data, user_name, ctx):
    sheet = ctx.get_worksheet("待辦事項")
    records = ctx.get("待辦事項")
    item_name = data.get("item", "")
    for i, row in enumerate(records):
        if row.get("事項") == item_name and row.get("狀態") == "待辦":
            # 檢查屬性：唯讀項目不可修改
            prop = str(row.get("屬性", "")).strip()
            if prop == "唯讀":
                return f"「{data.get('item')}」是外部行事曆的項目，請到原本的日曆上操作"
            col = {h: idx + 1 for idx, h in enumerate(sheet.row_values(1))}
            update_count = 0
            if data.get("item_new"):
                sheet.update_cell(i + 2, col["事項"], data.get("item_new"))
                update_count += 1
            if data.get("date"):
                sheet.update_cell(i + 2, col["日期"], data.get("date"))
                update_count += 1
            if data.get("time") is not None:
                sheet.update_cell(i + 2, col["時間"], data.get("time"))
                update_count += 1
            if data.get("person"):
                sheet.update_cell(i + 2, col["負責人"], data.get("person"))
                update_count += 1
            if data.get("type"):
                sheet.update_cell(i + 2, col["類型"], data.get("type"))
                update_count += 1
            new_person = data.get("person")
            if new_person and new_person != row.get("負責人") and new_person != user_name:
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
    for i, row in enumerate(records):
        if row.get("事項") == item_name and row.get("狀態") == "待辦":
            prop = str(row.get("屬性", "")).strip()
            if prop == "唯讀":
                # 唯讀項目：只改狀態為已完成，不刪除不封存
                header = sheet.row_values(1)
                try:
                    status_col = header.index("狀態") + 1
                except ValueError:
                    status_col = 5  # fallback
                sheet.update_cell(i + 2, status_col, "已完成")
                row["狀態"] = "已完成"
                return f"✅ 已標記「{data.get('item')}」為已完成（下次同步後不再顯示）"
            archive_headers = archive.row_values(1)
            archive.append_row(build_row(archive_headers, {**row, "狀態": "已完成"}))
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
        lines.append(f"• {r['事項']}（{r['日期']}{time_part}）")

    if not lines:
        return "目前沒有待辦事項"

    return "待辦事項：\n" + "\n".join(lines)
