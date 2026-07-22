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
每個 action 的形狀固定為 {{"action": "動作名", "args": [{{"key": "參數名", "value": "值"}}, ...]}}：
- 參數全部放在 args 的 key/value 陣列；value 一律是字串：數字寫 "27"、布林寫 "true"/"false"、weekdays 寫 "1,3,5"
- 沒用到的參數不要帶；無參數的 action 用 "args": []
- add_schedule / modify_schedule 沒有巢狀 params：要排程執行的指令參數（power/temperature/mode/fan_speed/button/humidity）直接平鋪進 args；modify_schedule 只要帶了任一指令參數，就視為整組取代原本的參數
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
- add_recurring_todo：建立週期性待辦（會自動在對的日子產生當日待辦）。item, recur_type（每天/每週/每月/每季/半年/每年/間隔天），選填 weekdays（每週時把「一三五」正規化成 [1,3,5]，週一=1…週日=7）, month_day（每月時 1~31）, interval_days（間隔天時，>=1）, time(HH:MM), person(留空=發話者), type(私人/公開,預設私人), light_notify(同 add_todo 規則), light_area, start_date(留空=今天), end_date(選填)。每季/半年/每年以 start_date 當錨點、每 3/6/12 個月重複同一天：使用者講「每年3月15日繳稅」就把 start_date 設成該起始日（2026-03-15）、講「每季/每半年」沒明講日期就用今天當起始日
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
- add_schedule：device_name, target_action(control_ac/control_ir/control_dehumidifier), trigger_time(YYYY-MM-DD HH:MM，根據現在時間自行計算)，加上要執行的指令參數（直接平鋪進 args，與原 action 參數相同）
- modify_schedule：device_name + trigger_time（必填，原值找目標）, 選填 device_name_new(換裝置), target_action_new(換動作類型), trigger_time_new，以及要取代的指令參數（平鋪進 args，帶了就整組取代不 merge）
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
- 週期 vs 單次：使用者說「每天/天天/每週X/每月N號/每季/每半年/每年/每隔N天…提醒」用 add_recurring_todo；說「明天/下週一/某個日期」這種單一日期用 add_todo
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
{{"actions": [{{"action": "add_food", "args": [{{"key": "name", "value": "牛奶"}}, {{"key": "quantity", "value": "1"}}, {{"key": "unit", "value": "瓶"}}, {{"key": "expiry", "value": "2026-03-25"}}]}}], "reply": "好的，牛奶已登記，過期日 3/25 🥛"}}
{{"actions": [{{"action": "add_todo", "args": [{{"key": "item", "value": "看牙醫"}}, {{"key": "date", "value": "2026-04-24"}}, {{"key": "time", "value": "14:00"}}]}}], "reply": "好的，4/24 下午 2 點看牙醫已記下 🦷"}}
{{"actions": [{{"action": "add_todo", "args": [{{"key": "item", "value": "收衣服"}}, {{"key": "date", "value": "2026-04-24"}}, {{"key": "time", "value": "20:00"}}, {{"key": "light_notify", "value": "true"}}, {{"key": "light_area", "value": "客廳"}}]}}], "reply": "好的，4/24 晚上 8 點提醒收衣服，會開啟客廳燈光提醒。"}}
{{"actions": [{{"action": "modify_todo", "args": [{{"key": "item", "value": "剪頭髮"}}, {{"key": "date", "value": "2026-04-10"}}, {{"key": "time", "value": "20:00"}}]}}], "reply": "好，剪頭髮改到 4/10 晚上 8 點 ✂️"}}
{{"actions": [{{"action": "add_recurring_todo", "args": [{{"key": "item", "value": "吃藥"}}, {{"key": "recur_type", "value": "每天"}}, {{"key": "time", "value": "08:00"}}]}}], "reply": "好的，每天早上 8 點提醒你吃藥 💊🔁"}}
{{"actions": [{{"action": "add_recurring_todo", "args": [{{"key": "item", "value": "收衣服"}}, {{"key": "recur_type", "value": "每週"}}, {{"key": "weekdays", "value": "1,3,5"}}, {{"key": "time", "value": "20:00"}}, {{"key": "light_notify", "value": "true"}}, {{"key": "light_area", "value": "客廳"}}]}}], "reply": "每週一三五晚上 8 點提醒收衣服，會開客廳燈光提醒 🔁"}}
{{"actions": [{{"action": "add_recurring_todo", "args": [{{"key": "item", "value": "繳房租"}}, {{"key": "recur_type", "value": "每月"}}, {{"key": "month_day", "value": "5"}}]}}], "reply": "好的，每月 5 號提醒你繳房租 🔁"}}
{{"actions": [], "reply": "要永久停掉「吃藥」每天的提醒嗎？回「是」我就停掉。"}}
{{"actions": [{{"action": "modify_schedule", "args": [{{"key": "device_name", "value": "客廳空調"}}, {{"key": "trigger_time", "value": "2026-03-19 22:30"}}, {{"key": "device_name_new", "value": "電風扇"}}, {{"key": "target_action_new", "value": "control_ir"}}, {{"key": "button", "value": "開"}}, {{"key": "trigger_time_new", "value": "2026-03-19 22:30"}}]}}], "reply": "好，把那筆改成 22:30 開電風扇 🌀"}}
{{"actions": [{{"action": "control_ac", "args": [{{"key": "device_name", "value": "客廳空調"}}, {{"key": "power", "value": "on"}}, {{"key": "temperature", "value": "26"}}]}}, {{"action": "control_ir", "args": [{{"key": "device_name", "value": "電風扇"}}, {{"key": "button", "value": "開"}}]}}, {{"action": "add_schedule", "args": [{{"key": "device_name", "value": "客廳空調"}}, {{"key": "target_action", "value": "control_ac"}}, {{"key": "temperature", "value": "27"}}, {{"key": "trigger_time", "value": "2026-03-19 22:30"}}]}}, {{"action": "add_schedule", "args": [{{"key": "device_name", "value": "客廳空調"}}, {{"key": "target_action", "value": "control_ac"}}, {{"key": "power", "value": "off"}}, {{"key": "trigger_time", "value": "2026-03-20 08:00"}}]}}, {{"action": "add_schedule", "args": [{{"key": "device_name", "value": "電風扇"}}, {{"key": "target_action", "value": "control_ir"}}, {{"key": "button", "value": "關"}}, {{"key": "trigger_time", "value": "2026-03-20 08:00"}}]}}], "reply": "好的，空調已開 26 度，電風扇已開 🌀\\n⏰ 排程已設定：\\n• 22:30 空調調 27 度\\n• 明早 8:00 空調和電風扇一起關"}}
{{"actions": [{{"action": "set_dehumidifier_auto", "args": [{{"key": "device_name", "value": "主臥除濕機"}}, {{"key": "scope", "value": "single"}}, {{"key": "auto_mode", "value": "on"}}, {{"key": "threshold", "value": "55"}}]}}], "reply": "好的，主臥除濕機會用同位置感應器開啟自動除濕模式，目標 55%。"}}
{{"actions": [{{"action": "set_dehumidifier_auto", "args": [{{"key": "scope", "value": "all"}}, {{"key": "auto_mode", "value": "on"}}, {{"key": "threshold", "value": "55"}}]}}], "reply": "好的，會替全家除濕機開啟自動除濕模式，目標 55%。"}}
{{"actions": [{{"action": "unclear", "args": [{{"key": "message", "value": "請問是哪個品項？"}}]}}], "reply": "請問是哪個品項？"}}
{{"actions": [], "reply": "了解，有需要再跟我說 😊"}}
"""

# ════════════════════════════════════════════
# 意圖解析的強制輸出 schema（structured outputs）
#
# SYSTEM_PROMPT「action 定義」的機器可讀雙生版，由 conversation.ask_claude 透過
# output_config.format 送給 API 做 constrained decoding：模型「發不出」不合 schema 的
# 輸出，於是 thinking 開著也不會吐人話/空回應取代 JSON（2026-07 Sonnet 5 踩過的雷）。
#
# 為什麼是 key/value 陣列、不是自然的扁平物件：structured outputs 有 grammar 編譯限制
# （全 schema optional 參數 ≤24、union 型別 ≤16），本系統 40+ 個參數的扁平 schema
# 實測直接 400（55 個 optional 被拒）。改成每個 action 帶 args: [{key, value}]，
# 所有欄位 required → 0 optional、0 union，參數再多也不撞牆。
# value 一律字串，由 assistant.py:_coerce_arg 依 ARG_KEY_TYPES 還原型別；
# add/modify_schedule 平鋪的指令參數由 _flatten_action 收回 params / params_new。
#
# ⚠️ 維護鐵則：additionalProperties=False → 沒列在這裡的東西模型永遠發不出來（且靜默
# 失效、無錯誤訊息）。新增參數＝ARG_KEY_TYPES 加一筆（key enum 自動跟著長）＋
# SYSTEM_PROMPT 同步描述；新增 action＝ACTION_NAMES ＋ assistant.ACTION_HANDLERS。
# ════════════════════════════════════════════

# 參數名 → 型別（str/int/num/bool/intlist）。schema 的 key enum 直接取自這張表，
# assistant.py 依它把字串 value 還原成 handler 期待的型別——單一事實來源，不會漂移。
ARG_KEY_TYPES = {
    # 食品
    "name": "str", "name_new": "str", "quantity": "num", "unit": "str", "expiry": "str",
    # 待辦（單次＋週期共用）
    "item": "str", "item_new": "str", "date": "str", "time": "str",
    "person": "str", "type": "str", "light_notify": "bool", "light_area": "str",
    # 週期待辦
    "recur_type": "str", "recur_type_new": "str", "weekdays": "intlist",
    "month_day": "int", "interval_days": "int", "start_date": "str", "end_date": "str",
    # 設備控制（同時是 add/modify_schedule 平鋪的指令參數）
    "device_name": "str", "power": "str", "temperature": "int", "mode": "str",
    "fan_speed": "str", "button": "str", "humidity": "int",
    # 除濕機自動模式
    "auto_mode": "str", "threshold": "int", "scope": "str",
    "sensor_name": "str", "duration_min": "num",
    # 天氣
    "location": "str",
    # 排程
    "target_action": "str", "trigger_time": "str", "device_name_new": "str",
    "target_action_new": "str", "trigger_time_new": "str", "all": "bool",
    # 風格 / 澄清
    "style": "str", "message": "str",
}

# add_schedule / modify_schedule 平鋪在 args 裡的「被排程指令」參數，
# 由 assistant.py:_flatten_action 收攏回 handler 期待的 params / params_new dict。
SCHEDULE_CMD_KEYS = ("power", "temperature", "mode", "fan_speed", "button", "humidity")

ACTION_NAMES = [
    "add_food", "delete_food", "modify_food", "query_food",
    "add_todo", "modify_todo", "delete_todo", "query_todo",
    "add_recurring_todo", "modify_recurring_todo",
    "stop_recurring_todo", "query_recurring_todo",
    "control_ac", "control_ir", "query_sensor",
    "control_dehumidifier", "query_dehumidifier",
    "set_dehumidifier_auto", "query_devices", "query_weather",
    "add_schedule", "modify_schedule", "delete_schedule",
    "query_schedule", "set_style", "unclear",
]

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ACTION_NAMES},
                    "args": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "key": {"type": "string", "enum": list(ARG_KEY_TYPES)},
                                "value": {"type": "string"},
                            },
                            "required": ["key", "value"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["action", "args"],
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
