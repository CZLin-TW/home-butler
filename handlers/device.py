from sheets import get_device_id_by_name, get_device_auth_by_name, get_all_devices_by_type
import switchbot_api
import panasonic_api
import weather_api


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
    return f"🌡️ {device_name}：溫度 {temp}°C，濕度 {humidity}%"


def handle_query_devices(ctx):
    valid = [r for r in ctx.get("智能居家") if r.get("狀態") == "啟用"]
    if not valid:
        return "目前沒有已設定的智能居家設備"
    lines = [f"• {r['名稱']}（{r['類型']}，{r.get('位置', '未設定')}）" for r in valid]
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
