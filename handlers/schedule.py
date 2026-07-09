import json
from datetime import datetime
from config import now_taipei
from sheets import get_all_devices_by_type, append_record, update_row_fields
from prompt import _format_schedule_params
from handlers.device import maintain_ac_auto_schedule


def _norm_trigger(s):
    """觸發時間正規化成標準 'YYYY-MM-DD HH:MM'（補前導零）。

    模型建立與刪除排程時對前導零不一致（時而 9:00 時而 09:00），而刪除/修改是用
    原始字串比對找目標 → 只差一個零就零命中「找不到」。這裡把兩邊都收斂成同一標準形
    再比，順便在寫入時正規化讓 Sheet 資料一致。解析失敗（非預期格式）就回原字串，不硬改。
    """
    s = str(s or "").strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return s


def _locate_schedule_rows(values, device_name, trigger_time, match_all):
    """在即時 Sheet 值矩陣裡定位待執行排程列，回 [(row_number_1based, row_dict), ...]。

    純函式好測；row_number 直接是 Sheet 列號（含 header）。寫入/刪除前用它取代 request
    快取的 i+2：背景 tick（排程執行封存、防黴、自動關機）會增刪排程列，快取位置會過時 →
    用舊 index 會打到別列。trigger_time 須已正規化；match_all=True 時忽略 trigger、
    抓該設備所有待執行。
    """
    if not values or len(values) < 2:
        return []
    headers = values[0]
    hidx = {h: c for c, h in enumerate(headers)}
    out = []
    for r, rv in enumerate(values[1:], start=2):
        row = {h: (rv[c] if c < len(rv) else "") for h, c in hidx.items()}
        if row.get("狀態") != "待執行":
            continue
        if row.get("設備名稱") != device_name:
            continue
        if not match_all and trigger_time and _norm_trigger(row.get("觸發時間")) != trigger_time:
            continue
        out.append((r, row))
    return out


def handle_add_schedule(data, user_name, ctx):
    sheet = ctx.get_worksheet("排程指令")
    device_name = data.get("device_name", "")
    target_action = data.get("target_action", "")
    params = json.dumps(data.get("params", {}), ensure_ascii=False)
    trigger_time = _norm_trigger(data.get("trigger_time", ""))
    now = now_taipei().strftime("%Y-%m-%d %H:%M")

    if not device_name:
        type_map = {"control_ac": "空調", "control_ir": "IR", "control_dehumidifier": "除濕機"}
        device_type = type_map.get(target_action, "")
        devices = get_all_devices_by_type(device_type, ctx)
        if len(devices) == 1:
            device_name = devices[0].get("名稱", "")
        else:
            return "❌ 請指定設備名稱"

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
    append_record(sheet, new_row)
    # 同步 ctx 快取，讓接著呼叫的 maintain_ac_auto_schedule 看得到這筆新排程
    ctx.get("排程指令").append(new_row)

    # AC 相關排程異動後重算該 AC 的 auto（新增 off 排程會清掉 auto）
    if target_action == "control_ac":
        maintain_ac_auto_schedule(device_name, ctx, transitioned_to_on=False)

    return f"✅ 已新增排程：{device_name} {trigger_time}"


def handle_modify_schedule(data, user_name, ctx):
    """編輯一筆待執行的排程。

    識別目標：用 (原 device_name, 原 trigger_time) 找待執行的 row。
    可改欄位（全部選填，至少要有一個）：
      device_name_new / target_action_new / params_new / trigger_time_new
    params_new 是「整個 dict 取代」，不做 merge——對應 UI 是重填表單，
    partial merge 反而難理解。

    建立者 / 建立時間 / 來源 不動，保留原 metadata。

    跨類型編輯（control_ac ↔ control_ir / control_dehumidifier）允許，
    呼叫端負責 params_new 形狀對得上新 action（與 add_schedule 一致，後端不驗）。
    """
    del user_name  # 保留簽名一致；建立者不變

    sheet = ctx.get_worksheet("排程指令")
    records = ctx.get("排程指令")
    device_name = data.get("device_name", "")
    trigger_time = _norm_trigger(data.get("trigger_time", ""))

    if not device_name or not trigger_time:
        return "❌ 請指定原排程的設備名稱與觸發時間"

    new_device = data.get("device_name_new")
    new_action = data.get("target_action_new")
    new_params = data.get("params_new")
    new_trigger = data.get("trigger_time_new")

    if new_device is None and new_action is None and new_params is None and new_trigger is None:
        return f"❌ 沒收到任何要更新的欄位（{device_name} {trigger_time}）"

    target_idx = None
    target_row = None
    for i, row in enumerate(records):
        if row.get("狀態") != "待執行":
            continue
        if row.get("設備名稱") != device_name:
            continue
        if _norm_trigger(row.get("觸發時間")) != trigger_time:
            continue
        target_idx = i
        target_row = row
        break

    if target_row is None:
        return "❌ 找不到符合條件的排程"

    old_action = target_row.get("動作", "")

    # 寫入前即時定位列號，不信任快取的 target_idx+2（背景 tick 增刪排程列會位移）。
    live = _locate_schedule_rows(sheet.get_all_values(), device_name, trigger_time, False)
    if not live:
        return "❌ 找不到符合條件的排程"
    sheet_row = live[0][0]

    updates = {}
    if new_device is not None:
        updates["設備名稱"] = new_device
    if new_action is not None:
        updates["動作"] = new_action
    if new_params is not None:
        params_str = json.dumps(new_params, ensure_ascii=False)
        updates["參數"] = params_str
    if new_trigger is not None:
        updates["觸發時間"] = _norm_trigger(new_trigger)
    update_row_fields(sheet, sheet_row, updates)
    target_row.update(updates)

    # AC auto 重算：原與新只要任一是 control_ac 就要重算對應裝置。
    # 跨裝置（原 客廳 → 新 主臥）兩台都要算；同台 AC 只改參數呼叫一次。
    # ctx 快取已在前面同步，maintain_ac_auto_schedule 讀到的是更新後狀態。
    final_device = target_row.get("設備名稱", device_name)
    final_action = target_row.get("動作", old_action)
    devices_to_recompute = set()
    if old_action == "control_ac":
        devices_to_recompute.add(device_name)
    if final_action == "control_ac":
        devices_to_recompute.add(final_device)
    for dev in devices_to_recompute:
        maintain_ac_auto_schedule(dev, ctx, transitioned_to_on=False)

    return f"✅ 已更新「{device_name} {trigger_time}」"


def handle_delete_schedule(data, ctx):
    sheet = ctx.get_worksheet("排程指令")
    archive = ctx.get_worksheet("排程封存")
    records = ctx.get("排程指令")
    device_name = data.get("device_name", "")
    trigger_time = _norm_trigger(data.get("trigger_time", ""))
    delete_all = data.get("all", False)

    # 即時定位待刪列，不信任快取 i+2（背景 tick 會增刪排程列造成位移 → 刪錯列）。
    matches = _locate_schedule_rows(sheet.get_all_values(), device_name, trigger_time, delete_all)
    if not matches:
        return "❌ 找不到符合條件的排程"

    any_user_ac_deleted = False
    # 倒序刪，避免刪一列後其餘列號位移。封存內容直接用即時讀到的 row。
    for row_number, row in sorted(matches, key=lambda x: x[0], reverse=True):
        # 記錄是否刪到了使用者手動設的 AC 排程 → 決定之後要不要重算 auto
        if row.get("動作") == "control_ac" and (row.get("來源") or "使用者") == "使用者":
            any_user_ac_deleted = True
        append_record(archive, {**row, "狀態": "已取消"})
        sheet.delete_rows(row_number)

    # 同步 request 快取：用內容比對移除（與剛刪掉的 live 條件一致），非索引。
    def _cache_match(rec):
        return (rec.get("狀態") == "待執行"
                and rec.get("設備名稱") == device_name
                and (delete_all or not trigger_time
                     or _norm_trigger(rec.get("觸發時間")) == trigger_time))
    records[:] = [rec for rec in records if not _cache_match(rec)]

    # 只在刪到使用者 AC 排程時重算（避免使用者剛刪掉 auto 又被立刻加回來的困擾）
    if any_user_ac_deleted:
        maintain_ac_auto_schedule(device_name, ctx, transitioned_to_on=False)
    return f"✅ 已取消 {len(matches)} 筆排程"


def handle_query_schedule(ctx):
    schedules = [r for r in ctx.get("排程指令") if r.get("狀態") == "待執行"]
    if not schedules:
        return "目前沒有排程"
    lines = []
    for r in schedules:
        params_text = _format_schedule_params(r.get("動作", ""), r.get("參數", ""))
        lines.append(f"• {r['設備名稱']}｜{params_text}｜{r['觸發時間']}")
    return "排程列表：\n" + "\n".join(lines)
