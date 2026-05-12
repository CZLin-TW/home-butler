import gspread
import json
from datetime import timedelta
from config import now_taipei
from sheets import get_device_id_by_name, get_device_auth_by_name, get_all_devices_by_type, build_row
import switchbot_api
import panasonic_api
import weather_api
import dehumidifier_auto

# 紅外線 AC 是 write-only 的絕對命令，SwitchBot 無法回讀當前狀態。
# 為了支援「調低1度」這類相對調整，我們把每次成功送出的指令寫回「智能居家」分頁，
# 下次 Claude 組 prompt 時就能看到上一次的設定並據此推算新值。
def _parse_offset(value):
    """Parse a compensation offset from a Sheet cell value (int, float, str, or empty)."""
    if value is None or value == "":
        return 0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0


def apply_sensor_compensation(temp, humidity, device_row):
    """Apply temperature/humidity offset from the device's Sheet config.

    Sign convention: offset is ADDED to the raw value.
    e.g. 濕度補償 = -5 means 'sensor reads 5% high' → actual = raw + (-5).
    Humidity is clamped to [0, 100]. Temperature is not clamped (can be negative).
    Non-numeric raw values (e.g. "N/A") are returned unchanged.
    """
    temp_offset = _parse_offset(device_row.get("溫度補償"))
    hum_offset = _parse_offset(device_row.get("濕度補償"))
    if isinstance(temp, (int, float)) and temp_offset:
        temp = round(temp + temp_offset, 1)
    if isinstance(humidity, (int, float)) and hum_offset:
        humidity = max(0, min(100, round(humidity + hum_offset, 1)))
    return temp, humidity


_AC_MODE_LABEL = {1: "自動", 2: "冷氣", 3: "除濕", 4: "送風", 5: "暖氣"}
_AC_FAN_LABEL = {1: "自動", 2: "低", 3: "中", 4: "高"}
_AC_STATE_COLUMNS = ["最後電源", "最後溫度", "最後模式", "最後風速", "最後更新時間"]
_ac_columns_warning_printed = False


def _save_ac_last_state(ctx, device_id, power, temperature=None, mode_int=None, fan_int=None):
    """把最後一次 AC 指令寫回「智能居家」分頁，供下次相對調整使用。

    [AC 最後狀態 cache 總覽]
    - 為什麼需要：紅外線 AC 是 write-only，SwitchBot 無法回讀真實狀態，
      只能在自己這邊記下「上次送出的指令」當作 best-effort 狀態。
    - 寫入端：本函式（每次成功送出 AC 指令後由 handle_control_ac 呼叫）。
    - 消費端 1：prompt.py:_format_ac_last_state — 組 system prompt 給 Claude，
      讓「調低 1 度」這類相對指令能據此推算絕對溫度。
    - 消費端 2：web_api.py:api_get_devices / api_dashboard — 給 Dashboard
      在卡片上顯示「目前 26°C 冷氣」之類的提示。
    - power == "off" 時刻意保留先前的溫度/模式/風速，方便下次重新開機時沿用。

    使用 batch_update 一次更新多格，避免多次 API 呼叫。
    若欄位尚未在 sheet 上建立會自動略過，不會中斷主流程。
    """
    try:
        sheet = ctx.get_worksheet("智能居家")
        records = ctx.get("智能居家")
        row_idx = None
        for i, row in enumerate(records):
            if row.get("Device ID") == device_id:
                row_idx = i + 2  # 第 1 列為 header，records 從第 2 列開始
                break
        if row_idx is None:
            return

        headers = sheet.row_values(1)
        header_to_col = {h: idx + 1 for idx, h in enumerate(headers)}
        if not any(col in header_to_col for col in _AC_STATE_COLUMNS):
            # sheet 還沒新增任何狀態欄位 → 不寫入但留下一次性警告，避免使用者好奇
            # 「為什麼 Dashboard 永遠顯示『尚無使用記錄』」卻沒線索可查。
            global _ac_columns_warning_printed
            if not _ac_columns_warning_printed:
                print(
                    f"[WARN] AC state columns not found in 智能居家 sheet "
                    f"({_AC_STATE_COLUMNS}). Last state will not be persisted; "
                    f"add these columns to enable Dashboard last-state display."
                )
                _ac_columns_warning_printed = True
            return

        now_str = now_taipei().strftime("%Y-%m-%d %H:%M")
        new_values = {"最後更新時間": now_str, "最後電源": power}
        if power == "on":
            if temperature is not None:
                new_values["最後溫度"] = temperature
            if mode_int is not None:
                new_values["最後模式"] = _AC_MODE_LABEL.get(mode_int, "")
            if fan_int is not None:
                new_values["最後風速"] = _AC_FAN_LABEL.get(fan_int, "")
        # power == "off" 時刻意保留先前的溫度/模式/風速，方便下次重新開機時沿用

        updates = []
        for header, value in new_values.items():
            col = header_to_col.get(header)
            if col is None:
                continue
            cell = gspread.utils.rowcol_to_a1(row_idx, col)
            updates.append({"range": cell, "values": [[value]]})

        if not updates:
            return
        sheet.batch_update(updates)

        # 同步更新 ctx 快取，讓同一 request 後續（例如排程或連續 action）能讀到新值
        rec = records[row_idx - 2]
        for header, value in new_values.items():
            if header in header_to_col:
                rec[header] = value
    except Exception as e:
        print(f"[AC STATE SAVE ERROR] device={device_id}: {e}")


def _parse_int_safe(value):
    """Parse an int from a Sheet cell value; returns 0 if invalid/empty."""
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return 0


def _is_ac_off_action(schedule_row):
    """判斷某筆排程動作是否為『把 AC 關閉』。"""
    if schedule_row.get("動作") != "control_ac":
        return False
    try:
        params = json.loads(schedule_row.get("參數", "{}"))
    except (json.JSONDecodeError, TypeError):
        return False
    return params.get("power") == "off"


def maintain_ac_auto_schedule(device_name, ctx, transitioned_to_on=False):
    """維護某台 AC 的自動關機排程。

    核心規則：AC 開著 AND 沒有使用者 pending off 排程 → 需要 auto，否則不需要。
    timer anchor：只有從『關 → 開』的 transition 才重置計時，純調整（on → on）保留現有 auto。

    transitioned_to_on=True 時視為開機事件（清掉舊 auto、加新 auto）
    transitioned_to_on=False 時視為調整/重新評估（有舊 auto 就保留、沒舊 auto 就按需補）
    """
    try:
        devices = ctx.get("智能居家")
        device_row = next(
            (d for d in devices
             if d.get("名稱") == device_name
             and d.get("類型") == "空調"
             and d.get("狀態") == "啟用"),
            None,
        )
        if not device_row:
            return

        power = str(device_row.get("最後電源", "")).strip()
        hours = _parse_int_safe(device_row.get("自動關機小時數"))

        schedule_sheet = ctx.get_worksheet("排程指令")
        archive_sheet = ctx.get_worksheet("排程封存")
        all_schedules = ctx.get("排程指令")

        # 找這台 AC 的 auto 與 user-off 排程
        existing_auto = [
            (i, r) for i, r in enumerate(all_schedules)
            if r.get("設備名稱") == device_name
            and r.get("狀態") == "待執行"
            and r.get("來源") == "自動"
        ]
        user_off = [
            r for r in all_schedules
            if r.get("設備名稱") == device_name
            and r.get("狀態") == "待執行"
            and (r.get("來源") or "使用者") == "使用者"
            and _is_ac_off_action(r)
        ]

        need_auto = (power == "on" and hours > 0 and not user_off)

        def _archive_and_delete(indices_rows):
            """封存 + 刪除（倒序，避免 row index 偏移）。"""
            if not indices_rows:
                return
            archive_headers = archive_sheet.row_values(1)
            for i, row in sorted(indices_rows, key=lambda x: x[0], reverse=True):
                archive_sheet.append_row(build_row(archive_headers, {**row, "狀態": "已取消"}))
                schedule_sheet.delete_rows(i + 2)
                all_schedules.pop(i)

        def _add_auto():
            """產生一筆自動 off 排程。"""
            trigger = (now_taipei() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
            now_str = now_taipei().strftime("%Y-%m-%d %H:%M")
            headers = schedule_sheet.row_values(1)
            new_row = {
                "設備名稱": device_name,
                "動作": "control_ac",
                "參數": json.dumps({"power": "off"}, ensure_ascii=False),
                "觸發時間": trigger,
                "建立者": "系統",
                "建立時間": now_str,
                "狀態": "待執行",
                "來源": "自動",
            }
            schedule_sheet.append_row(build_row(headers, new_row))
            all_schedules.append(new_row)  # 同步 ctx 快取
            print(f"[AUTO SCHEDULE] {device_name} off @ {trigger}")

        if need_auto:
            if transitioned_to_on:
                # 開機事件：清舊、加新（timer 重置）
                _archive_and_delete(existing_auto)
                _add_auto()
            elif not existing_auto:
                # 調整時沒有舊 auto（可能剛才 user off 排程被刪了）→ 補一筆
                _add_auto()
            # else: 調整時已有舊 auto → 保留不動（timer 不重置）
        else:
            # 不需要 auto（AC 關了、或已有 user off、或功能停用）→ 清掉現有
            _archive_and_delete(existing_auto)
    except Exception as e:
        print(f"[MAINTAIN AUTO SCHEDULE ERROR] device={device_name}: {e}")


def handle_control_ac(data, ctx, from_auto_schedule=False):
    device_name = data.get("device_name", "")
    device_id = get_device_id_by_name(device_name, ctx)

    if not device_id:
        ac_devices = get_all_devices_by_type("空調", ctx)
        if len(ac_devices) == 1:
            device_id = ac_devices[0].get("Device ID", "")
            device_name = ac_devices[0].get("名稱", device_name)
        elif len(ac_devices) > 1:
            names = "、".join([d.get("名稱") for d in ac_devices])
            return f"❌ 有多台空調（{names}），請指定要控制哪一台"
        else:
            return "❌ 找不到空調設備，請先在「智能居家」分頁設定"

    # 記錄命令前的電源狀態，用於判斷是否為「關→開」transition（影響 auto-schedule timer 是否重置）
    prior_power_on = False
    for r in ctx.get("智能居家"):
        if r.get("Device ID") == device_id and r.get("狀態") == "啟用":
            prior_power_on = str(r.get("最後電源", "")).strip() == "on"
            break

    power = data.get("power", "on")
    temperature = None
    mode = None
    fan = None
    if power == "off":
        result = switchbot_api.ac_turn_off(device_id)
    else:
        mode_str = data.get("mode", "cool")
        temperature = int(data.get("temperature", 24 if mode_str == "heat" else 27))
        fan_str = data.get("fan_speed", "auto")
        mode = switchbot_api.AC_MODE_MAP.get(mode_str, 2)
        fan = switchbot_api.AC_FAN_MAP.get(fan_str, 1)
        result = switchbot_api.ac_set_all(device_id, temperature, mode, fan, "on")

    if result.get("success"):
        _save_ac_last_state(ctx, device_id, power, temperature, mode, fan)
        # 自動排程 safety net：非自動排程觸發時才重算（避免 auto 觸發 → auto 再生 auto 的無限循環）
        if not from_auto_schedule:
            new_power_on = (power == "on")
            transitioned = new_power_on and not prior_power_on
            maintain_ac_auto_schedule(device_name, ctx, transitioned_to_on=transitioned)
        return f"✅ {device_name} 指令已送出"
    else:
        return f"❌ {device_name} 控制失敗：{result.get('error', '未知錯誤')}"


def handle_control_ir(data, ctx):
    device_name = data.get("device_name", "")
    button = data.get("button", "")
    device_id = get_device_id_by_name(device_name, ctx)

    if not device_id:
        ir_devices = get_all_devices_by_type("IR", ctx)
        if len(ir_devices) == 1:
            device_id = ir_devices[0].get("Device ID", "")
            device_name = ir_devices[0].get("名稱", device_name)
        else:
            return f"❌ 找不到「{device_name}」，請確認設備名稱"

    if not button:
        return "❌ 請指定要按哪個按鈕"

    result = switchbot_api.ir_control(device_id, button)
    if result.get("success"):
        return f"✅ {device_name}「{button}」指令已送出"
    else:
        return f"❌ {device_name} 控制失敗：{result.get('error', '未知錯誤')}"


def handle_query_sensor(data, ctx):
    device_name = data.get("device_name", "")
    device_id = get_device_id_by_name(device_name, ctx)

    if not device_id:
        sensor_devices = get_all_devices_by_type("感應器", ctx)
        if len(sensor_devices) == 1:
            device_id = sensor_devices[0].get("Device ID", "")
            device_name = sensor_devices[0].get("名稱", device_name)
        elif len(sensor_devices) > 1:
            names = "、".join([d.get("名稱") for d in sensor_devices])
            return f"❌ 有多個感應器（{names}），請指定要查詢哪一個"
        else:
            return "❌ 找不到感應器設備，請先在「智能居家」分頁設定"

    result = switchbot_api.get_hub_sensor(device_id)
    if "error" in result:
        return f"❌ 讀取 {device_name} 失敗：{result['error']}"

    temp = result.get("temperature", "N/A")
    humidity = result.get("humidity", "N/A")
    device_row = next((r for r in ctx.get("智能居家") if r.get("Device ID") == device_id and r.get("狀態") == "啟用"), {})
    temp, humidity = apply_sensor_compensation(temp, humidity, device_row)
    return f"🌡️ {device_name}:溫度 {temp}°C，濕度 {humidity}%"


def handle_query_devices(ctx):
    valid = [r for r in ctx.get("智能居家") if r.get("狀態") == "啟用"]
    if not valid:
        return "目前沒有已設定的智能居家設備"
    def _device_line(r):
        control = r.get("控制類型", "")
        control_part = f"，{control}" if control else ""
        return f"• {r['名稱']}（{r['類型']}，{r.get('位置', '未設定')}{control_part}）"
    lines = [_device_line(r) for r in valid]
    return "已設定的設備：\n" + "\n".join(lines)


def handle_control_dehumidifier(data, ctx, _internal=False):
    """除濕機手動控制。_internal=True 是自動模式規則自己呼叫，跳過 lock 檢查。"""
    device_name = data.get("device_name", "")
    auth, gwid = get_device_auth_by_name(device_name, ctx)

    if not auth:
        dh_devices = get_all_devices_by_type("除濕機", ctx)
        if len(dh_devices) == 1:
            auth = dh_devices[0].get("Auth", "")
            gwid = dh_devices[0].get("Device ID", "")
            device_name = dh_devices[0].get("名稱", device_name)
        elif len(dh_devices) > 1:
            names = "、".join([d.get("名稱") for d in dh_devices])
            return f"❌ 有多台除濕機（{names}），請指定要控制哪一台"
        else:
            return "❌ 找不到除濕機設備，請先在「智能居家」分頁設定"

    # 自動模式啟用中拒收外部控制（Dashboard 手動 / LINE bot / 排程都會走這條）
    if not _internal and dehumidifier_auto.is_locked(device_name):
        return f"❌ {device_name} 目前處於自動模式，請先在 Dashboard 關閉自動模式才能手動控制"

    power = data.get("power", "")
    mode = data.get("mode", "")
    humidity = data.get("humidity", "")

    if power == "off":
        result = panasonic_api.dehumidifier_turn_off(auth, gwid)
    elif power == "on" and not mode and not humidity:
        result = panasonic_api.dehumidifier_turn_on(auth, gwid)
    else:
        turn_on_result = panasonic_api.dehumidifier_turn_on(auth, gwid)
        if not turn_on_result.get("success"):
            return f"❌ {device_name} 開機失敗：{turn_on_result.get('error', '未知錯誤')}"
        result = turn_on_result
        if mode:
            result = panasonic_api.dehumidifier_set_mode(auth, gwid, mode)
            if not result.get("success"):
                return f"❌ {device_name} 模式設定失敗：{result.get('error')}"
        if humidity:
            result = panasonic_api.dehumidifier_set_humidity(auth, gwid, int(humidity))

    if result.get("success"):
        return f"✅ {device_name} 指令已送出"
    else:
        return f"❌ {device_name} 控制失敗：{result.get('error', '未知錯誤')}"


def handle_query_dehumidifier(data, ctx):
    device_name = data.get("device_name", "")
    auth, gwid = get_device_auth_by_name(device_name, ctx)

    if not auth:
        dh_devices = get_all_devices_by_type("除濕機", ctx)
        if len(dh_devices) == 1:
            auth = dh_devices[0].get("Auth", "")
            gwid = dh_devices[0].get("Device ID", "")
            device_name = dh_devices[0].get("名稱", device_name)
        else:
            return "❌ 找不到除濕機設備"

    status = panasonic_api.get_dehumidifier_status(auth, gwid)
    return panasonic_api.format_dehumidifier_status(status, device_name)


def handle_query_weather(data):
    date_str = data.get("date", "today")
    location = data.get("location", None)
    summary = weather_api.get_weather_summary(date_str, location)
    print(f"[WEATHER] date={date_str}, location={location}, summary={summary}")
    return weather_api.format_weather(summary)
