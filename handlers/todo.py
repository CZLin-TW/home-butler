import threading
from linebot.models import TextSendMessage
from config import line_bot_api, date_with_weekday
from conversation import save_conversation, cleanup_conversation
from calendar_sync import sync_external_events
from sheets import append_record, ensure_columns, update_row_fields
from hue_area_settings import DEFAULT_LIGHT_AREA_NAME, resolve_area


LIGHT_NOTIFY_COLUMN = "燈光提醒"
LIGHT_AREA_ID_COLUMN = "燈光區域ID"
HOUSEHOLD_LIGHT_NOTIFY_KEYWORDS = (
    "收衣服", "收衣", "晾衣服", "晾衣", "曬衣服", "曬衣", "洗衣服", "洗衣", "烘衣服", "烘衣",
    "洗衣機", "烘衣機", "倒垃圾", "垃圾", "回收", "廚餘", "拿包裹", "收包裹", "包裹", "取貨",
    "餵食", "餵貓", "餵狗", "貓砂", "澆花", "澆水", "關瓦斯", "瓦斯", "爐火", "關火",
    "洗碗", "掃地", "拖地", "吸地", "打掃",
)
HOUSEHOLD_LIGHT_NOTIFY_EXCLUSIONS = (
    "買", "購物", "採買", "預約", "牙醫", "看診", "醫生", "會議", "開會",
)


def _parse_bool(value, default=False):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y", "on", "是", "要", "需要", "開", "開啟", "啟用"):
        return True
    if text in ("false", "0", "no", "n", "off", "否", "不要", "不用", "關", "關閉", "停用"):
        return False
    return default


def _bool_cell(value):
    return "TRUE" if _parse_bool(value) else "FALSE"


def _is_household_light_notify_item(item):
    text = str(item or "").strip()
    if not text:
        return False
    if any(word in text for word in HOUSEHOLD_LIGHT_NOTIFY_EXCLUSIONS):
        return False
    return any(word in text for word in HOUSEHOLD_LIGHT_NOTIFY_KEYWORDS)


def _default_light_notify(data):
    return bool(data.get("time") and _is_household_light_notify_item(data.get("item")))


def _resolve_light_notify(data):
    if "light_notify" in data:
        return _parse_bool(data.get("light_notify"), default=False)
    return _default_light_notify(data)


def _resolve_light_area(data, light_notify, existing_area_id=""):
    time_value = data.get("time") if "time" in data else data.get("時間")
    if not light_notify or not time_value:
        return {"id": "", "name": ""}

    explicit_id = str(data.get("light_area_id") or "").strip()
    explicit_name = str(data.get("light_area") or "").strip()
    if explicit_id or explicit_name:
        return resolve_area(explicit_name, area_id=explicit_id)
    if existing_area_id:
        return resolve_area(area_id=existing_area_id)
    return resolve_area(DEFAULT_LIGHT_AREA_NAME)


def handle_add_todo(data, user_name, ctx):
    sheet = ctx.get_worksheet("待辦事項")
    ensure_columns(sheet, [LIGHT_NOTIFY_COLUMN, LIGHT_AREA_ID_COLUMN])
    person = data.get("person") or user_name
    todo_type = data.get("type", "私人")
    light_notify = _resolve_light_notify(data)
    light_area = _resolve_light_area(data, light_notify)
    append_record(sheet, {
        "事項": data.get("item", ""),
        "日期": data.get("date", ""),
        "時間": data.get("time", ""),
        "負責人": person,
        "狀態": "待辦",
        "類型": todo_type,
        "來源": "本地",
        "屬性": "讀寫",
        LIGHT_NOTIFY_COLUMN: "TRUE" if light_notify else "FALSE",
        LIGHT_AREA_ID_COLUMN: light_area.get("id", ""),
    })
    date_str = data.get("date", "")
    time_str = data.get("time", "")
    time_part = f" {time_str}" if time_str else ""
    type_label = "🔒 私人" if todo_type == "私人" else "📢 公開"
    area_name = light_area.get("name") or DEFAULT_LIGHT_AREA_NAME
    light_label = f"，燈光提醒：{area_name}" if light_notify and time_str else ""
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
    return f"✅ 已新增待辦：{data.get('item')}（{date_str}{time_part}）{type_label}{light_label}"


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
    ensure_columns(sheet, [LIGHT_NOTIFY_COLUMN, LIGHT_AREA_ID_COLUMN])
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
            if "light_notify" in data:
                updates[LIGHT_NOTIFY_COLUMN] = _bool_cell(data.get("light_notify"))
                light_notify_next = _parse_bool(data.get("light_notify"), default=False)
                updates[LIGHT_AREA_ID_COLUMN] = _resolve_light_area(
                    {**row, **data},
                    light_notify_next,
                    existing_area_id=str(row.get(LIGHT_AREA_ID_COLUMN, "") or ""),
                ).get("id", "")
            elif "light_area_id" in data or "light_area" in data:
                light_notify_next = _parse_bool(row.get(LIGHT_NOTIFY_COLUMN), default=False)
                updates[LIGHT_AREA_ID_COLUMN] = _resolve_light_area(
                    {**row, **data},
                    light_notify_next,
                    existing_area_id=str(row.get(LIGHT_AREA_ID_COLUMN, "") or ""),
                ).get("id", "")
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
