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


def select_todo(rows, data, actor=None):
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
    # Never choose the first of ambiguous names or duplicate IDs.
    return matches[0] if len(matches) == 1 else (None, None)
