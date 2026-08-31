from datetime import datetime

import notion_api
# _parse_sheet_values 是私有的，但這裡刻意重用：sync 結束要重建 ctx 快取，
# 用同一支函式才能保證型別（數值化、補齊欄位）跟 ctx.load() 讀出來的完全一致。
from sheets import build_row, ensure_columns, _parse_sheet_values

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


def sync_external_events(ctx):
    """把 Notion 事件同步進「待辦事項」Sheet。

    順序刻意是「先查 Notion，再動 Sheet」：
      1. `_fetch_members` 抓事件，查失敗的成員整個排除在這輪之外
      2. 用**即時** `get_all_values()` 決定要刪哪幾列（不信任 ctx 快取的列號）
      3. 把還在 Notion、且沒被標完成的事件寫回表尾
      4. 用剛剛那份即時內容重建 ctx 快取

    **完成記號的主鍵是 Notion page id**（寫在「外部ID」欄），不是名字＋時間：
    page id 在改標題、改日期、改時間之後都不變，而名字/日期/時間任一格對不上，
    就會同時造成「skip 失效（任務重新寫入）」和「記號被當過期刪掉」兩件事，
    使用者看到的就是「明明標了完成，五分鐘後又變回待辦、逾時提醒繼續響」。

    改版前寫進去的列沒有「外部ID」，那些列退回 `_legacy_key` 比對；等 sync
    重建過一輪，待辦列就都會帶上 id。

    完全不動任何列的情況（刻意 fail-closed，寧可留著也不要誤刪）：
      - 沒有任何啟用成員設定 Notion
      - 某成員這輪查詢失敗 → 他名下的列全部原樣保留
    """
    try:
        sheet = ctx.get_worksheet("待辦事項")

        # 1. 先查 Notion。查不到就什麼都不做——絕不能拿空結果當「Notion 上沒有了」。
        fetched = _fetch_members(ctx)
        if not fetched:
            return

        # 這輪 Notion 上確實還在的事件（兩種 key 都收，好對付還沒帶 id 的舊列）
        live_ids, live_legacy = set(), set()
        for _permission, events in fetched.values():
            for page_id, name, date_part, time_part in events:
                if page_id:
                    live_ids.add(page_id)
                live_legacy.add(_legacy_key(name, date_part, time_part))

        # 2. 即時讀 Sheet 決定要刪哪幾列。
        #    不用 ctx 快取的 index：背景 tick 與 LINE 請求會並行改動這張表，
        #    快取列號一旦過期就會刪到別人的列（handle_delete_todo 早在 297599f
        #    就改成即時定位了，這裡是漏網的那支）。
        ensure_columns(sheet, [EXTERNAL_ID_COLUMN])
        values = sheet.get_all_values()
        if not values:
            return
        headers = values[0]
        idx = {h: i for i, h in enumerate(headers) if h}

        def cell(row, name):
            i = idx.get(name, -1)
            return str(row[i]) if 0 <= i < len(row) else ""

        completed_ids, completed_legacy = set(), set()
        rebuild_rows, stale_rows = [], []
        for r, row in enumerate(values[1:], start=2):
            source = cell(row, "來源").strip()
            if not source or source == "本地":
                continue
            status = cell(row, "狀態").strip()
            page_id = cell(row, EXTERNAL_ID_COLUMN).strip()
            legacy = _legacy_key(cell(row, "事項"), cell(row, "日期"), cell(row, "時間"))

            # 完成記號要無條件收集：就算那位成員這輪查詢失敗、他的列不會被動，
            # 記號還是得算數，否則會重複寫入同一筆任務。
            if status == "已完成":
                if page_id:
                    completed_ids.add(page_id)
                else:
                    completed_legacy.add(legacy)

            if cell(row, "負責人").strip() not in fetched:
                continue  # 這輪沒同步到的成員 → 整列不動

            if status == "待辦":
                rebuild_rows.append(r)
            elif status == "已完成":
                still_there = page_id in live_ids if page_id else legacy in live_legacy
                if not still_there:
                    stale_rows.append(r)

        # 由下往上刪，避免刪一列之後後面的列號整批位移。
        for r in sorted(rebuild_rows + stale_rows, reverse=True):
            sheet.delete_rows(r)
        if rebuild_rows:
            print(f"[SYNC] 已刪除 {len(rebuild_rows)} 筆外部行事曆快取")
        if stale_rows:
            print(f"[SYNC] 已清理 {len(stale_rows)} 筆過期的已完成外部事件")

        # 3. 寫回這輪的事件（已標完成的跳過）
        new_rows = []
        for member_name, (permission, events) in fetched.items():
            for page_id, name, date_part, time_part in events:
                if page_id and page_id in completed_ids:
                    continue
                if _legacy_key(name, date_part, time_part) in completed_legacy:
                    continue
                new_rows.append(build_row(headers, {
                    "事項": name, "日期": date_part, "時間": time_part,
                    "負責人": member_name, "狀態": "待辦", "類型": "私人",
                    "來源": "Notion", "屬性": permission,
                    EXTERNAL_ID_COLUMN: page_id,
                }))

        if new_rows:
            # insert_data_option=INSERT_ROWS：預設的 OVERWRITE 會先找「表格範圍」再往下寫，
            # 表中間若有一整列空白，判定會提早結束而蓋掉既有資料列。改成一律插新列。
            sheet.append_rows(
                new_rows,
                value_input_option="USER_ENTERED",
                insert_data_option="INSERT_ROWS",
                table_range="A1",
            )
            print(f"[SYNC] 已寫入 {len(new_rows)} 筆外部行事曆事件")

        # 4. 用剛剛那份即時內容重建 ctx 快取（扣掉刪除的、加上新寫的）
        deleted = set(rebuild_rows) | set(stale_rows)
        kept = [row for r, row in enumerate(values[1:], start=2) if r not in deleted]
        ctx.set("待辦事項", _parse_sheet_values([headers] + kept + new_rows))

    except Exception as e:
        print(f"[SYNC ERROR] {e}")
