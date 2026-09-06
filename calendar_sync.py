from todo_access import TODO_ID, new_todo_id
from datetime import datetime
from threading import Lock
from todo_coordination import todo_write_lock
from notion_reconcile import plan_changes

import notion_api
# _parse_sheet_values 是私有的，但這裡刻意重用：sync 結束要重建 ctx 快取，
# 用同一支函式才能保證型別（數值化、補齊欄位）跟 ctx.load() 讀出來的完全一致。
from sheets import build_row, ensure_columns, _parse_sheet_values, update_row_fields

# 待辦事項分頁存 Notion page id 的欄位；不存在時 sync 第一次跑會自己補上。
EXTERNAL_ID_COLUMN = "外部ID"


def _split_start(start_str):
    """Notion date.start → (日期 YYYY-MM-DD, 時間 HH:MM)。全天事件時間為空字串。"""
    if "T" in start_str:
        try:
            dt = datetime.fromisoformat(start_str)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
        except (ValueError, TypeError):
            return start_str[:10], ""
    return start_str, ""


def _legacy_key(name, date_str, time_str):
    """改版前的 (事項, 日期, 時間) 三元組主鍵。

    只用在「外部ID 還是空的」那些列——也就是這次改版之前就存在的完成記號。
    兩邊都 strip 再比，避免儲存格前後多一個空白就永遠對不上。
    """
    return (str(name).strip(), str(date_str).strip(), str(time_str).strip())


def _fetch_members(ctx):
    """抓每位啟用成員的 Notion 事件。

    回傳 {成員名稱: (屬性, [(page_id, 事項, 日期, 時間), ...])}，**只含這一輪查詢
    成功的成員**。查詢失敗（`get_upcoming_events` 回 None）的成員不會出現在裡面，
    呼叫端因此不會去動他的任何一列——這是刻意的：舊版把「Notion 查不到」和
    「Notion 上已經沒有了」當同一件事，一次 timeout 就會把該成員所有「已完成」
    記號當過期刪光，下一輪任務全部復活、逾時提醒重新開始響。
    """
    fetched = {}
    for member in ctx.get("家庭成員"):
        if member.get("狀態") != "啟用":
            continue
        db_id = str(member.get("Notion Database ID", "") or "").strip()
        if not db_id:
            continue

        member_name = member.get("名稱", "")
        filters = str(member.get("Notion 篩選", "") or "").strip()
        # 空白儲存格的 .get() 拿到的是 ""，dict 預設值不會頂上 → 額外用 or 補。
        # 少了這個 or，屬性會寫成空字串 → 標完成時走成「本地」分支（封存＋刪列），
        # 那列一消失，下一輪 sync 就把任務原封不動從 Notion 拉回來變待辦。
        permission = str(member.get("Notion 權限", "") or "").strip() or "唯讀"

        events = notion_api.get_upcoming_events(db_id, filters)
        if events is None:
            print(f"[SYNC] {member_name}：Notion 查詢失敗，本輪跳過（不刪待辦、不清完成記號）")
            continue

        parsed = []
        for item in events:
            name = item.get("Event", "")
            if not name:
                continue
            date_val = item.get("Date", {})
            if not isinstance(date_val, dict):
                continue
            start_str = date_val.get("start", "")
            if not start_str:
                continue
            date_part, time_part = _split_start(start_str)
            page_id = str(item.get(notion_api.PAGE_ID_KEY, "") or "").strip()
            parsed.append((page_id, name, date_part, time_part))

        fetched[member_name] = (permission, parsed)
    return fetched


_sync_lock = Lock()


def sync_external_events(ctx):
    """Fetch outside the write lock; reconcile only successful members by stable ID.

    Only one sync runs at a time, so an older Notion fetch cannot overwrite a newer
    sync. Manual todo operations continue while Notion is slow. All app writers
    share todo_write_lock for the live read, row selection and write sequence.
    """
    if not _sync_lock.acquire(blocking=False):
        return
    try:
        fetched = _fetch_members(ctx)
        if not fetched:
            return
        with todo_write_lock:
            sheet = ctx.get_worksheet("待辦事項")
            ensure_columns(sheet, [EXTERNAL_ID_COLUMN, TODO_ID])
            values = sheet.get_all_values()
            changes = plan_changes(values, fetched)
            try:
                # Update before structural edits, keeping the observed row numbers valid.
                for number, fields in changes.updates:
                    update_row_fields(sheet, number, fields)
                for number in changes.removals:
                    sheet.delete_rows(number)
                if changes.additions:
                    # Never retry append: a dropped reply might already have inserted rows.
                    # The next sync re-reads IDs and will not append those rows again.
                    sheet.append_rows(
                        [build_row(values[0], {**r, TODO_ID: new_todo_id()}) for r in changes.additions],
                        value_input_option="USER_ENTERED", insert_data_option="INSERT_ROWS", table_range="A1",
                    )
            finally:
                # Re-read even after partial failure; do not guess what Sheets accepted.
                changed = changes.updates or changes.removals or changes.additions
                ctx.set("待辦事項", _parse_sheet_values(sheet.get_all_values() if changed else values))
            print(f"[SYNC] added={len(changes.additions)} updated={len(changes.updates)} removed={len(changes.removals)}")
    except Exception as e:
        print(f"[SYNC ERROR] {e}")
        raise
    finally:
        _sync_lock.release()
