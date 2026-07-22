from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from linebot.models import TextSendMessage
from datetime import datetime, timedelta
import json

from config import line_bot_api, TZ, now_taipei, date_with_weekday, daily_push_hour
from sheets import RequestContext, build_row, state_get, state_set
from prompt import get_style_instruction, _format_schedule_params
from conversation import save_conversation, cleanup_conversation, generate_notify_message, get_recent_conversation
from calendar_sync import sync_external_events
from handlers.device import (
    handle_control_ac, handle_control_ir, handle_control_dehumidifier, ANTIMOLD_SOURCE,
)
from handlers.recurring_todo import materialize_recurring_todos
from auth import verify_api_key
import weather_api

router = APIRouter(dependencies=[Depends(verify_api_key)])

# 每日綜合推播的去重 marker key（存在 sheets 的「系統狀態」KV 分頁）。
LAST_DAILY_PUSH_KEY = "最後每日推播日期"


def run_daily_push(ctx):
    """晚間綜合推播：同步外部行事曆 + 明日天氣 + 食品到期 + 明日/未完成待辦 + 待執行排程，
    逐成員依各自風格生成文字後 push。

    原由 GAS 日計時器（晚上 9~10 點）呼叫；GAS 退場後改由 main.py 的 polling thread
    透過 run_daily_push_if_due() 每天觸發一次（用 Sheet marker 去重）。
    /notify 端點保留為手動觸發（debug / 補發）入口。
    """
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
            label = f"{overdue_mark}{source_mark}{r['事項']}（{date_with_weekday(date_str)}{time_part}）"
            if r.get("類型") == "私人":
                person = r.get("負責人", "")
                if person not in todo_private:
                    todo_private[person] = []
                todo_private[person].append(label)
            else:
                todo_public.append(label)

    # 註：週期待辦已改「提前 materialize 下一筆」，明天該出現的實例此刻多半已是真實待辦，
    # 由上面的待辦迴圈自然納入 → 不再需要從模板現算的「明日預報」（那會與實例重複）。

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

        # per-member 例外隔離：某成員 user_id 失效 / 封鎖 bot / 429 不該中斷
        # 後面所有人的推播。
        try:
            line_bot_api.push_message(user_id, TextSendMessage(text=message))
            save_conversation(user_id, "assistant", message)
            cleanup_conversation(user_id)
        except Exception as e:
            print(f"[NOTIFY] push to {member_name or user_id} failed: {e}")
            continue


def run_daily_push_if_due(ctx, now=None):
    """每日綜合推播的閘門：每天過了 DAILY_PUSH_HOUR 之後的第一個 tick 觸發一次。

    去重靠 Sheet 上的 marker（系統狀態 / 最後每日推播日期），跨 Render 重啟存活：
    - 還沒到鐘點 → 不發。
    - 今天已發過（marker == 今天）→ 不發。
    - 傍晚睡著、稍晚才醒 → 醒來補發一次；睡整晚跨午夜才醒 → 當天不補（符合「睡整天就漏」語意）。
    marker 讀取失敗時保守不發，避免重發浪費 LINE 推播額度。marker 寫在「發完之後」，
    寧可極少數情況下重發、也不要標記已發卻其實沒發。
    """
    now = now or now_taipei()
    if now.hour < daily_push_hour():
        return
    today_str = now.date().isoformat()
    try:
        if state_get(LAST_DAILY_PUSH_KEY) == today_str:
            return
    except Exception as e:
        print(f"[daily-push] marker 讀取失敗，本 tick 跳過：{e}")
        return
    run_daily_push(ctx)
    try:
        state_set(LAST_DAILY_PUSH_KEY, today_str)
    except Exception as e:
        print(f"[daily-push] marker 寫入失敗（下個 tick 可能重發一次）：{e}")


@router.post("/notify")
async def notify():
    """手動觸發晚間綜合推播（debug / 補發）。日常由 polling thread 自動驅動，不再靠 GAS。

    注意：手動呼叫「不」檢查也「不」更新每日 marker——純粹立即發一次，方便補一封。
    """
    try:
        ctx = RequestContext()
        ctx.load()
        run_daily_push(ctx)
        return {"status": "ok"}
    except Exception as e:
        # 端點失敗回非 2xx，讓手動呼叫看得到錯誤而非 silent 200。
        print(f"[NOTIFY ERROR] {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})



def _push_todo_reminder(todo_type, person, message, ctx, members):
    """把一則「已組好文字」的提醒推給對應成員（私人 → 負責人本人；公開 → 全部啟用成員）。

    去重：提醒文字由程式規則產生（不經 Claude 潤飾），所以能精確比對——該則文字只要
    已出現在該成員最近對話裡就跳過。文字本身內嵌階段/逾時小時數，於是「同一階段 5 分
    tick 連發」被擋掉（文字相同），「跨小時的逾時提醒」因文字不同而放行（每小時一次）。

    去重看「最近幾則」而非「只看最後一則」：多個待辦同時提醒時，彼此會把對方擠成最後一
    則，只比最後一則會互相誤判成沒發過而輪流重發。
    """
    if todo_type == "私人":
        targets = [m for m in members if m.get("狀態") == "啟用" and m.get("名稱") == person]
    else:
        targets = [m for m in members if m.get("狀態") == "啟用"]

    for member in targets:
        user_id = member.get("Line User ID")
        if not user_id:
            continue

        recent = get_recent_conversation(user_id, ctx)
        already = any(
            msg.get("role") == "assistant" and msg.get("content", "").strip() == message
            for msg in recent
        )
        if already:
            continue

        # per-member 例外隔離：單一成員推播失敗不中斷其他人。
        try:
            line_bot_api.push_message(user_id, TextSendMessage(text=message))
            save_conversation(user_id, "assistant", message)
            cleanup_conversation(user_id)
        except Exception as e:
            print(f"[REMINDER] push to {member.get('名稱', user_id)} failed: {e}")
            continue


def _process_todo_reminders(now, today, ctx):
    """掃過所有待辦，依「距任務時間多久」決定推播；文字全部由程式規則產生（不經 Claude
    潤飾），好讓去重能用精確比對（見 _push_todo_reminder）。

    三個階段，各自去重、各發一次：
      1. 事前：任務前 20 分鐘內          → 「⏰ 提醒…」
      2. 剛逾時：過期後 10~60 分         → 「⚠️ 未完成…」
      3. 持續逾時：過期滿 1 小時起，每小時 → 「⚠️ 已逾時約 N 小時…」
         N 寫進文字：同一小時內文字相同 → 去重擋住不洗版；跨小時文字變了 → 放行下一次。

    逾時（階段 2、3）只在「任務當天」發，過了當天午夜就自動停，避免跨日還每小時嘮叨。
    事前（階段 1）不設當天限制（跨午夜的任務提前 20 分提醒才正確）。
    只處理有設「時間」的待辦；只有日期沒時間的不進這段（會出現在晚間綜合推播）。
    """
    todo_records = ctx.get("待辦事項")
    members = ctx.get("家庭成員")

    for r in todo_records:
        if r.get("狀態") != "待辦":
            continue
        date_str = r.get("日期", "")
        time_str = r.get("時間", "")
        if not date_str or not time_str:
            continue

        try:
            todo_dt = TZ.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        except (ValueError, TypeError) as e:
            print(f"[WARN] 無法解析即時提醒時間 {date_str!r} {time_str!r}: {e}")
            continue

        item = r["事項"]
        person = r.get("負責人", "")
        todo_type = r.get("類型", "公開")
        delta_min = (now - todo_dt).total_seconds() / 60  # 正 = 已逾時
        same_day = todo_dt.date() == today

        message = None
        if -20 <= delta_min <= 0:
            message = f"⏰ 提醒：{item}（{time_str}）"
        elif same_day and delta_min >= 60:
            hours = int(delta_min // 60)
            message = f"⚠️ 已逾時約 {hours} 小時：{item}（原訂 {time_str}）"
        elif same_day and delta_min >= 10:
            message = f"⚠️ 未完成：{item}（原訂 {time_str}）"

        if message:
            _push_todo_reminder(todo_type, person, message, ctx, members)


def _execute_pending_schedules(now, ctx):
    """執行到時間的排程，回傳被處理過的設備名稱集合。

    超時 2 小時以上的排程標為「已過期」不執行（避免使用者離線太久回來突然冷氣全開）；
    防黴收尾關例外——晚關也該關，不然冷氣會一直送風下去。
    is_auto 標記用來避免「auto 排程觸發 → 又觸發 auto 重算 → 無限循環」。
    """
    schedule_records = ctx.get("排程指令")
    schedule_sheet = ctx.get_worksheet("排程指令")

    header = schedule_sheet.row_values(1)
    try:
        status_col = header.index("狀態") + 1
    except ValueError:
        status_col = 7  # fallback

    processed_devices = set()
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
        if trigger_dt > now:
            continue

        device_name = r.get("設備名稱", "")
        processed_devices.add(device_name)
        hours_late = (now - trigger_dt).total_seconds() / 3600

        # 防黴收尾關「不過期」：它就是把還在送風的冷氣關掉，晚關也該關——否則 polling thread
        # 曾停過 >2h（例如實例睡著），那台冷氣會一直送風下去關不掉。其餘排程維持 2h 過期保險
        #（避免使用者離線太久、回來冷氣突然全開之類）。
        if hours_late > 2 and r.get("來源") != ANTIMOLD_SOURCE:
            schedule_sheet.update_cell(i + 2, status_col, "已過期")
            print(f"[SCHEDULE EXPIRED] {device_name} {r.get('動作')} 超時 {hours_late:.1f} 小時")
            continue

        action_type = r.get("動作", "")
        try:
            params = json.loads(r.get("參數", "{}"))
        except (json.JSONDecodeError, TypeError):
            params = {}
        params["device_name"] = device_name

        is_auto = r.get("來源") == "自動"
        result = ""
        if action_type == "control_ac":
            result = handle_control_ac(params, ctx, from_auto_schedule=is_auto)
        elif action_type == "control_ir":
            result = handle_control_ir(params, ctx)
        elif action_type == "control_dehumidifier":
            result = handle_control_dehumidifier(params, ctx)

        schedule_sheet.update_cell(i + 2, status_col, "已執行")
        print(f"[SCHEDULE EXEC] {device_name} {action_type} {params} → {result}")

    return processed_devices


def _archive_processed_schedules(processed_devices, ctx):
    """把已執行/已過期的排程搬到封存表（前提：該設備所有排程都已收尾）。

    刻意只 fetch 一次 sheet 後在記憶體裡計算要刪的 row index，再倒序刪。
    這樣可以避免：
      1. 多次 get_all_records 之間外部來源（LINE bot 同時操作）造成索引不一致
      2. 邊刪邊讀導致 row 偏移
    """
    if not processed_devices:
        return

    schedule_sheet = ctx.get_worksheet("排程指令")
    schedule_archive = ctx.get_worksheet("排程封存")

    current_records = schedule_sheet.get_all_records()
    rows_to_archive = []  # list of (sheet_row_number, record)
    for device_name in processed_devices:
        device_records = [r for r in current_records if r.get("設備名稱") == device_name]
        if any(r.get("狀態") == "待執行" for r in device_records):
            continue  # 還有排程，這台先不封存
        for i, r in enumerate(current_records):
            if r.get("設備名稱") == device_name and r.get("狀態") in ("已執行", "已過期"):
                rows_to_archive.append((i + 2, r))  # +2: header row + 0-index

    archive_headers = schedule_archive.row_values(1)
    for sheet_row, row in sorted(rows_to_archive, key=lambda x: x[0], reverse=True):
        schedule_archive.append_row(build_row(archive_headers, row))
        schedule_sheet.delete_rows(sheet_row)


def run_realtime_tick(ctx, now=None):
    """即時 tick，每 5 分鐘由 main.py 的 polling thread 呼叫一次（原 GAS 每 15 分鐘）：
    1. 同步外部行事曆
    2. 生成今天該出現的週期性待辦（materialize；總開關關閉時 no-op）
    3. 推播即將到期/未完成的待辦提醒
    4. 執行到時間的設備排程
    5. 把收尾完的排程封存

    待辦提醒的時間窗與去重都收在 _process_todo_reminders 內（文字由程式規則產生 →
    精確比對去重），不再依賴整點判斷。

    每個步驟各自 try/except 隔離——這是無人值守跑在背景 thread 的工作，一步壞不該擋掉
    其餘步驟（尤其行事曆同步失敗，不能害到期排程不執行）。

    註：週期性待辦的生成「只」掛這條 tick，絕不另外掛第二個時間源——雙時間源會重入重生。
    """
    now = now or now_taipei()
    today = now.date()

    try:
        sync_external_events(ctx)
    except Exception as e:
        print(f"[realtime] 行事曆同步失敗：{e}")

    try:
        # 先 materialize：生成的當日實例要能被同一 tick 後段的 _process_todo_reminders 納入
        materialize_recurring_todos(now, ctx)
    except Exception as e:
        print(f"[realtime] 週期待辦生成失敗：{e}")

    try:
        _process_todo_reminders(now, today, ctx)
    except Exception as e:
        print(f"[realtime] 待辦提醒失敗：{e}")

    try:
        processed_devices = _execute_pending_schedules(now, ctx)
        _archive_processed_schedules(processed_devices, ctx)
    except Exception as e:
        print(f"[realtime] 排程執行/封存失敗：{e}")


@router.post("/notify_realtime")
async def notify_realtime():
    """手動觸發 realtime tick（debug / 補做）。日常由 polling thread 每 5 分自動驅動，不再靠 GAS。"""
    try:
        ctx = RequestContext()
        ctx.load()
        run_realtime_tick(ctx)
        return {"status": "ok"}
    except Exception as e:
        print(f"[NOTIFY_REALTIME ERROR] {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
