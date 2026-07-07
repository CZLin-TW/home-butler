import threading
import anthropic
from config import claude, now_taipei, get_app_version, weekday_zh
from sheets import get_sheet
from prompt import (
    SYSTEM_PROMPT, DEFAULT_STYLE, ACTION_SCHEMA,
    SEMANTIC_TODO_PROMPT, SEMANTIC_FOOD_PROMPT, SEMANTIC_DEFAULT_PROMPT,
    get_family_members_info, get_current_food, get_current_todo,
    get_device_info, get_lighting_area_info, get_schedule_info, get_style_instruction,
)


def _response_text(response):
    """從 messages.create 回應取純文字回覆。

    開 adaptive thinking 後（Sonnet 5 省略 thinking 參數時的預設），content 陣列
    最前面會是 thinking block，不能再抓 content[0]；這裡挑出所有 type=='text' 的
    block 串接。thinking 關閉時行為等同原本的 content[0].text。
    """
    parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    return "".join(parts).strip()


def save_conversation(user_id, role, content):
    try:
        sheet = get_sheet("對話暫存")
        now = now_taipei().strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([user_id, role, content, now])
    except Exception as e:
        print(f"[SAVE CONV ERROR] {e}")


def cleanup_conversation(user_id, limit=6):
    def _cleanup():
        try:
            sheet = get_sheet("對話暫存")
            archive = get_sheet("對話封存")
            records = sheet.get_all_records()
            user_records = [(i, r) for i, r in enumerate(records) if r.get("Line User ID") == user_id]
            if len(user_records) <= limit:
                return
            old_records = user_records[:-limit]
            rows_to_delete = []
            for i, r in old_records:
                archive.append_row([r.get("Line User ID"), r.get("角色"), r.get("內容"), r.get("時間")])
                rows_to_delete.append(i + 2)
            for row_num in sorted(rows_to_delete, reverse=True):
                sheet.delete_rows(row_num)
            print(f"[CLEANUP] 已封存 {len(old_records)} 則對話（{user_id}）")
        except Exception as e:
            print(f"[CLEANUP ERROR] {e}")
    threading.Thread(target=_cleanup, daemon=True).start()


def get_recent_conversation(user_id, ctx, limit=6):
    records = ctx.get("對話暫存")
    user_records = [(i, r) for i, r in enumerate(records) if r.get("Line User ID") == user_id]
    recent = user_records[-limit:]
    return [{"role": r["角色"], "content": r["內容"]} for _, r in recent if r.get("內容")]


def ask_claude(user_id, user_message, user_name, ctx):
    now = now_taipei()
    today = f"{now.strftime('%Y-%m-%d')}（{weekday_zh(now)}）"
    now_time = now.strftime("%H:%M")
    style_instruction = get_style_instruction(user_name, ctx)
    prompt = SYSTEM_PROMPT.format(
        today=today, now_time=now_time,
        family_info=get_family_members_info(ctx),
        food_info=get_current_food(ctx),
        todo_info=get_current_todo(ctx),
        device_info=get_device_info(ctx),
        lighting_info=get_lighting_area_info(ctx),
        schedule_info=get_schedule_info(ctx),
        current_user=user_name,
        user_style=style_instruction,
        app_version=get_app_version(),
    )
    history = get_recent_conversation(user_id, ctx)
    messages = history + [{"role": "user", "content": user_message}]
    try:
        response = claude.messages.create(
            model="claude-sonnet-5",
            # thinking 跟回覆共用 max_tokens 預算，開 adaptive 後要留思考空間，否則
            # 複雜指令思考一長就把 JSON 擠掉（stop_reason=max_tokens、回應被截斷）。
            max_tokens=4000,
            # 意圖解析開 adaptive thinking（提升複雜指令的解析力），輸出正確性不靠模型
            # 自律、靠 output_config 的 constrained decoding：曾發生 thinking 開著時模型
            # 改吐自然語言（甚至空回應）→ JSON parse 失敗、指令不執行；強制 schema 後
            # 「吐人話取代 JSON」在 API 層就不可能發生。schema 見 prompt.ACTION_SCHEMA，
            # 新增 action/參數時必須同步維護（additionalProperties=False，漏列=發不出來）。
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": ACTION_SCHEMA}},
            system=prompt,
            messages=messages
        )
    except anthropic.BadRequestError as e:
        # 保底：schema 若被 API 的 grammar 限制拒絕（限制值可能隨版本變動，曾實測
        # optional >24 直接 400 → bot 全掛），退回「無 schema + 關思考」的已知可用
        # 組合——降級（少了強制 JSON 保證）但不斷線。看到這行 log 就要回頭修 schema。
        print(f"[ask_claude] structured outputs 被 API 拒絕，降級為無 schema 模式：{e}")
        response = claude.messages.create(
            model="claude-sonnet-5",
            max_tokens=2000,
            thinking={"type": "disabled"},
            system=prompt,
            messages=messages
        )
    text = _response_text(response)
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    print(f"[DEBUG] Claude raw: {repr(text)}")
    return text


def ask_claude_semantic(user_text, raw_data, user_name, ctx, action_types):
    """二次呼叫 Claude，把 query_* action 的 raw 結果包裝成自然語言回覆。

    根據 action_types 選對應的 prompt 模板：
      - query_todo  → SEMANTIC_TODO_PROMPT（依日期分組、加星期）
      - query_food  → SEMANTIC_FOOD_PROMPT（依過期日排序）
      - 其他        → SEMANTIC_DEFAULT_PROMPT（簡短回答 + 建議）

    user 風格指令會 append 到 system 後面，讓使用者自訂風格也作用在 semantic 回覆上。
    """
    style_block = get_style_instruction(user_name, ctx)
    now = now_taipei()
    today = f"{now.strftime('%Y-%m-%d')}（{weekday_zh(now)}）"

    if action_types & {"query_todo"}:
        system = SEMANTIC_TODO_PROMPT.format(today=today) + style_block
        max_tokens = 1500
    elif action_types & {"query_food"}:
        system = SEMANTIC_FOOD_PROMPT.format(today=today) + style_block
        max_tokens = 1500
    else:
        system = SEMANTIC_DEFAULT_PROMPT + style_block
        max_tokens = 1200

    response = claude.messages.create(
        model="claude-sonnet-5",
        max_tokens=max_tokens,
        system=system,
        messages=[
            {"role": "user", "content": f"使用者問：{user_text}\n\n數據：\n{raw_data}"}
        ],
    )
    return _response_text(response)


def generate_notify_message(data_summary, style_instruction=""):
    try:
        today = now_taipei().strftime("%Y-%m-%d")
        now_time = now_taipei().strftime("%H:%M")
        notify_style = style_instruction if style_instruction else f"\n{DEFAULT_STYLE}"
        response = claude.messages.create(
            model="claude-sonnet-5",
            max_tokens=1500,
            system=f"你負責管理家庭的食品庫存、待辦事項和智能居家設備。現在是 {today} {now_time}。請根據以下資料整理成一則推播訊息。主動補充貼心提醒（快過期的催促、今天的待辦提醒注意時間、天氣提醒帶傘或注意溫差等）。不要加開頭問候語如「早安」，直接進入內容。只回傳推播文字，不要 JSON。{notify_style}",
            messages=[{"role": "user", "content": data_summary}]
        )
        return _response_text(response)
    except Exception as e:
        print(f"[NOTIFY CLAUDE ERROR] {e}")
        return None
