from datetime import datetime
import notion_api


def sync_external_events(ctx):
    """
    同步外部行事曆到待辦事項 Sheet：
    1. 收集已完成的外部事項 (name, date, time) 三元組（用於比對跳過）
    2. 刪掉所有來源≠本地且狀態=待辦的項目
    3. 拉 Notion API 寫入新事件（跳過已完成的）
    4. 清理已完成但 Notion 已不存在的項目
    5. 更新 ctx 快取

    去重 key 用 (事項, 日期, 時間) 而非單純 name：避免 Notion 上同名不同時間
    的兩筆任務、其中一筆被標完成後、另一筆也被連帶 skip 不同步進來。
    """
    try:
        sheet = ctx.get_worksheet("待辦事項")
        records = ctx.get("待辦事項")

        # 1. 收集已完成的外部事項（用 name+date+time 唯一識別）
        completed_external = set()
        for i, row in enumerate(records):
            source = str(row.get("來源", "")).strip()
            if source and source != "本地" and row.get("狀態") == "已完成":
                completed_external.add((
                    row.get("事項", ""),
                    row.get("日期", ""),
                    row.get("時間", ""),
                ))

        # 2. 刪掉所有外部且狀態=待辦的項目（從下往上刪）
        pending_external_indices = []
        for i, row in enumerate(records):
            source = str(row.get("來源", "")).strip()
            if source and source != "本地" and row.get("狀態") == "待辦":
                pending_external_indices.append(i)

        for i in sorted(pending_external_indices, reverse=True):
            sheet.delete_rows(i + 2)
            records.pop(i)

        if pending_external_indices:
            print(f"[SYNC] 已刪除 {len(pending_external_indices)} 筆外部行事曆快取")

        # 3. 遍歷成員，拉 Notion 事件寫入（跳過已完成的）
        members = ctx.get("家庭成員")
        new_rows = []
        new_event_keys = set()

        for member in members:
            if member.get("狀態") != "啟用":
                continue
            db_id = str(member.get("Notion Database ID", "")).strip()
            if not db_id:
                continue

            member_name = member.get("名稱", "")
            filters = str(member.get("Notion 篩選", "")).strip()
            permission = str(member.get("Notion 權限", "唯讀")).strip()

            events = notion_api.get_upcoming_events(db_id, filters)
            if not events:
                continue

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

                # 拆日期和時間
                if "T" in start_str:
                    try:
                        dt = datetime.fromisoformat(start_str)
                        date_part = dt.strftime("%Y-%m-%d")
                        time_part = dt.strftime("%H:%M")
                    except (ValueError, TypeError):
                        date_part = start_str[:10]
                        time_part = ""
                else:
                    date_part = start_str
                    time_part = ""

                event_key = (name, date_part, time_part)

                # 跳過已標記完成的事件（用三元組比對，同名不同時間互不影響）
                if event_key in completed_external:
                    new_event_keys.add(event_key)
                    continue

                new_row = [name, date_part, time_part, member_name, "待辦", "私人", "Notion", permission]
                new_rows.append(new_row)
                new_event_keys.add(event_key)
                records.append({
                    "事項": name, "日期": date_part, "時間": time_part,
                    "負責人": member_name, "狀態": "待辦", "類型": "私人",
                    "來源": "Notion", "屬性": permission
                })

        if new_rows:
            sheet.append_rows(new_rows, value_input_option='USER_ENTERED')
            print(f"[SYNC] 已寫入 {len(new_rows)} 筆外部行事曆事件")

        # 4. 清理已完成但 Notion 已不存在的項目（過期或被刪了）
        stale_indices = []
        for i, row in enumerate(records):
            source = str(row.get("來源", "")).strip()
            if source and source != "本地" and row.get("狀態") == "已完成":
                row_key = (row.get("事項", ""), row.get("日期", ""), row.get("時間", ""))
                if row_key not in new_event_keys:
                    stale_indices.append(i)

        for i in sorted(stale_indices, reverse=True):
            sheet.delete_rows(i + 2)
            records.pop(i)

        if stale_indices:
            print(f"[SYNC] 已清理 {len(stale_indices)} 筆過期的已完成外部事件")

        # 5. 更新 ctx 快取
        ctx.set("待辦事項", records)

    except Exception as e:
        print(f"[SYNC ERROR] {e}")
