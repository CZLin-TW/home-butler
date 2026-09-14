"""HB-owned run timers for HA ACs, persisted in the existing schedule sheet.

One cycle record survives completion/unknown results until a confirmed off.
This prevents an on snapshot or a Render restart from rearming the same run.
No HA automation or device command is created while reconciling settings.
"""
import json
from datetime import timedelta
from threading import RLock

LOCK = RLock()
HOURS_COLUMN = "自動關機小時數"
SOURCE = "自動（HA）"


def hours_for(row):
    try:
        if type(row.get(HOURS_COLUMN)) is bool:
            return 0
        value = float(row.get(HOURS_COLUMN) or 0)
        return int(value) if value.is_integer() and 0 <= value <= 168 else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def metadata(row):
    try:
        value = json.loads(row.get("參數") or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def keep_cycle(row):
    return row.get("來源") == SOURCE and not metadata(row).get("_auto_closed")


def reconcile(ctx, now):
    """Observe HA once per scheduler tick; unknown/offline never means off.

First observation of an already running AC starts at now. Temperature/mode
changes don't reset it. A changed hours setting starts a new timer at now.
"""
    import ha_climate
    from sheets import append_record, update_row_fields, ensure_columns
    from schedule_execution import _rows, _identity, HA_MANUAL_SOURCE, RESULT_COLUMN
    devices = ctx.get("智能居家")
    changed = set()
    sheet = None

    def write(row, fields):
        nonlocal sheet
        sheet = sheet or ctx.get_worksheet("排程指令")
        matches = [(n, live) for n, live in _rows(sheet) if _identity(live) == _identity(row)
                   and live.get("狀態") == row.get("狀態")]
        if len(matches) != 1:
            raise ValueError("Auto-off cycle changed during update")
        ensure_columns(sheet, [RESULT_COLUMN])
        update_row_fields(sheet, matches[0][0], fields)
        row.update(fields)

    for device in devices:
        name = device.get("名稱")
        if (not name or device.get("類型") != "空調" or device.get("狀態") != "啟用"
                or sum(d.get("名稱") == name for d in devices) != 1 or not ha_climate.managed(name)):
            continue
        hours = hours_for(device)
        # Disabled installations without cycle records need no worksheet lookup.
        if not hours and not any(r.get("設備名稱") == name and keep_cycle(r) for r in ctx.get("排程指令")):
            continue
        state = ha_climate.status(name)
        power = state.get("lastPower") if state.get("available") and not state.get("stateUncertain") else ""
        if hours and not power:
            continue
        # Normal ticks reuse the job's batch read. Resolve live identity/position
        # only for a mutation; never pay extra Sheet reads just for counting.
        rows = list(enumerate(ctx.get("排程指令"), start=2))
        cycles = [(n, r) for n, r in rows if r.get("設備名稱") == name and keep_cycle(r)]
        active = []
        for number, row in cycles:
            meta = metadata(row)
            if not hours or power == "off" or meta.get("_auto_hours") != hours:
                fields = {"參數": json.dumps({**meta, "_auto_closed": True}, ensure_ascii=False)}
                if row.get("狀態") == "待執行" or meta.get("_auto_paused"):
                    fields.update({"狀態": "已取消", RESULT_COLUMN: "已關機、停用或變更自動關機時數"})
                write(row, fields)
                changed.add(name)
            else:
                active.append((number, row))
        if not hours or power != "on":
            continue
        # Never guess which duplicate cycle should execute.
        if len(active) > 1:
            raise ValueError("Duplicate active HA auto-off cycles")
        user_off = any(r.get("設備名稱") == name and r.get("動作") == "control_ac"
                       and r.get("狀態") in ("待執行", "待確認", "執行失敗") and r.get("來源") == HA_MANUAL_SOURCE
                       and metadata(r).get("power") == "off" for _, r in rows)
        if not active:
            sheet = sheet or ctx.get_worksheet("排程指令")
            if any(r.get("設備名稱") == name and keep_cycle(r) for _, r in _rows(sheet)):
                raise ValueError("Auto-off cycle already exists")
            ensure_columns(sheet, [RESULT_COLUMN])
            started = now.strftime("%Y-%m-%d %H:%M")
            row = {"設備名稱": name, "動作": "control_ac", "建立者": "系統", "建立時間": started,
                   "來源": SOURCE, "觸發時間": (now + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M"),
                   "狀態": "已取消" if user_off else "待執行",
                   "參數": json.dumps({"power": "off", "_auto_hours": hours, "_auto_paused": user_off}),
                   RESULT_COLUMN: "手動關機排程優先" if user_off else ""}
            append_record(sheet, row)
            ctx.get("排程指令").append(row)
            changed.add(name)
        else:
            number, row = active[0]
            meta = metadata(row)
            # Failed/claimed/completed attempts are terminal for this power cycle.
            if row.get("執行識別碼") or row.get("狀態") not in ("待執行", "已取消"):
                continue
            if user_off != bool(meta.get("_auto_paused")):
                write(row, {
                    "參數": json.dumps({**meta, "_auto_paused": user_off}),
                    "狀態": "已取消" if user_off else "待執行",
                    RESULT_COLUMN: "手動關機排程優先" if user_off else ""})
                changed.add(name)
    if sheet is not None:
        ctx.set("排程指令", [r for _, r in _rows(sheet)])
    # Closed successful/cancelled records can now be archived by the normal job.
    changed.update(r.get("設備名稱") for r in ctx.get("排程指令")
                   if r.get("來源") == SOURCE and not keep_cycle(r))
    return changed


def dispatch_allowed(row, ctx, schedules=None):
    """Recheck configuration and current HA state immediately before claiming."""
    import ha_climate
    name = row.get("設備名稱")
    devices = [d for d in ctx.get("智能居家") if d.get("名稱") == name and d.get("狀態") == "啟用"]
    state = ha_climate.status(name)
    meta = metadata(row)
    from schedule_execution import HA_MANUAL_SOURCE
    schedules = ctx.get("排程指令") if schedules is None else schedules
    if any(r.get("設備名稱") == name and r.get("來源") == HA_MANUAL_SOURCE
           and r.get("動作") == "control_ac" and r.get("狀態") in ("待執行", "待確認", "執行失敗")
           and metadata(r).get("power") == "off" for r in schedules):
        return False
    return (len(devices) == 1 and hours_for(devices[0]) == meta.get("_auto_hours")
            and hours_for(devices[0]) > 0 and keep_cycle(row) and not meta.get("_auto_paused")
            and state.get("available") and not state.get("stateUncertain") and state.get("lastPower") == "on")


def describe(device, schedules):
    import ha_climate
    name = device["名稱"]
    hours = hours_for(device)
    managed = ha_climate.managed(name)
    active = next((r for r in schedules if r.get("設備名稱") == name
                   and (keep_cycle(r) if managed else r.get("來源") == "自動" and r.get("狀態") == "待執行")), None)
    state = ha_climate.status(name) if managed else {"available": True, "lastPower": device.get("最後電源")}
    status = ("disabled" if not hours else "unavailable" if not state.get("available") or state.get("stateUncertain")
              else "waiting_power" if state.get("lastPower") != "on" else "waiting_observation")
    if hours and active and status not in ("unavailable", "waiting_power"):
        status = "manual_priority" if metadata(active).get("_auto_paused") else {
            "待執行": "counting", "待確認": "needs_review", "執行失敗": "needs_review",
            "已執行": "completed", "已過期": "expired", "已取消": "cancelled"}.get(active.get("狀態"), status)
    return {"hours": hours, "status": status,
            "scheduled_at": active.get("觸發時間") if active and status == "counting" else None}
