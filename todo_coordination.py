"""One process-wide boundary for read/locate/write operations on the todo sheet.

This is not a distributed lock and cannot coordinate edits made directly in Sheets.
RLock allows completion to generate the next recurring occurrence in the same write.
"""
from functools import wraps
from inspect import signature
from threading import RLock
from todo_access import TODO_ID, new_todo_id

todo_write_lock = RLock()


def refresh_todos(ctx):
    from sheets import ensure_columns, update_row_fields, _parse_sheet_values
    sheet = ctx.get_worksheet("待辦事項")
    ensure_columns(sheet, [TODO_ID])
    rows = _parse_sheet_values(sheet.get_all_values())
    for number, row in enumerate(rows, start=2):
        if row.get("事項") and not row.get(TODO_ID):
            row[TODO_ID] = new_todo_id()
            update_row_fields(sheet, number, {TODO_ID: row[TODO_ID]})
    ctx.set("待辦事項", rows)
    return rows


def todo_write(fn):
    params = signature(fn)

    @wraps(fn)
    def serialized(*args, **kwargs):
        ctx = params.bind(*args, **kwargs).arguments["ctx"]
        with todo_write_lock:
            # Requests may spend seconds parsing LINE input before reaching a handler.
            # Refresh INSIDE the boundary, before permission checks and row selection.
            refresh_todos(ctx)
            return fn(*args, **kwargs)
    return serialized
