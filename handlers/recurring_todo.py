"""週期性待辦（recurring todo）。

設計核心：**模板 / 實例分離 + 永遠掛著「下一次」**
- 「週期待辦模板」分頁只描述「規律」（每天 / 每週幾 / 每月 N 號 / 間隔 N 天）。
- 每 5 分鐘由 notify.py 的 realtime tick 呼叫 materialize_recurring_todos，確保**每條啟用
  規則在「待辦事項」分頁裡永遠有且只有一筆 active（待辦）實例**——「下一次要做的」。
- 生成出來的就是普通待辦 → 完成、燈光提醒、首頁卡、提醒全部沿用既有邏輯，零修改。
  完成 / 刪除那筆 → 該規則暫時沒有 active 實例 → 完成當下 inline（或下個 tick）補上下一筆。
  停整個週期 = 模板狀態改「停用」。

「下一次」怎麼算（_compute_next_occurrence）：
- **每天 / 每週 / 每月 = 固定日曆**：下一格 = 嚴格晚於「最後生成日期」(= 上一筆實例的發生日)
  的下一個符合日；**不 clamp 到今天**，所以漏掉沒清的過去格子會一筆一筆補上來（走 backlog，
  完成一個才前進下一個）。完成的時機不影響格子在哪。
- **間隔天 = 完成後 + N 天**：下一筆 =（完成後第一個 tick 的今天）+ N。完成越晚、下一筆越晚。
- 首次（最後生成日期空）：calendar 型取 >= max(起始, 今天) 的第一格；間隔天取 max(起始, 今天)。
- 超過「結束日期」就不再生。

冪等（抗重啟 / tick 漂移）：唯一真相是 Sheet——「有沒有 active 實例」+「最後生成日期」，
無記憶體 flag。同一條規則只要還有 active 實例就不補，天生不重生。

總開關：config.recurring_todo_enabled()（預設關）。關閉時 materialize 直接 no-op，
模板 CRUD 仍可用（可先把規則建好），對現有使用者零影響。
"""

import calendar
import uuid
from datetime import datetime, timedelta

from config import now_taipei, recurring_todo_enabled
from sheets import get_or_create_sheet, append_record, update_row_fields, ensure_columns
from hue_area_settings import DEFAULT_LIGHT_AREA_NAME
from handlers.todo_helpers import (
    LIGHT_NOTIFY_COLUMN,
    LIGHT_AREA_ID_COLUMN,
    parse_bool,
    bool_cell,
    resolve_light_notify,
    resolve_light_area,
)


TEMPLATE_SHEET = "週期待辦模板"
TODO_SHEET = "待辦事項"
TODO_ARCHIVE = "待辦封存"
RULE_ID_COLUMN = "規則ID"

RECUR_TYPES = ("每天", "每週", "每月", "每季", "半年", "每年", "間隔天")
# 每 N 個月一次的「月倍數」型：以起始日期當錨（月＋日），每 period 個月同一天（月底 clamp）。
_MONTHLY_MULTIPLE = {"每季": 3, "半年": 6, "每年": 12}
WEEKDAY_ZH = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}

TEMPLATE_HEADERS = [
    RULE_ID_COLUMN, "事項", "重複類型", "星期", "月日", "間隔天數", "時間",
    "負責人", "類型", LIGHT_NOTIFY_COLUMN, LIGHT_AREA_ID_COLUMN,
    "起始日期", "結束日期", "狀態", "最後生成日期", "建立者", "建立時間",
]


# ── 純函式（好寫單元測試） ─────────────────────────────

def _gen_rule_id():
    return uuid.uuid4().hex[:8]


def _parse_int(value, default=None):
    if value is None or value == "":
        return default
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


def _parse_date(value):
    """'YYYY-MM-DD'（或 Sheets 可能回的 'YYYY/M/D'）→ date；失敗回 None。"""
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _parse_weekdays(value):
    """接受 [1,3,5] / "1,3,5" / "1、3、5" / 單一 int → {1,3,5}（isoweekday，一=1…日=7）。"""
    if value is None or value == "":
        return set()
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = str(value).replace("、", ",").replace(" ", ",").split(",")
    out = set()
    for it in items:
        n = _parse_int(it)
        if n is not None and 1 <= n <= 7:
            out.add(n)
    return out


def _normalize_weekdays(value):
    """→ 排序後的 "1,3,5" 字串（存進 Sheet 用）。"""
    return ",".join(str(d) for d in sorted(_parse_weekdays(value)))


def _should_generate_today(rule, today):
    """純判斷：依「重複類型」判斷 today 是否該生成一筆實例。
    不檢查狀態 / 起訖日界（那是 _within_window 的事），方便單獨測試。
    """
    rtype = str(rule.get("重複類型", "")).strip()
    if rtype == "每天":
        return True
    if rtype == "每週":
        return today.isoweekday() in _parse_weekdays(rule.get("星期"))
    if rtype == "每月":
        md = _parse_int(rule.get("月日"))
        if md is None:
            return False
        # 月底 clamp：每月 31 號在 2 月 → 落在當月最後一天（28/29），不跳過。
        last_day = calendar.monthrange(today.year, today.month)[1]
        return today.day == min(md, last_day)
    if rtype in _MONTHLY_MULTIPLE:
        # 每季/半年/每年：以起始日期當錨（月＋日）。today 是發生日 ⟺ 與錨點的月數差是
        # period 的整數倍、且日子＝錨點日（月底 clamp）。缺錨點就不生。
        period = _MONTHLY_MULTIPLE[rtype]
        anchor = _parse_date(rule.get("起始日期"))
        if anchor is None or today < anchor:
            return False
        month_diff = (today.year - anchor.year) * 12 + (today.month - anchor.month)
        if month_diff < 0 or month_diff % period != 0:
            return False
        last_day = calendar.monthrange(today.year, today.month)[1]
        return today.day == min(anchor.day, last_day)
    if rtype == "間隔天":
        interval = _parse_int(rule.get("間隔天數"))
        if not interval or interval < 1:
            return False
        # 間隔天必須有「起始日期」當錨點；缺了就無法定義「每 N 天」，保守不生。
        # （若退化成 anchor=today，delta 恆 0 → 天天生成，完全失去間隔語意。）
        anchor = _parse_date(rule.get("起始日期"))
        if anchor is None:
            return False
        delta = (today - anchor).days
        return delta >= 0 and delta % interval == 0
    return False


def _within_window(rule, today):
    start = _parse_date(rule.get("起始日期"))
    end = _parse_date(rule.get("結束日期"))
    if start and today < start:
        return False
    if end and today > end:
        return False
    return True


def format_recur_summary(rule):
    """人類可讀摘要：「每週一三五 20:00」「每月5號」「每3天 08:00」。
    query / 確認 / 前端共用，避免各處重算漂移。"""
    rtype = str(rule.get("重複類型", "")).strip()
    time_str = str(rule.get("時間", "") or "").strip()
    time_part = f" {time_str}" if time_str else ""
    if rtype == "每天":
        return f"每天{time_part}"
    if rtype == "每週":
        # 用「、」分隔，避免「一三五」連在一起難判讀（一眼看不出是哪幾天）。
        names = "、".join(WEEKDAY_ZH.get(d, "") for d in sorted(_parse_weekdays(rule.get("星期"))))
        # names 空 = 這條週規則沒存到星期（多半是早期建立、欄位未捕捉）。明確標出來，
        # 別默默顯示成「每週」害使用者看不出哪幾天、也看不出是資料有問題。
        return f"每週{names}{time_part}" if names else f"每週（未設定星期）{time_part}"
    if rtype == "每月":
        md = _parse_int(rule.get("月日"))
        return f"每月{md}號{time_part}" if md else f"每月{time_part}"
    if rtype in _MONTHLY_MULTIPLE:
        anchor = _parse_date(rule.get("起始日期"))
        label = {"每季": "每季", "半年": "每半年", "每年": "每年"}[rtype]
        if anchor:
            return f"{label} {anchor.month}/{anchor.day}{time_part}"
        return f"{label}{time_part}"
    if rtype == "間隔天":
        iv = _parse_int(rule.get("間隔天數"))
        return f"每{iv}天{time_part}" if iv else f"間隔天{time_part}"
    return rtype or "週期"


def _next_calendar_date(rule, from_date, inclusive):
    """calendar 型（每天/每週/每月）：回傳 >= from_date（inclusive=True）或
    > from_date（inclusive=False）的下一個符合此規律的日期。

    用逐日掃描 + 既有的 _should_generate_today（同一套規律定義，不會漂移）。掃描上限
    366 天避免死迴圈（壞規則如每月無月日會永遠不符 → 回 None）。間隔天不走這裡。
    """
    # 掃描上限 370 天：每年型的兩次發生相隔最多 366 天（跨閏日），370 留餘裕。
    d = from_date if inclusive else from_date + timedelta(days=1)
    for _ in range(370):
        if _should_generate_today(rule, d):
            return d
        d += timedelta(days=1)
    return None


def _compute_next_occurrence(rule, today):
    """回傳這條規則「接下來該顯示」的發生日（date），或 None（超過結束日 / 算不出）。

    呼叫前提：這條規則目前沒有 active 實例（該補一筆了）。
    - calendar 型（每天/每週/每月）：嚴格晚於「最後生成日期」的下一格，不 clamp 今天
      （過去格子照補 → backlog）；首次取 >= max(起始, 今天) 的第一格。
    - 間隔天：完成後 + N（今天 ≈ 完成日，因為完成後第一個 tick 才 regen）；首次取
      max(起始, 今天)。
    """
    rtype = str(rule.get("重複類型", "")).strip()
    last = _parse_date(rule.get("最後生成日期"))
    start = _parse_date(rule.get("起始日期"))
    end = _parse_date(rule.get("結束日期"))

    if rtype == "間隔天":
        n = _parse_int(rule.get("間隔天數"))
        if not n or n < 1:
            return None
        if last is None:
            occ = max(start, today) if start else today
        else:
            occ = today + timedelta(days=n)
    elif rtype in ("每天", "每週", "每月") or rtype in _MONTHLY_MULTIPLE:
        if last is None:
            base = max(start, today) if start else today
            occ = _next_calendar_date(rule, base, inclusive=True)
        else:
            occ = _next_calendar_date(rule, last, inclusive=False)
    else:
        return None

    if occ is None:
        return None
    if end and occ > end:
        return None
    if start and occ < start:  # 雙保險：起始日之前不生
        return None
    return occ


# ── 生成引擎（掛在 notify.py 每 5 分的 realtime tick） ──

def _template_sheet():
    return get_or_create_sheet(TEMPLATE_SHEET, TEMPLATE_HEADERS)


def _materialize_one(todo_sheet, rule, date_str, ctx):
    """把一條模板生成成一筆普通待辦（日期＝算好的發生日，可能是未來或過去 backlog），
    並同步 ctx 快取（讓同一個 tick 後段的提醒 / 排程步驟看得到這筆新實例）。"""
    record = {
        "事項": rule.get("事項", ""),
        "日期": date_str,
        "時間": str(rule.get("時間", "") or "").strip(),
        "負責人": rule.get("負責人", ""),
        "狀態": "待辦",
        "類型": rule.get("類型", "私人") or "私人",
        "來源": "本地",
        "屬性": "讀寫",
        LIGHT_NOTIFY_COLUMN: bool_cell(rule.get(LIGHT_NOTIFY_COLUMN)),
        LIGHT_AREA_ID_COLUMN: str(rule.get(LIGHT_AREA_ID_COLUMN, "") or ""),
        RULE_ID_COLUMN: str(rule.get(RULE_ID_COLUMN, "") or ""),
    }
    append_record(todo_sheet, record)
    try:
        ctx.get(TODO_SHEET).append(dict(record))
    except Exception as e:
        print(f"[recur] ctx cache sync failed: {e}")


def materialize_recurring_todos(now, ctx):
    """每 5 分鐘 tick 呼叫。確保每條啟用規則在活表裡「有且只有一筆 active（待辦）實例」；
    沒有的（新規則、或剛被完成/刪除）就補上算好的下一筆（見 _compute_next_occurrence）。

    回傳本次生成筆數。整段獨立 try/except，單條壞規則不影響其它規則，也絕不把例外冒泡到
    tick 外層（否則會連帶讓提醒 / 排程整批失效）。
    """
    if not recurring_todo_enabled():
        return 0
    try:
        today = now.date()

        # 活表要有「規則ID」欄：materialize 寫入 + 下面 active 查重 + Dashboard 讀 /api/todos
        # 顯示 🔁 都需要。封存表也保留此欄（完成的本地實例搬進封存時帶著規則ID，供 Dashboard
        # 顯示）。
        todo_sheet = ctx.get_worksheet(TODO_SHEET)
        ensure_columns(todo_sheet, [RULE_ID_COLUMN])
        archive_sheet = ctx.get_worksheet(TODO_ARCHIVE)
        ensure_columns(archive_sheet, [RULE_ID_COLUMN])

        template_sheet = _template_sheet()
        rules = template_sheet.get_all_records()

        # 「已經有 active（待辦）實例」的規則ID——有就不補（永遠只掛一筆）。完成/刪除會把
        # 實例移出活表 → 這裡看不到 → 補下一筆。這就是唯一的冪等依據（無記憶體 flag）。
        active_rids = set()
        for r in ctx.get(TODO_SHEET):
            if str(r.get("狀態", "")).strip() != "待辦":
                continue
            rid = str(r.get(RULE_ID_COLUMN, "") or "").strip()
            if rid:
                active_rids.add(rid)

        generated = 0
        for idx, rule in enumerate(rules):
            try:
                if str(rule.get("狀態", "")).strip() != "啟用":
                    continue
                rid = str(rule.get(RULE_ID_COLUMN, "") or "").strip()
                if not rid:
                    continue
                if rid in active_rids:
                    continue  # 已有一筆掛著，不補
                occ = _compute_next_occurrence(rule, today)
                if occ is None:
                    continue  # 超過結束日 / 算不出
                occ_str = occ.isoformat()
                _materialize_one(todo_sheet, rule, occ_str, ctx)
                active_rids.add(rid)  # 同 tick 內不再補這條
                generated += 1
                try:
                    update_row_fields(template_sheet, idx + 2, {"最後生成日期": occ_str})
                except Exception as e:
                    print(f"[recur] update 最後生成日期 failed ({rid}): {e}")
            except Exception as e:
                print(f"[recur] rule {rule.get(RULE_ID_COLUMN)} materialize error: {e}")

        if generated:
            print(f"[recur] materialized {generated} todo(s)")
        return generated
    except Exception as e:
        print(f"[recur] materialize_recurring_todos fatal: {e}")
        return 0


# ── CRUD handler（LINE action + Dashboard REST 共用） ────

def _build_light_data(item, time_str, data):
    """組給 resolve_light_* 用的 dict。只在 caller 真的有給 light_notify/area 時才放 key，
    否則 resolve_light_notify 會誤判成『明確指定 false』而吃不到家事關鍵字自動判斷。"""
    ld = {"item": item, "time": time_str}
    if data.get("light_notify") is not None:
        ld["light_notify"] = data.get("light_notify")
    if data.get("light_area") is not None:
        ld["light_area"] = data.get("light_area")
    if data.get("light_area_id") is not None:
        ld["light_area_id"] = data.get("light_area_id")
    return ld


def handle_add_recurring_todo(data, user_name, ctx):
    rtype = str(data.get("recur_type", "")).strip()
    if rtype not in RECUR_TYPES:
        return f"❌ 不支援的重複類型「{rtype}」（請用：{' / '.join(RECUR_TYPES)}）"
    item = str(data.get("item", "")).strip()
    if not item:
        return "❌ 請告訴我要週期提醒的事項"

    time_str = str(data.get("time", "") or "").strip()

    weekdays = ""
    month_day = ""
    interval_days = ""
    if rtype == "每週":
        weekdays = _normalize_weekdays(data.get("weekdays"))
        if not weekdays:
            return "❌ 每週重複請指定星期幾（例如週一、三、五）"
    elif rtype == "每月":
        md = _parse_int(data.get("month_day"))
        if md is None or not (1 <= md <= 31):
            return "❌ 每月重複請指定月日（1~31）"
        month_day = md
    elif rtype == "間隔天":
        iv = _parse_int(data.get("interval_days"))
        if iv is None or iv < 1:
            return "❌ 間隔天數請填 1 以上的整數"
        interval_days = iv

    person = str(data.get("person") or user_name).strip()
    todo_type = str(data.get("type") or "私人").strip()
    start_date = str(data.get("start_date") or "").strip() or now_taipei().date().isoformat()
    end_date = str(data.get("end_date") or "").strip()

    light_data = _build_light_data(item, time_str, data)
    light_notify = resolve_light_notify(light_data) if time_str else False
    light_area = resolve_light_area(light_data, light_notify)

    rule = {
        RULE_ID_COLUMN: _gen_rule_id(),
        "事項": item,
        "重複類型": rtype,
        "星期": weekdays,
        "月日": month_day,
        "間隔天數": interval_days,
        "時間": time_str,
        "負責人": person,
        "類型": todo_type,
        LIGHT_NOTIFY_COLUMN: "TRUE" if light_notify else "FALSE",
        LIGHT_AREA_ID_COLUMN: light_area.get("id", ""),
        "起始日期": start_date,
        "結束日期": end_date,
        "狀態": "啟用",
        "最後生成日期": "",
        "建立者": user_name,
        "建立時間": now_taipei().strftime("%Y-%m-%d %H:%M"),
    }
    append_record(_template_sheet(), rule)

    # 立刻把「今天該出現的」生成出來，避免設了卻整天沒動靜（flag 關閉時 no-op）
    try:
        materialize_recurring_todos(now_taipei(), ctx)
    except Exception as e:
        print(f"[recur] inline materialize after add failed: {e}")

    area_name = light_area.get("name") or DEFAULT_LIGHT_AREA_NAME
    light_label = f"，燈光提醒：{area_name}" if light_notify else ""
    return f"✅ 已設定週期提醒：{item}（{format_recur_summary(rule)}）🔁{light_label}"


def _find_active(records, item, recur_type_filter, rule_id=None):
    """定位啟用中的模板。
    - 有 rule_id（Dashboard 走精準 ID）→ 只用 ID 比對，最準、同名也不會搞錯。
    - 否則用 事項（+ 選填 重複類型）比對（LINE 自然語言走這條）。
    """
    rid = str(rule_id or "").strip()
    if rid:
        return [
            (i, r) for i, r in enumerate(records)
            if str(r.get(RULE_ID_COLUMN, "")).strip() == rid
            and str(r.get("狀態", "")).strip() == "啟用"
        ]
    return [
        (i, r) for i, r in enumerate(records)
        if str(r.get("狀態", "")).strip() == "啟用"
        and str(r.get("事項", "")).strip() == item
        and (not recur_type_filter or str(r.get("重複類型", "")).strip() == recur_type_filter)
    ]


def list_recurring_rules(active_only=True):
    """給 REST / Dashboard 用：回傳模板 list（每筆附人類可讀『摘要』，前端不重算避免漂移）。"""
    rules = _template_sheet().get_all_records()
    if active_only:
        rules = [r for r in rules if str(r.get("狀態", "")).strip() == "啟用"]
    for r in rules:
        r["摘要"] = format_recur_summary(r)
    return rules


def handle_query_recurring_todo(ctx):
    rules = [r for r in _template_sheet().get_all_records()
             if str(r.get("狀態", "")).strip() == "啟用"]
    if not rules:
        return "目前沒有設定週期提醒"
    lines = []
    for r in rules:
        person = str(r.get("負責人", "") or "")
        person_part = f"（{person}）" if person else ""
        lines.append(f"🔁 {r.get('事項', '')}｜{format_recur_summary(r)}{person_part}")
    return "週期提醒：\n" + "\n".join(lines)


def handle_stop_recurring_todo(data, user_name, ctx):
    """停掉整個週期 = 模板狀態改「停用」（不刪，保留可再啟用）。
    已生成的當天那筆實例不主動清，使用者可自行完成或不理。"""
    del user_name
    item = str(data.get("item", "")).strip()
    rule_id = str(data.get("rule_id") or "").strip()
    if not item and not rule_id:
        return "❌ 請告訴我要停止哪個週期提醒"
    recur_type_filter = str(data.get("recur_type") or "").strip()

    sheet = _template_sheet()
    matches = _find_active(sheet.get_all_records(), item, recur_type_filter, rule_id)
    label = item or "週期提醒"
    if not matches:
        return f"❌ 找不到啟用中的週期提醒「{label}」"
    if len(matches) > 1:
        opts = "、".join(format_recur_summary(r) for _, r in matches)
        return f"❌ 有多個「{item}」週期提醒（{opts}），請說明是哪一個（例如「停掉每週的{item}」）"
    i, r = matches[0]
    name = str(r.get("事項", "") or label)
    update_row_fields(sheet, i + 2, {"狀態": "停用"})
    return f"✅ 已停止週期提醒：{name}（{format_recur_summary(r)}）"


def handle_modify_recurring_todo(data, user_name, ctx):
    del user_name
    item = str(data.get("item", "")).strip()
    rule_id = str(data.get("rule_id") or "").strip()
    if not item and not rule_id:
        return "❌ 請告訴我要修改哪個週期提醒"
    recur_type_filter = str(data.get("recur_type") or "").strip()

    sheet = _template_sheet()
    records = sheet.get_all_records()
    matches = _find_active(records, item, recur_type_filter, rule_id)
    label = item or "週期提醒"
    if not matches:
        return f"❌ 找不到啟用中的週期提醒「{label}」"
    if len(matches) > 1:
        opts = "、".join(format_recur_summary(r) for _, r in matches)
        return f"❌ 有多個「{item}」週期提醒（{opts}），請指定要改哪一個"

    i, r = matches[0]
    updates = {}

    new_type = str(data.get("recur_type_new") or "").strip()
    if new_type:
        if new_type not in RECUR_TYPES:
            return f"❌ 不支援的重複類型「{new_type}」（請用：{' / '.join(RECUR_TYPES)}）"
        updates["重複類型"] = new_type
    if data.get("item_new"):
        updates["事項"] = str(data.get("item_new")).strip()
    if data.get("weekdays") is not None:
        updates["星期"] = _normalize_weekdays(data.get("weekdays"))
    if data.get("month_day") is not None:
        md = _parse_int(data.get("month_day"))
        if md is not None:
            updates["月日"] = md
    if data.get("interval_days") is not None:
        iv = _parse_int(data.get("interval_days"))
        if iv is not None:
            updates["間隔天數"] = iv
    if data.get("time") is not None:
        updates["時間"] = str(data.get("time") or "").strip()
    if data.get("person"):
        updates["負責人"] = str(data.get("person")).strip()
    if data.get("type"):
        updates["類型"] = str(data.get("type")).strip()
    if data.get("start_date") is not None:
        updates["起始日期"] = str(data.get("start_date") or "").strip()
    if data.get("end_date") is not None:
        updates["結束日期"] = str(data.get("end_date") or "").strip()
    if "light_notify" in data and data.get("light_notify") is not None:
        ln = parse_bool(data.get("light_notify"), default=False)
        updates[LIGHT_NOTIFY_COLUMN] = "TRUE" if ln else "FALSE"
        ld = {
            "item": updates.get("事項", r.get("事項", "")),
            "time": updates.get("時間", str(r.get("時間", "") or "")),
            "light_notify": ln,
        }
        if data.get("light_area") is not None:
            ld["light_area"] = data.get("light_area")
        area = resolve_light_area(ld, ln, existing_area_id=str(r.get(LIGHT_AREA_ID_COLUMN, "") or ""))
        updates[LIGHT_AREA_ID_COLUMN] = area.get("id", "")

    if not updates:
        return f"❌ 沒收到任何要更新的欄位（{item}）"
    update_row_fields(sheet, i + 2, updates)
    merged = {**r, **updates}
    return f"✅ 已更新週期提醒：{merged.get('事項', item)}（{format_recur_summary(merged)}）🔁"
