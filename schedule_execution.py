"""Conservative, single-process schedule dispatch with durable attempt markers.

Sheets is not a transactional queue. This lock prevents overlapping dispatch in
this process; it is not a distributed lease. Do not add uvicorn workers without
moving job claiming to a transactional store.
"""
import json
import threading
import uuid
from datetime import datetime
from command_result import CommandResult

ATTENTION_STATES = {"執行失敗", "待確認"}
VISIBLE_STATES = {"待執行", *ATTENTION_STATES}
ATTEMPT_COLUMN = "執行識別碼"
RESULT_COLUMN = "執行結果"
_dispatch_lock = threading.Lock()
_identity_fields = ("設備名稱", "動作", "參數", "觸發時間", "建立者", "建立時間", "來源")


def _rows(sheet):
    values = sheet.get_all_values()
    if not values: return []
    headers = values[0]
    return [(i, {h: row[c] if c < len(row) else "" for c, h in enumerate(headers) if h})
            for i, row in enumerate(values[1:], start=2)]


def _identity(row):
    return tuple(str(row.get(key, "") or "") for key in _identity_fields)


def execute_pending(now, ctx, *, tz, handlers, ensure_columns, update_fields, antimold_source):
    if not _dispatch_lock.acquire(blocking=False): return set()
    processed = set()
    try:
        sheet = ctx.get_worksheet("排程指令")
        initial = _rows(sheet)
        if not any(r.get("狀態") == "待執行" for _, r in initial): return processed
        ensure_columns(sheet, [ATTEMPT_COLUMN, RESULT_COLUMN])
        for _, original in initial:
            if original.get("狀態") != "待執行": continue
            try:
                trigger = tz.localize(datetime.strptime(str(original.get("觸發時間", "")), "%Y-%m-%d %H:%M"))
            except (ValueError, TypeError):
                continue
            if trigger > now: continue
            # Re-read before dispatch: a user or AC maintenance may have moved/cancelled rows.
            live = _rows(sheet)
            match = next(((n, r) for n, r in live
                          if r.get("狀態") == "待執行" and _identity(r) == _identity(original)), None)
            if match is None: continue
            row_number, row = match
            device_name = row.get("設備名稱", "")
            if (now - trigger).total_seconds() > 7200 and row.get("來源") != antimold_source:
                update_fields(sheet, row_number, {"狀態": "已過期"})
                processed.add(device_name)
                continue

            attempt = uuid.uuid4().hex
            claim = {"狀態": "待確認", ATTEMPT_COLUMN: attempt,
                     RESULT_COLUMN: "指令處理中；若結果未更新，請先確認設備狀態。系統不會自動重送。"}
            try:
                # Persist BEFORE sending. A restart / dropped reply must not replay an IR toggle.
                update_fields(sheet, row_number, claim)
            except Exception as exc:
                print(f"[SCHEDULE CLAIM ERROR] {type(exc).__name__}; no command sent")
                continue
            row.update(claim)
            ctx.set("排程指令", [r for _, r in live])
            action = row.get("動作", "")
            try:
                params = json.loads(row.get("參數", "{}"))
                if not isinstance(params, dict):
                    raise ValueError("params must be an object")
            except (ValueError, TypeError):
                outcome = CommandResult.failed("排程參數格式錯誤，未送出指令。")
            else:
                handler = handlers.get(action)
                if handler is None:
                    outcome = CommandResult.failed("不支援的排程動作，未送出指令。")
                else:
                    try:
                        # Do not let legacy 'only one device' fallback redirect a deleted target.
                        kind = {"control_ac": "空調", "control_ir": "IR", "control_dehumidifier": "除濕機"}[action]
                        exists = any(d.get("名稱") == device_name and d.get("類型") == kind
                                     and d.get("狀態") == "啟用" for d in ctx.get("智能居家"))
                        if not exists:
                            outcome = CommandResult.failed("找不到啟用中的排程設備，未送出指令。")
                        else:
                            params["device_name"] = device_name
                            kwargs = {"from_auto_schedule": row.get("來源") == "自動"} if action == "control_ac" else {}
                            outcome = handler(params, ctx, **kwargs)
                            if not isinstance(outcome, CommandResult):
                                outcome = CommandResult.unknown("設備回應格式無法確認，請檢查設備狀態。")
                    except Exception as exc:
                        print(f"[SCHEDULE COMMAND ERROR] attempt={attempt} {type(exc).__name__}")
                        outcome = CommandResult.unknown("指令結果未確認，請檢查設備狀態。系統不會自動重送。")
            status = {"success": "已執行", "failed": "執行失敗", "unknown": "待確認"}[outcome.status]
            try:
                # AC handlers can insert/delete schedules. Never reuse the old row number.
                matches = [(n, r) for n, r in _rows(sheet) if r.get(ATTEMPT_COLUMN) == attempt]
                if len(matches) != 1:
                    print(f"[SCHEDULE RESULT LOST] attempt={attempt}; row missing or ambiguous")
                    continue
                update_fields(sheet, matches[0][0], {"狀態": status, RESULT_COLUMN: outcome.message})
                if status == "已執行": processed.add(device_name)
                print(f"[SCHEDULE RESULT] attempt={attempt} status={outcome.status}")
            except Exception as exc:
                # The persisted pending-confirmation marker remains: do not repeat the command.
                print(f"[SCHEDULE RESULT WRITE ERROR] attempt={attempt} {type(exc).__name__}")
        ctx.set("排程指令", [r for _, r in _rows(sheet)])
        return processed
    finally:
        _dispatch_lock.release()
