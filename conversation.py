import threading
from config import claude, now_taipei
from sheets import get_sheet
from prompt import (
    SYSTEM_PROMPT, DEFAULT_STYLE,
    SEMANTIC_TODO_PROMPT, SEMANTIC_FOOD_PROMPT, SEMANTIC_DEFAULT_PROMPT,
    get_family_members_info, get_current_food, get_current_todo,
    get_device_info, get_schedule_info, get_style_instruction,
)


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
    today = now_taipei().strftime("%Y-%m-%d")
    now_time = now_taipei().strftime("%H:%M")
    style_instruction = get_style_instruction(user_name, ctx)
    prompt = SYSTEM_PROMPT.format(
        today=today, now_time=now_time,
        family_info=get_family_members_info(ctx),
        food_info=get_current_food(ctx),
        todo_info=get_current_todo(ctx),
        device_info=get_device_info(ctx),
        schedule_info=get_schedule_info(ctx),
        current_user=user_name,
        user_style=style_instruction
    )
    history = get_recent_conversation(user_id, ctx)
    messages = history + [{"role": "user", "content": user_message}]
    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=prompt,
        messages=messages
    )
    text = response.content[0].text.strip()
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
    today = now_taipei().strftime("%Y-%m-%d")

    if action_types & {"query_todo"}:
        system = SEMANTIC_TODO_PROMPT.format(today=today) + style_block
        max_tokens = 500
    elif action_types & {"query_food"}:
        system = SEMANTIC_FOOD_PROMPT.format(today=today) + style_block
        max_tokens = 500
    else:
        system = SEMANTIC_DEFAULT_PROMPT + style_block
        max_tokens = 300

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system,
        messages=[
            {"role": "user", "content": f"使用者問：{user_text}\n\n數據：\n{raw_data}"}
        ],
    )
    return response.content[0].text.strip()


def generate_notify_message(data_summary, style_instruction=""):
    try:
        today = now_taipei().strftime("%Y-%m-%d")
        now_time = now_taipei().strftime("%H:%M")
        notify_style = style_instruction if style_instruction else f"\n{DEFAULT_STYLE}"
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system=f"你負責管理家庭的食品庫存、待辦事項和智能居家設備。現在是 {today} {now_time}。請根據以下資料整理成一則推播訊息。主動補充貼心提醒（快過期的催促、今天的待辦提醒注意時間、天氣提醒帶傘或注意溫差等）。不要加開頭問候語如「早安」，直接進入內容。只回傳推播文字，不要 JSON。{notify_style}",
            messages=[{"role": "user", "content": data_summary}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"[NOTIFY CLAUDE ERROR] {e}")
        return None
