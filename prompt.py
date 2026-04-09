import json
from sheets import get_all_devices_by_type

# !! 禁止擅自修改 SYSTEM_PROMPT，任何調整請與使用者確認後再進行 !!
SYSTEM_PROMPT = """你負責管理家庭的食品庫存、待辦事項和智能居家設備。
查詢時主動補充貼心提醒。
{user_style}
家庭成員：{family_info}
現在傳訊息的人是「{current_user}」。add_todo 若未指定 person，預設填「{current_user}」。

目前庫存：{food_info}
目前待辦：{todo_info}
智能設備：{device_info}
目前排程：{schedule_info}
今天 {today}，現在 {now_time}。

永遠只回傳 JSON：{{"actions": [...], "reply": "回覆文字"}}

所有需要 device_name 的 action，若該類型只有一台設備可省略 device_name。

action 定義：
- add_food：name, quantity(預設1), unit(預設「個」), expiry(YYYY-MM-DD)
- delete_food：name
- modify_food：name, 只填要改的欄位(name_new/quantity/unit/expiry)。quantity 為更新後數量，自行計算
- query_food：無參數
- add_todo：item, date(YYYY-MM-DD), 選填 time(HH:MM), person(留空=自動填), type(「私人」或「公開」，預設私人)
- modify_todo：item, 只填要改的欄位(item_new/date/time/person/type)
- delete_todo：item
- query_todo：無參數
- control_ac：device_name, 選填 power(on/off), temperature(16-30), mode(cool/heat/dry/fan/auto), fan_speed(auto/low/medium/high)。只說溫度或模式時預設 power=on。未指定溫度時：heat 預設 24 度，其餘預設 27 度
- query_sensor：device_name
- control_ir：device_name, button。開關用 button="開"/"關"，其他填實際按鈕名稱（須完全一致）
- control_dehumidifier：device_name, 選填 power(on/off), mode(連續除濕/自動除濕/防黴/送風/目標濕度/空氣清淨/AI舒適/省電/快速除濕/靜音除濕), humidity(40/45/50/55/60/65/70)。只說模式或濕度時預設 power=on
- query_dehumidifier：device_name
- query_devices：無參數
- query_weather：選填 date（YYYY-MM-DD，自行根據今天日期計算，不指定則查今天，最多未來 7 天）, 選填 location（完整地名如「雲林縣莿桐鄉」，不指定則查竹北市）
- add_schedule：device_name, target_action(control_ac/control_ir/control_dehumidifier), params(與原 action 參數相同), trigger_time(YYYY-MM-DD HH:MM，根據現在時間自行計算)
- delete_schedule：device_name, 選填 trigger_time(YYYY-MM-DD HH:MM), all(true=刪除該設備全部排程)
- query_schedule：無參數
- set_style：style（將使用者的風格偏好整合為精簡 prompt 指令，30 字以內。整合舊設定與新需求。「恢復預設」填空字串。語意不清時用 unclear 反問，不猜測）
- unclear：message(反問內容)

規則：
- 可一次多個 action
- 有上下文先推斷，真的模糊才用 unclear
- modify_todo 不要用 delete+add 替代
- 所有待辦都可用 delete_todo 標記完成。外部行事曆項目無法 modify_todo，系統會自動判斷
- 調整風格、語氣、角色扮演時用 set_style，不要直接用新風格回覆
- set_style 改寫成正向具體指令；語意不完整務必 unclear 反問
- IR 設備無狀態回饋，開/關是 toggle，重複送會反轉。僅使用者明確要求時才送 control_ir
- control_ac 開啟時，reply 必須告知實際溫度設定（含預設值）

排程規則：
- 即時指令用原 action，未來指令用 add_schedule
- 對有排程的設備下即時指令時，reply 提醒現有排程並詢問保留或取消
- 「取消排程」「清除排程」使用 delete_schedule

範例：
{{"actions": [{{"action": "add_food", "name": "牛奶", "quantity": 1, "unit": "瓶", "expiry": "2026-03-25"}}], "reply": "好的，牛奶已登記，過期日 3/25 🥛"}}
{{"actions": [{{"action": "add_todo", "item": "看牙醫", "date": "2026-04-24", "time": "14:00"}}], "reply": "好的，4/24 下午 2 點看牙醫已記下 🦷"}}
{{"actions": [{{"action": "control_ac", "device_name": "客廳空調", "power": "on", "temperature": 26}}, {{"action": "control_ir", "device_name": "電風扇", "button": "開"}}, {{"action": "add_schedule", "device_name": "客廳空調", "target_action": "control_ac", "params": {{"temperature": 27}}, "trigger_time": "2026-03-19 22:30"}}, {{"action": "add_schedule", "device_name": "客廳空調", "target_action": "control_ac", "params": {{"power": "off"}}, "trigger_time": "2026-03-20 08:00"}}, {{"action": "add_schedule", "device_name": "電風扇", "target_action": "control_ir", "params": {{"button": "關"}}, "trigger_time": "2026-03-20 08:00"}}], "reply": "好的，空調已開 26 度，電風扇已開 🌀\\n⏰ 排程已設定：\\n• 22:30 空調調 27 度\\n• 明早 8:00 空調和電風扇一起關"}}
{{"actions": [{{"action": "unclear", "message": "請問是哪個品項？"}}], "reply": "請問是哪個品項？"}}
{{"actions": [], "reply": "了解，有需要再跟我說 😊"}}
"""

# 預設風格（用戶未自訂時使用）
DEFAULT_STYLE = "語氣有禮簡潔，帶管家從容感，適度用 emoji（🥛📋🌡️❄️ 等）但不過度。"


def get_user_name(user_id, ctx):
    for row in ctx.get("家庭成員"):
        if row.get("Line User ID") == user_id and row.get("狀態") == "啟用":
            return row.get("名稱", user_id)
    return user_id


def get_family_members_info(ctx):
    members = []
    for row in ctx.get("家庭成員"):
        if row.get("狀態") == "啟用":
            members.append(f"{row.get('名稱')}（稱謂：{row.get('稱謂', '')}）")
    return "、".join(members)


def get_current_food(ctx):
    valid = [r for r in ctx.get("食品庫存") if r.get("狀態") == "有效"]
    if not valid:
        return "目前庫存是空的"
    lines = [f"{r['品名']} {r['數量']}{r['單位']}（過期日 {r['過期日']}）" for r in valid]
    return "、".join(lines)


def get_current_todo(ctx):
    valid = [r for r in ctx.get("待辦事項") if r.get("狀態") == "待辦"]
    if not valid:
        return "目前沒有待辦事項"
    lines = []
    for r in valid:
        time_part = f" {r['時間']}" if r.get("時間") else ""
        type_part = "（私人）" if r.get("類型") == "私人" else "（公開）"
        lines.append(f"{r['事項']}／{r['負責人']}／{r['日期']}{time_part}{type_part}")
    return "、".join(lines)


def _format_ac_last_state(r):
    """空調最後一次透過 Home Butler 送出的指令。讓 Claude 在收到「調低1度」這類
    相對指令時能據此推算絕對溫度。若從未操作過則回傳空字串。"""
    power = str(r.get("最後電源", "")).strip()
    if not power:
        return ""
    updated = str(r.get("最後更新時間", "")).strip()
    updated_part = f"（最後更新 {updated}）" if updated else ""
    if power == "off":
        return f"，目前狀態：關閉{updated_part}"
    temp = r.get("最後溫度", "")
    mode = str(r.get("最後模式", "")).strip()
    fan = str(r.get("最後風速", "")).strip()
    parts = []
    if temp != "" and temp is not None:
        parts.append(f"{temp}°C")
    if mode:
        parts.append(mode)
    if fan:
        parts.append(f"風速{fan}")
    state_text = " ".join(parts) if parts else "開啟"
    return f"，目前狀態：開機 {state_text}{updated_part}"


def get_device_info(ctx):
    valid = [r for r in ctx.get("智能居家") if r.get("狀態") == "啟用"]
    if not valid:
        return "目前沒有已設定的智能居家設備"
    lines = []
    for r in valid:
        buttons = r.get("按鈕", "")
        control = r.get("控制類型", "")
        control_part = f"，控制類型：{control}" if control else ""
        last_state = _format_ac_last_state(r) if r.get("類型") == "空調" else ""
        if buttons:
            lines.append(f"{r['名稱']}（類型：{r['類型']}，位置：{r.get('位置', '')}，按鈕：{buttons}{control_part}{last_state}）")
        else:
            lines.append(f"{r['名稱']}（類型：{r['類型']}，位置：{r.get('位置', '')}{control_part}{last_state}）")
    return "、".join(lines)



def _format_schedule_params(_action_type, params_str):
    """將排程參數 JSON 轉為人類可讀文字"""
    try:
        params = json.loads(params_str) if isinstance(params_str, str) else params_str
    except (json.JSONDecodeError, TypeError):
        return params_str

    parts = []
    if params.get("power") == "off":
        return "關機"
    if params.get("power") == "on":
        parts.append("開機")
    if "temperature" in params:
        parts.append(f"{params['temperature']}度")
    if "mode" in params:
        parts.append(f"模式:{params['mode']}")
    if "fan_speed" in params:
        parts.append(f"風速:{params['fan_speed']}")
    if "button" in params:
        return params["button"]
    if "humidity" in params:
        parts.append(f"濕度{params['humidity']}%")

    return " ".join(parts) if parts else params_str


def get_schedule_info(ctx):
    """取得排程資訊，注入 system prompt"""
    schedules = [r for r in ctx.get("排程指令") if r.get("狀態") == "待執行"]
    if not schedules:
        return "目前沒有排程"
    by_device = {}
    for r in schedules:
        name = r.get("設備名稱", "")
        if name not in by_device:
            by_device[name] = []
        params_text = _format_schedule_params(r.get("動作", ""), r.get("參數", ""))
        trigger = r.get("觸發時間", "")
        by_device[name].append(f"{params_text}（{trigger}）")
    lines = [f"{name}：{'、'.join(items)}" for name, items in by_device.items()]
    return "；".join(lines)


def get_style_instruction(user_name, ctx):
    """取得用戶風格指令。有自訂則用自訂，否則回傳預設風格。"""
    for row in ctx.get("家庭成員"):
        if row.get("名稱") == user_name and row.get("狀態") == "啟用":
            style = str(row.get("管家風格", "")).strip()
            if style:
                return f"\n使用者自訂風格：{style}。以此風格為主。"
    return f"\n{DEFAULT_STYLE}"
