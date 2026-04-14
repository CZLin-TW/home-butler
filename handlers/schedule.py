import json
from config import now_taipei
from sheets import get_all_devices_by_type, build_row
from prompt import _format_schedule_params
from handlers.device import maintain_ac_auto_schedule


def handle_add_schedule(data, user_name, ctx):
    sheet = ctx.get_worksheet("排程指令")
    device_name = data.get("device_name", "")
    target_action = data.get("target_action", "")
    params = json.dumps(data.get("params", {}), ensure_ascii=False)
    trigger_time = data.get("trigger_time", "")
    now = now_taipei().strftime("%Y-%m-%d %H:%M")

    if not device_name:
        type_map = {"control_ac": "空調", "control_ir": "IR", "control_dehumidifier": "除濕機"}
        device_type = type_map.get(target_action, "")
        devices = get_all_devices_by_type(device_type, ctx)
        if len(devices) == 1:
            device_name = devices[0].get("名稱", "")
        else:
            return "❌ 請指定設備名稱"

    headers = sheet.row_values(1)
    new_row = {
        "設備名稱": device_name,
        "動作": target_action,
        "參數": params,
        "觸發時間": trigger_time,
        "建立者": user_name,
        "建立時間": now,
        "狀態": "待執行",
        "來源": "使用者",
    }
    sheet.append_row(build_row(headers, new_row))
    # 同步 ctx 快取，讓接著呼叫的 maintain_ac_auto_schedule 看得到這筆新排程
    ctx.get("排程指令").append(new_row)

    # AC 相關排程異動後重算該 AC 的 auto（新增 off 排程會清掉 auto）
    if target_action == "control_ac":
        maintain_ac_auto_schedule(device_name, ctx, transitioned_to_on=False)

    return f"✅ 已新增排程：{device_name} {trigger_time}"


def handle_delete_schedule(data, ctx):
    sheet = ctx.get_worksheet("排程指令")
    archive = ctx.get_worksheet("排程封存")
    records = ctx.get("排程指令")
    device_name = data.get("device_name", "")
    trigger_time = data.get("trigger_time", "")
    delete_all = data.get("all", False)

    deleted = 0
    indices_to_delete = []

    for i, row in enumerate(records):
        if row.get("狀態") != "待執行":
            continue
        if row.get("設備名稱") != device_name:
            continue
        if not delete_all and trigger_time and row.get("觸發時間") != trigger_time:
            continue
        indices_to_delete.append(i)

    archive_headers = archive.row_values(1)
    any_user_ac_deleted = False
    for i in sorted(indices_to_delete, reverse=True):
        row = records[i]
        # 記錄是否刪到了使用者手動設的 AC 排程 → 決定之後要不要重算 auto
        if row.get("動作") == "control_ac" and (row.get("來源") or "使用者") == "使用者":
            any_user_ac_deleted = True
        archive.append_row(build_row(archive_headers, {**row, "狀態": "已取消"}))
        sheet.delete_rows(i + 2)
        records.pop(i)
        deleted += 1

    if deleted:
        # 只在刪到使用者 AC 排程時重算（避免使用者剛刪掉 auto 又被立刻加回來的困擾）
        if any_user_ac_deleted:
            maintain_ac_auto_schedule(device_name, ctx, transitioned_to_on=False)
        return f"✅ 已取消 {deleted} 筆排程"
    return "❌ 找不到符合條件的排程"


def handle_query_schedule(ctx):
    schedules = [r for r in ctx.get("排程指令") if r.get("狀態") == "待執行"]
    if not schedules:
        return "目前沒有排程"
    lines = []
    for r in schedules:
        params_text = _format_schedule_params(r.get("動作", ""), r.get("參數", ""))
        lines.append(f"• {r['設備名稱']}｜{params_text}｜{r['觸發時間']}")
    return "排程列表：\n" + "\n".join(lines)
