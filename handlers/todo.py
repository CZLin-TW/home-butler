import threading
from linebot.models import TextSendMessage
from config import line_bot_api, date_with_weekday, now_taipei
from conversation import save_conversation, cleanup_conversation
from calendar_sync import sync_external_events
from sheets import append_record, ensure_columns, update_row_fields
from hue_area_settings import DEFAULT_LIGHT_AREA_NAME
from handlers.recurring_todo import materialize_recurring_todos, RULE_ID_COLUMN
# 燈光提醒 / 布林解析等共用工具搬到 todo_helpers，讓 recurring_todo 也能複用。
# 用 as 別名保留原本的私有命名，下方 handler body 完全不動。
from handlers.todo_helpers import (
    LIGHT_NOTIFY_COLUMN,
    LIGHT_AREA_ID_COLUMN,
    parse_bool as _parse_bool,
    bool_cell as _bool_cell,
    resolve_light_notify as _resolve_light_notify,
    resolve_light_area as _resolve_light_area,
)


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
            # 寫入前即時定位列號，不信任快取的 i+2（背景同步搬動外部列會讓本地列位移）。
            row_number, _ = _locate_todo_row(sheet.get_all_values(), item_name, date_orig, time_orig)
            if row_number is None:
                return f"❌ 找不到「{item_name}」"
            update_count = update_row_fields(sheet, row_number, updates)
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


def _locate_todo_row(values, item_name, date_orig, time_orig):
    """在即時 Sheet 值矩陣裡定位待辦列，回 (row_number_1based, 屬性)；找不到回 (None, None)。

    純函式好測；row_number 直接是 Sheet 列號（含 header）。比對規則與 _matches_todo 一致：
    事項完全相等、狀態＝待辦，date/time_orig 非空才比對。
    """
    if not values or len(values) < 2:
        return None, None
    headers = values[0]
    idx = {h: c for c, h in enumerate(headers)}
    ci, cs, cd, ct, cp = (idx.get("事項", -1), idx.get("狀態", -1),
                          idx.get("日期", -1), idx.get("時間", -1), idx.get("屬性", -1))

    def cell(row, c):
        return row[c] if 0 <= c < len(row) else ""

    for r, row in enumerate(values[1:], start=2):
        if cell(row, ci) != item_name:
            continue
        if cs != -1 and cell(row, cs) != "待辦":
            continue
        if date_orig and str(cell(row, cd)) != date_orig:
            continue
        if time_orig and str(cell(row, ct)) != time_orig:
            continue
        return r, str(cell(row, cp)).strip()
    return None, None


# 提醒文字的開頭（notify._process_todo_reminders 產生）。用來在待辦列已經消失時，
# 從對話記錄回推「這筆確實存在過、只是已經結案了」。
_REMINDER_PREFIXES = ("⏰ 提醒：", "⚠️ 未完成：", "⚠️ 已逾時約")


def _find_completed_row(values, item_name):
    """找同名但狀態＝已完成的列，回 (日期, 時間)；沒有回 None。

    刻意不比對 date_orig/time_orig：這裡只是要判斷「是不是早就結案了」，
    使用者口頭給的時間常常跟表上不完全一致，比寬一點才不會又誤報成找不到。
    """
    if not values or len(values) < 2:
        return None
    headers = values[0]
    idx = {h: c for c, h in enumerate(headers)}
    ci, cs, cd, ct = (idx.get("事項", -1), idx.get("狀態", -1),
                      idx.get("日期", -1), idx.get("時間", -1))
    if ci == -1 or cs == -1:
        return None

    def cell(row, c):
        return row[c] if 0 <= c < len(row) else ""

    for row in values[1:]:
        if cell(row, ci) == item_name and cell(row, cs) == "已完成":
            return str(cell(row, cd)), str(cell(row, ct))
    return None


def _reminded_recently(item_name, ctx, user_name=""):
    """對話暫存裡有沒有這個任務的提醒（⏰/⚠️ 開頭）？

    用來分辨兩件本來共用「❌ 找不到」的事：
      - Notion 那邊狀態改掉 → sync 把列砍了不再寫回 → 列消失，但今天確實提醒過
      - 使用者名字講錯 / 根本沒這筆 → 從來沒提醒過
    知道 user_name 就只看那個成員自己的對話（避免比對到別人的私人待辦）。
    """
    user_ids = set()
    if user_name:
        for m in ctx.get("家庭成員"):
            if m.get("名稱") == user_name and m.get("Line User ID"):
                user_ids.add(str(m.get("Line User ID")))

    for r in ctx.get("對話暫存"):
        if r.get("角色") != "assistant":
            continue
        if user_ids and str(r.get("Line User ID", "")) not in user_ids:
            continue
        content = str(r.get("內容", "") or "")
        if item_name and item_name in content and content.startswith(_REMINDER_PREFIXES):
            return True
    return False


def _explain_missing_todo(values, item_name, ctx, user_name=""):
    """定位不到「待辦」列時，講清楚是哪一種情況。

    `_locate_todo_row` 只認狀態＝待辦的列，所以「早就完成了」跟「根本沒這筆」以前都會
    落到同一句 ❌ 找不到——使用者看到 ❌ 會以為指令沒生效，其實事情早就結案了
    （實際遇過：Notion 任務逾時提醒了一整個上午，中途 Notion 狀態改掉、列被 sync 移除，
    使用者回「結束了」卻收到 ❌）。前兩種都回 ✅，因為使用者想要的狀態已經達成。
    """
    done = _find_completed_row(values, item_name)
    if done:
        when = " ".join(x for x in done if x)
        suffix = f"（{when}）" if when else ""
        return f"✅ 「{item_name}」已經是完成狀態了{suffix}，不用再標記一次"
    if _reminded_recently(item_name, ctx, user_name):
        return (f"✅ 「{item_name}」已經不在待辦清單上了，不用再標記"
                f"（外部行事曆項目在 Notion 那邊狀態改變後就會自動移除）")
    return f"❌ 找不到「{item_name}」"


def handle_delete_todo(data, ctx, user_name=""):
    sheet = ctx.get_worksheet("待辦事項")
    archive = ctx.get_worksheet("待辦封存")
    records = ctx.get("待辦事項")
    item_name = data.get("item", "")
    date_orig = data.get("date_orig") or ""
    time_orig = data.get("time_orig") or ""

    # 寫入前用「即時 Sheet 內容」定位列號，不信任 request 開頭快取的 i+2：背景 realtime
    # tick 的 sync_external_events 每次都把所有外部（Notion）列砍掉重建到表尾，若發生在
    # 解析→寫入的空窗，那筆待辦就換了列 → 用舊 index 會寫到別列（實測：唯讀任務標完成
    # 沒生效卻回報成功）。改成即時定位，順帶讓「真的找不到」正確回 ❌ 而非假成功。
    values = sheet.get_all_values()
    row_number, prop = _locate_todo_row(values, item_name, date_orig, time_orig)
    if row_number is None:
        return _explain_missing_todo(values, item_name, ctx, user_name)

    if prop == "唯讀":
        # 唯讀項目：只改狀態為已完成，不刪除不封存
        update_row_fields(sheet, row_number, {"狀態": "已完成"})
        for row in records:  # 同步 request 快取，讓同一輪後續動作看到
            if _matches_todo(row, item_name, date_orig, time_orig):
                row["狀態"] = "已完成"
                break
        return f"✅ 已標記「{item_name}」為已完成（下次同步後不再顯示）"

    # 本地項目：封存 + 刪列。封存內容從快取取（找不到就用手上的最小資訊），
    # 但刪除一定用即時列號，避免砍錯列。
    cache_row = next((r for r in records if _matches_todo(r, item_name, date_orig, time_orig)), None)
    append_record(archive, {**(cache_row or {"事項": item_name, "日期": date_orig, "時間": time_orig}), "狀態": "已完成"})
    sheet.delete_rows(row_number)
    if cache_row is not None and cache_row in records:
        records.remove(cache_row)
    # 若刪的是週期實例，完成當下就補下一筆（否則要等下個 5 分 tick 才出現，感覺像沒反應）。
    # 該實例已移出活表 → materialize 看不到 active 實例 → 補下一筆；idempotent，補完不會再補。
    if str((cache_row or {}).get(RULE_ID_COLUMN, "") or "").strip():
        try:
            materialize_recurring_todos(now_taipei(), ctx)
        except Exception as e:
            print(f"[recur] inline regen after complete failed: {e}")
    return f"✅ 已標記「{item_name}」為已完成"


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
