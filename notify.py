from fastapi import APIRouter, Depends
from linebot.models import TextSendMessage
from datetime import datetime, timedelta
import json

from config import line_bot_api, TZ, now_taipei
from sheets import RequestContext, build_row
from prompt import get_style_instruction, _format_schedule_params
from conversation import save_conversation, cleanup_conversation, generate_notify_message, get_recent_conversation
from calendar_sync import sync_external_events
from handlers.device import handle_control_ac, handle_control_ir, handle_control_dehumidifier
from auth import verify_api_key
import weather_api

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.post("/notify")
async def notify():
    try:
        ctx = RequestContext()
        ctx.load()

        # 查詢本月推播額度
        quota_info = ""
        try:
            quota = line_bot_api.get_message_quota()
            consumption = line_bot_api.get_message_quota_consumption()
            total = quota.value if hasattr(quota, 'value') else 200
            used = consumption.total_usage
            remaining = total - used
            quota_info = f"\n\n📊 本月推播額度：剩餘 {remaining}/{total} 則"
        except Exception as e:
            print(f"[NOTIFY] Failed to get quota: {e}")

        # 同步外部行事曆
        sync_external_events(ctx)

        today = now_taipei().date()
        tomorrow = today + timedelta(days=1)

        # 食品到期提醒：到期日 <= 今天+2天（已過期 + 今明後天到期）
        food_alert = []
        for r in ctx.get("食品庫存"):
            if r.get("狀態") != "有效":
                continue
            expiry_str = r.get("過期日", "")
            if not expiry_str:
                continue
            try:
                expiry = datetime.strptime(str(expiry_str), "%Y-%m-%d").date()
            except (ValueError, TypeError) as e:
                print(f"[WARN] 無法解析食品過期日 {expiry_str!r}: {e}")
                continue
            days_left = (expiry - today).days
            if days_left <= 2:
                label = f"{r['品名']}（{expiry_str}）"
                food_alert.append(label)

        members = ctx.get("家庭成員")

        # 明日天氣預報（與今日比較）
        today_weather = None
        tomorrow_weather = None
        try:
            today_weather = weather_api.get_weather_data_for_notify("today")
            tomorrow_weather = weather_api.get_weather_data_for_notify("tomorrow")
        except Exception as e:
            print(f"[NOTIFY WEATHER ERROR] {e}")

        # 明天待辦 + 未完成任務
        todo_public = []
        todo_private = {}
        for r in ctx.get("待辦事項"):
            if r.get("狀態") != "待辦":
                continue
            date_str = r.get("日期", "")
            if not date_str:
                continue
            try:
                todo_date = datetime.strptime(str(date_str), "%Y-%m-%d").date()
            except (ValueError, TypeError) as e:
                print(f"[WARN] 無法解析待辦日期 {date_str!r}: {e}")
                continue
            # 未完成（今天及之前）或明天的待辦
            if todo_date <= tomorrow:
                time_part = f" {r['時間']}" if r.get("時間") else ""
                overdue_mark = "⚠️ 未完成 " if todo_date <= today else ""
                source = str(r.get("來源", "")).strip()
                source_mark = "📅 " if source and source != "本地" else ""
                label = f"{overdue_mark}{source_mark}{r['事項']}（{date_str}{time_part}）"
                if r.get("類型") == "私人":
                    person = r.get("負責人", "")
                    if person not in todo_private:
                        todo_private[person] = []
                    todo_private[person].append(label)
                else:
                    todo_public.append(label)

        # 待執行排程
        schedule_pending = []
        for r in ctx.get("排程指令"):
            if r.get("狀態") == "待執行":
                params_text = _format_schedule_params(r.get("動作", ""), r.get("參數", ""))
                schedule_pending.append(f"{r.get('設備名稱', '')}｜{params_text}｜{r.get('觸發時間', '')}")

        for member in members:
            if member.get("狀態") != "啟用":
                continue
            user_id = member.get("Line User ID")
            member_name = member.get("名稱", "")
            if not user_id:
                continue

            data_parts = []
            if tomorrow_weather:
                data_parts.append(f"【重點】明日天氣預報：{tomorrow_weather}")
            if today_weather:
                data_parts.append(f"（參考）今日天氣：{today_weather}")
            if tomorrow_weather and today_weather:
                data_parts.append("請以明日天氣為主，今日僅供比較溫差變化。如果明天比今天冷很多或會下雨，主動提醒。")
            if food_alert:
                data_parts.append("食品到期提醒：" + "、".join(food_alert))
            if todo_public:
                data_parts.append("明日與未完成待辦：" + "、".join(todo_public))
            if member_name in todo_private:
                data_parts.append("您的私人待辦：" + "、".join(todo_private[member_name]))
            if schedule_pending:
                data_parts.append("待執行排程：" + "、".join(schedule_pending))

            if not data_parts:
                continue

            data_summary = "\n".join(data_parts)
            member_style = get_style_instruction(member_name, ctx)
            message = generate_notify_message(data_summary, member_style)
            if not message:
                message = data_summary
            if quota_info:
                message += quota_info

            line_bot_api.push_message(user_id, TextSendMessage(text=message))
            save_conversation(user_id, "assistant", message)
            cleanup_conversation(user_id)

        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}



@router.post("/notify_realtime")
async def notify_realtime():
    try:
        now = now_taipei()
        today = now.date()
        window_start = now
        window_end = now + timedelta(minutes=20)
        is_near_hour = now.minute <= 4 or now.minute >= 55

        ctx = RequestContext()
        ctx.load()

        # 同步外部行事曆
        sync_external_events(ctx)

        todo_records = ctx.get("待辦事項")
        members = ctx.get("家庭成員")

        def push_to_member(person, todo_type, todo_item, data_summary, fallback_message):
            target_members = []
            if todo_type == "私人":
                for member in members:
                    if member.get("狀態") == "啟用" and member.get("名稱") == person:
                        target_members.append(member)
            else:
                target_members = [m for m in members if m.get("狀態") == "啟用"]
            for member in target_members:
                user_id = member.get("Line User ID")
                if not user_id:
                    continue
                # 去重：檢查對話暫存中是否已有該待辦的提醒
                recent = get_recent_conversation(user_id, ctx)
                already_notified = any(
                    msg.get("role") == "assistant" and todo_item in msg.get("content", "")
                    for msg in recent
                )
                if already_notified:
                    print(f"[SKIP] 已提醒過 {user_id}: {todo_item}")
                    continue
                member_name = member.get("名稱", "")
                member_style = get_style_instruction(member_name, ctx)
                message = generate_notify_message(data_summary, member_style)
                if not message:
                    message = fallback_message
                line_bot_api.push_message(user_id, TextSendMessage(text=message))
                save_conversation(user_id, "assistant", message)
                cleanup_conversation(user_id)

        for r in todo_records:
            if r.get("狀態") != "待辦":
                continue
            date_str = r.get("日期", "")
            time_str = r.get("時間", "")
            person = r.get("負責人", "")
            todo_type = r.get("類型", "公開")

            if not date_str or not time_str:
                continue

            try:
                todo_dt = TZ.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
            except (ValueError, TypeError) as e:
                print(f"[WARN] 無法解析即時提醒時間 {date_str!r} {time_str!r}: {e}")
                continue

            if window_start <= todo_dt <= window_end:
                data_summary = f"即時提醒：{r['事項']}，時間 {time_str}"
                fallback = f"⏰ 提醒：{r['事項']}（{time_str}）"
                push_to_member(person, todo_type, r['事項'], data_summary, fallback)

            elif is_near_hour and todo_dt.date() == today and todo_dt < now:
                data_summary = f"未完成提醒：{r['事項']} 原訂 {time_str}，尚未完成"
                fallback = f"⚠️ 未完成：{r['事項']}（原訂 {time_str}）"
                push_to_member(person, todo_type, r['事項'], data_summary, fallback)

        # ── 排程執行 ──
        schedule_records = ctx.get("排程指令")
        schedule_sheet = ctx.get_worksheet("排程指令")
        schedule_archive = ctx.get_worksheet("排程封存")
        processed_devices = set()

        # 找出狀態欄位的位置
        header = schedule_sheet.row_values(1)
        try:
            status_col = header.index("狀態") + 1
        except ValueError:
            status_col = 7  # fallback

        for i, r in enumerate(schedule_records):
            if r.get("狀態") != "待執行":
                continue
            trigger_str = r.get("觸發時間", "")
            if not trigger_str:
                continue
            try:
                trigger_dt = TZ.localize(datetime.strptime(str(trigger_str), "%Y-%m-%d %H:%M"))
            except (ValueError, TypeError):
                continue

            if trigger_dt <= now:
                hours_late = (now - trigger_dt).total_seconds() / 3600
                device_name = r.get("設備名稱", "")
                processed_devices.add(device_name)

                if hours_late > 2:
                    schedule_sheet.update_cell(i + 2, status_col, "已過期")
                    print(f"[SCHEDULE EXPIRED] {device_name} {r.get('動作')} 超時 {hours_late:.1f} 小時")
                else:
                    action_type = r.get("動作", "")
                    try:
                        params = json.loads(r.get("參數", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        params = {}
                    params["device_name"] = device_name

                    result = ""
                    if action_type == "control_ac":
                        result = handle_control_ac(params, ctx)
                    elif action_type == "control_ir":
                        result = handle_control_ir(params, ctx)
                    elif action_type == "control_dehumidifier":
                        result = handle_control_dehumidifier(params, ctx)

                    schedule_sheet.update_cell(i + 2, status_col, "已執行")
                    print(f"[SCHEDULE EXEC] {device_name} {action_type} {params} → {result}")

        # 檢查有變動的設備是否還有待執行排程，全部完成則封存
        # 只 fetch 一次 sheet，所有 archival 統一在最後倒序刪除，避免：
        #   1. 多次 get_all_records 之間外部來源（LINE bot）改動造成索引不一致
        #   2. 邊刪邊讀導致 row 偏移
        if processed_devices:
            current_records = schedule_sheet.get_all_records()
            rows_to_archive = []  # list of (sheet_row_number, record)
            for device_name in processed_devices:
                device_records = [r for r in current_records if r.get("設備名稱") == device_name]
                if any(r.get("狀態") == "待執行" for r in device_records):
                    continue  # 還有排程，不封存
                for i, r in enumerate(current_records):
                    if r.get("設備名稱") == device_name and r.get("狀態") in ("已執行", "已過期"):
                        rows_to_archive.append((i + 2, r))  # +2: header row + 0-index

            # 倒序刪除避免 index 偏移
            archive_headers = schedule_archive.row_values(1)
            for sheet_row, row in sorted(rows_to_archive, key=lambda x: x[0], reverse=True):
                schedule_archive.append_row(build_row(archive_headers, row))
                schedule_sheet.delete_rows(sheet_row)

        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
