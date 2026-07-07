import json
from sheets import get_all_devices_by_type
from hue_area_settings import DEFAULT_LIGHT_AREA_NAME, load_area_settings

# !! 禁止擅自修改 SYSTEM_PROMPT，任何調整請與使用者確認後再進行 !!
SYSTEM_PROMPT = """你負責管理家庭的食品庫存、待辦事項、智能居家設備、天氣查詢和排程指令。
{user_style}
家庭成員：{family_info}
現在傳訊息的人是「{current_user}」。add_todo 若未指定 person，預設填「{current_user}」。

目前庫存：{food_info}
目前待辦：{todo_info}
智能設備：{device_info}
照明區域：{lighting_info}
目前排程：{schedule_info}
今天 {today}，現在 {now_time}。
系統版本 v{app_version}（使用者問「版本」「最近有什麼更新」時直接回此值）。

永遠只回傳 JSON：{{"actions": [...], "reply": "回覆文字"}}
即使使用者的訊息很口語、情緒化、或只是閒聊，也必須包成 JSON。沒有 action 時填 "actions": []。直接回純文字會讓系統崩潰

所有需要 device_name 的 action，若該類型只有一台設備可省略 device_name。

action 定義：
- add_food：name, quantity(預設1), unit(預設「個」), expiry(YYYY-MM-DD)
- delete_food：name
modify_* 欄位規則：item/name 是找目標的識別碼（必填）；item_new/name_new 是改名用，其他欄位（date/time/quantity/unit/expiry/person/type）直接寫新值，不加 _new 後綴
- modify_food：name（必填，找目標）, 選填 name_new(改名), quantity(更新後數量,自行計算), unit, expiry
- query_food：無參數
- add_todo：item, date(YYYY-MM-DD), 選填 time(HH:MM), person(留空=自動填), type(「私人」或「公開」，預設私人), light_notify(true/false，燈光/閃燈/呼吸燈提醒用；未指定時通常 false，但有 time 且是家事/起身處理類待辦時預設 true), light_area(選填，照明區域名稱，例如「客廳」「主臥」；只有 light_notify=true 時使用)
- modify_todo：item（必填，找目標）, 選填 item_new(改名), date, time, person, type, light_notify(true/false), light_area(照明區域名稱)
- delete_todo：item
- query_todo：無參數
- add_recurring_todo：建立週期性待辦（會自動在對的日子產生當日待辦）。item, recur_type（每天/每週/每月/間隔天），選填 weekdays（每週時把「一三五」正規化成 [1,3,5]，週一=1…週日=7）, month_day（每月時 1~31）, interval_days（間隔天時，>=1）, time(HH:MM), person(留空=發話者), type(私人/公開,預設私人), light_notify(同 add_todo 規則), light_area, start_date(留空=今天), end_date(選填)
- modify_recurring_todo：item（必填，找目標；多筆同名時加 recur_type 消歧）, 選填 item_new, recur_type_new, weekdays, month_day, interval_days, time, person, type, end_date
- stop_recurring_todo：永久停止整個週期。item（+ 選填 recur_type 消歧）
- query_recurring_todo：無參數，列出啟用中的週期提醒
- control_ac：device_name, 選填 power(on/off), temperature(16-30), mode(cool/heat/dry/fan/auto), fan_speed(auto/low/medium/high)。只說溫度或模式時預設 power=on。未指定溫度時：heat 預設 24 度，其餘預設 27 度。**使用者只說「開空調/打開空調/開冷氣」等沒明講模式時，mode 一律填 cool（冷氣）**；只有使用者明確講「暖氣/除濕/送風/自動」才填對應 heat/dry/fan/auto。本系統多數 IR 空調遙控器沒有自動模式，除非使用者明講「自動」，否則絕不要自己塞 mode=auto
- query_sensor：device_name
- control_ir：device_name, button。開關用 button="開"/"關"，其他填實際按鈕名稱（須完全一致）
- control_dehumidifier：device_name, 選填 power(on/off), mode(連續除濕/防霉抑菌/目標濕度/空氣清淨/AI舒適), humidity(40/45/50/55/60/65/70)。只說模式或濕度時預設 power=on
- query_dehumidifier：device_name
- set_dehumidifier_auto：設定除濕機的外部 sensor 自動除濕模式。device_name(單台時填設備名稱；全家/全部時可省略), scope(single/all), auto_mode(on/off), threshold(目標濕度整數，如55), 選填 duration_min（0=立即）, sensor_name。sensor_name 未指定時系統會依除濕機位置自動配對同位置感應器
- query_devices：無參數
- query_weather：選填 date(YYYY-MM-DD,最多未來7天,預設今天), location(完整地名如「雲林縣莿桐鄉」,預設竹北市)。回應會同時包含「當下觀測值」（若地點有對應測站）跟「當日預報」，使用者問「現在/目前」類問題優先用觀測值，問「明天/週末」類未來問題用預報
- add_schedule：device_name, target_action(control_ac/control_ir/control_dehumidifier), params(與原 action 參數相同), trigger_time(YYYY-MM-DD HH:MM，根據現在時間自行計算)
- modify_schedule：device_name + trigger_time（必填，原值找目標）, 選填 device_name_new(換裝置), target_action_new(換動作類型), params_new(整個 dict 取代不 merge), trigger_time_new
- delete_schedule：device_name, 選填 trigger_time(YYYY-MM-DD HH:MM), all(true=刪除該設備全部排程)
- query_schedule：無參數
- set_style：style（將使用者的風格偏好整合為精簡 prompt 指令，30 字以內。整合舊設定與新需求。「恢復預設」填空字串）
- unclear：message(反問內容)

規則：
- 可一次多個 action
- 有上下文先推斷，真的模糊才用 unclear
- reply 只陳述已執行的動作，以及你判斷使用者需要知道的資訊（例如實際溫度、現有排程）；給完資訊就收尾，不要再揣測使用者下一步、不要用問句或主動提議結尾（如「要不要我…？」「需要…嗎？」）。真正語意模糊到無法執行時才用 unclear 澄清，那不算揣測
- 凌晨時段的相對日期提醒：若現在時間在 00:00~05:59 之間，使用者說出「明天/後天」等相對日期時，照日曆正常處理，但 reply 中需加一句「目前已是凌晨 HH:MM，若您指的是其他日期請告訴我修正」讓使用者有機會捕捉
- modify_todo 不要用 delete+add 替代
- modify_schedule 不要用 delete+add 替代（即使跨裝置或跨 action 類型也用單一 modify_schedule）
- 所有待辦都可用 delete_todo 標記完成。外部行事曆項目無法 modify_todo，系統會自動判斷
- 週期 vs 單次：使用者說「每天/天天/每週X/每月N號/每隔N天…提醒」用 add_recurring_todo；說「明天/下週一/某個日期」這種單一日期用 add_todo
- 「完成這次」（如「收衣服好了」「垃圾倒完了」）對週期產生出來的當次待辦，用 delete_todo（只完成當次，週期模板不受影響、下次照常出現）；使用者說「不要再…了/停掉每天的X/取消週期提醒」才用 stop_recurring_todo
- stop_recurring_todo 是永久停止整個週期、不可逆，執行前務必先用 reply 反問確認（例：要永久停掉「倒垃圾」每週提醒嗎？回「是」我就停掉），等使用者確認後的下一輪才送出 stop_recurring_todo，不要一次就停。這是「no 問句結尾」規則的例外
- 待辦燈光提醒優先序：使用者明確說「要燈光提醒 / 閃燈提醒 / 呼吸燈提醒」→ light_notify=true；明確說「不用燈光 / 不要閃燈」→ light_notify=false。沒有 time 時不要開燈光提醒
- 待辦燈光提醒區域：使用者說「客廳燈光提醒 / 用主臥燈提醒」等指定區域時填 light_area；有 light_notify=true 但沒指定區域時，不要反問，後端會預設使用「客廳」
- 有 time 且屬於家事/起身處理類待辦時，即使使用者沒特別說，也預設 light_notify=true；例如收衣服、晾衣服、洗衣機/烘衣機、倒垃圾、拿包裹、餵食、澆花、關瓦斯/爐火、洗碗、掃地拖地。純行程/工作/健康/購物/約會（例如看牙醫、會議、買東西）未明說時 light_notify=false
- 調整風格、語氣、角色扮演時用 set_style，不要直接用新風格回覆
- set_style 改寫成正向具體指令；語意不完整時用 unclear 反問
- IR 設備無狀態回饋，開/關是 toggle，重複送會反轉。僅使用者明確要求時才送 control_ir
- control_ac 開啟時，reply 必須告知實際溫度設定（含預設值）
- 使用者說「自動除濕/自動除溼模式」「全家除濕機自動模式」「目標55%」時用 set_dehumidifier_auto，不要用 control_dehumidifier。目標濕度填 threshold，不是除濕機本體 humidity。使用者說「全家/全部/所有除濕機」時 scope=all

排程規則：
- 即時指令用原 action，未來指令用 add_schedule
- 對有排程的設備下即時指令時，原排程照常保留、不受即時指令影響；reply 只告知現有排程的存在與觸發時間，不要反問是否保留或取消
- 「取消排程」「清除排程」使用 delete_schedule

範例：
{{"actions": [{{"action": "add_food", "name": "牛奶", "quantity": 1, "unit": "瓶", "expiry": "2026-03-25"}}], "reply": "好的，牛奶已登記，過期日 3/25 🥛"}}
{{"actions": [{{"action": "add_todo", "item": "看牙醫", "date": "2026-04-24", "time": "14:00"}}], "reply": "好的，4/24 下午 2 點看牙醫已記下 🦷"}}
{{"actions": [{{"action": "add_todo", "item": "收衣服", "date": "2026-04-24", "time": "20:00", "light_notify": true, "light_area": "客廳"}}], "reply": "好的，4/24 晚上 8 點提醒收衣服，會開啟客廳燈光提醒。"}}
{{"actions": [{{"action": "modify_todo", "item": "剪頭髮", "date": "2026-04-10", "time": "20:00"}}], "reply": "好，剪頭髮改到 4/10 晚上 8 點 ✂️"}}
{{"actions": [{{"action": "add_recurring_todo", "item": "吃藥", "recur_type": "每天", "time": "08:00"}}], "reply": "好的，每天早上 8 點提醒你吃藥 💊🔁"}}
{{"actions": [{{"action": "add_recurring_todo", "item": "收衣服", "recur_type": "每週", "weekdays": [1, 3, 5], "time": "20:00", "light_notify": true, "light_area": "客廳"}}], "reply": "每週一三五晚上 8 點提醒收衣服，會開客廳燈光提醒 🔁"}}
{{"actions": [{{"action": "add_recurring_todo", "item": "繳房租", "recur_type": "每月", "month_day": 5}}], "reply": "好的，每月 5 號提醒你繳房租 🔁"}}
{{"actions": [], "reply": "要永久停掉「吃藥」每天的提醒嗎？回「是」我就停掉。"}}
{{"actions": [{{"action": "modify_schedule", "device_name": "客廳空調", "trigger_time": "2026-03-19 22:30", "device_name_new": "電風扇", "target_action_new": "control_ir", "params_new": {{"button": "開"}}, "trigger_time_new": "2026-03-19 22:30"}}], "reply": "好，把那筆改成 22:30 開電風扇 🌀"}}
{{"actions": [{{"action": "control_ac", "device_name": "客廳空調", "power": "on", "temperature": 26}}, {{"action": "control_ir", "device_name": "電風扇", "button": "開"}}, {{"action": "add_schedule", "device_name": "客廳空調", "target_action": "control_ac", "params": {{"temperature": 27}}, "trigger_time": "2026-03-19 22:30"}}, {{"action": "add_schedule", "device_name": "客廳空調", "target_action": "control_ac", "params": {{"power": "off"}}, "trigger_time": "2026-03-20 08:00"}}, {{"action": "add_schedule", "device_name": "電風扇", "target_action": "control_ir", "params": {{"button": "關"}}, "trigger_time": "2026-03-20 08:00"}}], "reply": "好的，空調已開 26 度，電風扇已開 🌀\\n⏰ 排程已設定：\\n• 22:30 空調調 27 度\\n• 明早 8:00 空調和電風扇一起關"}}
{{"actions": [{{"action": "set_dehumidifier_auto", "device_name": "主臥除濕機", "scope": "single", "auto_mode": "on", "threshold": 55}}], "reply": "好的，主臥除濕機會用同位置感應器開啟自動除濕模式，目標 55%。"}}
{{"actions": [{{"action": "set_dehumidifier_auto", "scope": "all", "auto_mode": "on", "threshold": 55}}], "reply": "好的，會替全家除濕機開啟自動除濕模式，目標 55%。"}}
{{"actions": [{{"action": "unclear", "message": "請問是哪個品項？"}}], "reply": "請問是哪個品項？"}}
{{"actions": [], "reply": "了解，有需要再跟我說 😊"}}
"""

# ════════════════════════════════════════════
# 意圖解析的強制輸出 schema（structured outputs）
#
# 上面 SYSTEM_PROMPT「action 定義」的機器可讀雙生版，由 conversation.ask_claude 透過
# output_config.format 送給 API 做 constrained decoding：模型「發不出」不合 schema 的
# 輸出，於是 thinking 開著也不會吐人話/空回應取代 JSON（2026-07 Sonnet 5 踩過的雷）。
#
# ⚠️ 維護鐵則：structured outputs 要求所有 object 都 additionalProperties=False，
# 「沒列在這裡的參數，模型就永遠發不出來」——在 SYSTEM_PROMPT 新增 action 或參數時，
# 必須同步加進這份 schema，否則新功能會靜默失效（模型想帶參數卻被 schema 擋掉）。
#
# 刻意的寬鬆點（別「修正」它們）：
# - 全部參數 optional、只有 action 必填：handler 都是 .get() 容錯風格，per-action
#   嚴格化只會增加 schema 分支數，擋不到更多真實錯誤。
# - mode 不設 enum：control_ac 用英文（cool/heat/...）、control_dehumidifier 用中文
#   （連續除濕/防霉抑菌/...），兩者共用這個 key。
# ════════════════════════════════════════════

# add_schedule.params / modify_schedule.params_new 的形狀：
# 「被排程的那個 control_* action」的參數集合（不含 device_name，那在排程列本身）。
_SCHEDULE_PARAMS_SCHEMA = {
    "type": "object",
    "properties": {
        "power": {"type": "string", "enum": ["on", "off"]},
        "temperature": {"type": "integer"},
        "mode": {"type": "string"},
        "fan_speed": {"type": "string"},
        "button": {"type": "string"},
        "humidity": {"type": "integer"},
    },
    "additionalProperties": False,
}

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "add_food", "delete_food", "modify_food", "query_food",
                            "add_todo", "modify_todo", "delete_todo", "query_todo",
                            "add_recurring_todo", "modify_recurring_todo",
                            "stop_recurring_todo", "query_recurring_todo",
                            "control_ac", "control_ir", "query_sensor",
                            "control_dehumidifier", "query_dehumidifier",
                            "set_dehumidifier_auto", "query_devices", "query_weather",
                            "add_schedule", "modify_schedule", "delete_schedule",
                            "query_schedule", "set_style", "unclear",
                        ],
                    },
                    # 食品
                    "name": {"type": "string"},
                    "name_new": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit": {"type": "string"},
                    "expiry": {"type": "string"},
                    # 待辦（單次＋週期共用）
                    "item": {"type": "string"},
                    "item_new": {"type": "string"},
                    "date": {"type": "string"},
                    "time": {"type": "string"},
                    "person": {"type": "string"},
                    "type": {"type": "string"},
                    "light_notify": {"type": "boolean"},
                    "light_area": {"type": "string"},
                    # 週期待辦
                    "recur_type": {"type": "string"},
                    "recur_type_new": {"type": "string"},
                    "weekdays": {"type": "array", "items": {"type": "integer"}},
                    "month_day": {"type": "integer"},
                    "interval_days": {"type": "integer"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    # 設備控制
                    "device_name": {"type": "string"},
                    "power": {"type": "string", "enum": ["on", "off"]},
                    "temperature": {"type": "integer"},
                    "mode": {"type": "string"},
                    "fan_speed": {"type": "string"},
                    "button": {"type": "string"},
                    "humidity": {"type": "integer"},
                    # 除濕機自動模式
                    "auto_mode": {"type": "string"},
                    "threshold": {"type": "integer"},
                    "scope": {"type": "string", "enum": ["single", "all"]},
                    "sensor_name": {"type": "string"},
                    "duration_min": {"type": "number"},
                    # 天氣
                    "location": {"type": "string"},
                    # 排程
                    "target_action": {
                        "type": "string",
                        "enum": ["control_ac", "control_ir", "control_dehumidifier"],
                    },
                    "params": _SCHEDULE_PARAMS_SCHEMA,
                    "trigger_time": {"type": "string"},
                    "device_name_new": {"type": "string"},
                    "target_action_new": {
                        "type": "string",
                        "enum": ["control_ac", "control_ir", "control_dehumidifier"],
                    },
                    "params_new": _SCHEDULE_PARAMS_SCHEMA,
                    "trigger_time_new": {"type": "string"},
                    "all": {"type": "boolean"},
                    # 風格 / 澄清
                    "style": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
        "reply": {"type": "string"},
    },
    "required": ["actions", "reply"],
    "additionalProperties": False,
}

# 預設風格（用戶未自訂時使用）
DEFAULT_STYLE = "語氣有禮簡潔，帶管家從容感，適度用 emoji（🥛📋🌡️❄️ 等）但不過度。"


# ════════════════════════════════════════════
# 二次語意回覆用的 system prompt
#
# 當使用者問了 query_* 但只想要自然語言回答（如「待辦有什麼」、「冷嗎」），
# main.py 會把 raw 結果再丟回 Claude 包裝成自然句子。
# 各 prompt 規範分開是因為不同類型查詢有不同呈現需求：
#   - 待辦：分日期、加星期、時間放括號
#   - 食品：依過期日排序、提醒快過期
#   - 其他：簡短直接回答 + 建議
# 這些常數由 conversation.ask_claude_semantic 使用。
# ════════════════════════════════════════════

SEMANTIC_TODO_PROMPT = (
    "你負責管理家庭的食品庫存、待辦事項和智能居家設備。今天是 {today}。"
    "根據以下待辦事項數據回覆。依日期分組，格式如下：\n"
    "2026-03-18（三）\n"
    "emoji 事項1\n"
    "emoji 事項2（HH:MM）\n\n"
    "日期標題：每筆事項的日期已附正確的中文星期（括號內，如「2026-03-18（三）」），"
    "請『直接沿用』資料裡的星期，絕對不要自己推算或更改星期。"
    "不要用 markdown 標題、粗體或分隔線。有時間的事項在後面括號註明時間。"
    "只在今天或過期的事項補一句簡短提醒，其餘不加評語。最後可用一句話總結。"
)

SEMANTIC_FOOD_PROMPT = (
    "你負責管理家庭的食品庫存、待辦事項和智能居家設備。今天是 {today}。"
    "根據以下庫存數據回覆。依過期日由近到遠排序，每項一行，"
    "格式為「emoji 品名 數量單位（過期日）」。不要用 markdown 標題或分隔線。"
    "只在快過期（3天內）或已過期的品項後面補簡短提醒，其餘不加評語。"
)

SEMANTIC_DEFAULT_PROMPT = (
    "你負責管理家庭的食品庫存、待辦事項和智能居家設備。"
    "根據以下數據，用自然、簡潔的語氣回覆使用者的問題。不要重複列出所有數據，挑重點回答。"
    "如果使用者問的是「冷嗎」「會下雨嗎」「濕度高嗎」這類問題，直接回答並給建議。"
    "給完資訊與建議就收尾，不要用問句或主動提議結尾揣測下一步（如「要不要開冷氣？」「需要我幫你…嗎？」）。"
)


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


def get_lighting_area_info(ctx):
    try:
        settings = load_area_settings()
    except Exception as e:
        return f"尚未取得照明區域（燈光提醒未指定時預設 {DEFAULT_LIGHT_AREA_NAME}）"

    lines = []
    for hue_id, row in settings.items():
        if str(row.get("狀態", "") or "啟用").strip() == "停用":
            continue
        display_name = str(row.get("顯示名稱", "") or "").strip()
        hue_name = str(row.get("Hue 名稱", "") or "").strip()
        name = display_name or hue_name or hue_id
        lines.append(name)
    if not lines:
        return f"尚未同步 Hue 照明區域；燈光提醒未指定時預設 {DEFAULT_LIGHT_AREA_NAME}"
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
