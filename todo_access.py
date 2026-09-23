"""Todo visibility is based on an authenticated member's exact identity."""
from uuid import uuid4

TODO_ID = "待辦ID"


def visible(row, actor):
    return actor is None or row.get("類型") == "公開" or row.get("負責人") == actor


def actor_from_members(members, user_id):
    if user_id is None:
        return None  # Trusted API-key-only automation remains a system caller.
    matches = [m for m in members if m.get("Line User ID") == user_id and m.get("狀態") == "啟用"]
    if len(matches) != 1 or not matches[0].get("名稱"):
        raise PermissionError("登入成員無效或已停用。")
    return matches[0]["名稱"]


def new_todo_id():
    return "todo_" + uuid4().hex


def matching_todos(rows, data, actor=None):
    """Return only live, authorized candidates; an explicit ID never falls back."""
    todo_id = data.get("todo_id")
    matches = []
    for i, row in enumerate(rows, start=2):
        if row.get("狀態") != "待辦" or not visible(row, actor):
            continue
        if todo_id:
            match = row.get(TODO_ID) == todo_id
        else:
            match = (row.get("事項") == data.get("item")
                     and (not data.get("date_orig") or row.get("日期") == data["date_orig"])
                     and (not data.get("time_orig") or str(row.get("時間", "")) == data["time_orig"]))
        if match:
            matches.append((i, row))
    return matches


def select_todo(rows, data, actor=None):
    matches = matching_todos(rows, data, actor)
    # Never choose the first of ambiguous names or duplicate IDs.
    return matches[0] if len(matches) == 1 else (None, None)


def todo_selection_message(rows, data, actor=None):
    matches = matching_todos(rows, data, actor)
    if len(matches) < 2 or data.get("todo_id"):
        return "❌ 找不到可操作的待辦，請重新查詢待辦清單後指定項目。"
    options = []
    for _, row in matches[:8]:
        when = " ".join(str(row.get(k) or "") for k in ("日期", "時間")).strip() or "未設定日期時間"
        options.append(f"• {row.get('事項')}（{when}／{row.get('負責人') or '未指定負責人'}）")
    suffix = "\n另有其他符合項目，請再縮小日期範圍。" if len(matches) > 8 else ""
    return ("找到多筆待辦，請指定要操作哪個日期、時間的項目：\n"
            + "\n".join(options) + suffix
            + "\n若日期、時間也相同，請到 Dashboard 選取該筆待辦。")
