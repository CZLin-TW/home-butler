import gspread
from config import now_taipei
from sheets import get_device_id_by_name, get_device_auth_by_name, get_all_devices_by_type
import switchbot_api
import panasonic_api
import weather_api

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


def handle_control_ac(data, ctx):
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


def handle_control_dehumidifier(data, ctx):
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
