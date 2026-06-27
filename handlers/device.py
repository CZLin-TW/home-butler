import gspread
import json
from datetime import datetime, timedelta
from config import now_taipei, TZ
from sheets import get_device_id_by_name, get_all_devices_by_type, build_row
import switchbot_api
import panasonic_api
import lg_api
import weather_api
import dehumidifier_auto
import dehumidifier_auto_service
import sensor_state
import device_status

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

# ── 防黴送風（關冷氣前先吹乾蒸發器） ──
# 冷氣/除濕運轉會在蒸發器結露，直接關機悶著容易長黴。關機前若「這次運轉夠久且是會結露
# 的模式」，先切送風吹乾 N 分鐘，再由排程把它真正關掉。
ANTIMOLD_FAN_MINUTES = 5          # 送風時長「預設」（實際受 polling thread 5 分粒度影響，約 5~10 分）
ANTIMOLD_MIN_RUNTIME_MINUTES = 30  # 運轉門檻「預設」：從最後一次開機算起運轉滿這麼久才防黴
ANTIMOLD_MODES = {"冷氣", "除濕"}  # 只有會結露的模式才需要（送風/暖氣/自動不攔）
ANTIMOLD_SOURCE = "防黴"           # 排程「來源」欄值，跟使用者/自動關機排程區隔開
# 上面兩個分鐘數可在「智能居家」分頁逐台覆寫（欄位空白就用預設）。
ANTIMOLD_THRESHOLD_COL = "防黴運轉門檻分鐘"
ANTIMOLD_FAN_COL = "防黴送風分鐘"


def _save_ac_last_state(ctx, device_id, power, temperature=None, mode_int=None, fan_int=None, mark_on_time=False, restore_on_off=None):
    """把最後一次 AC 指令寫回「智能居家」分頁，供下次相對調整使用。

    [AC 最後狀態 cache 總覽]
    - 為什麼需要：紅外線 AC 是 write-only，SwitchBot 無法回讀真實狀態，
      只能在自己這邊記下「上次送出的指令」當作 best-effort 狀態。
    - 寫入端：本函式（每次成功送出 AC 指令後由 handle_control_ac 呼叫）。
    - 消費端 1：prompt.py:_format_ac_last_state — 組 system prompt 給 Claude，
      讓「調低 1 度」這類相對指令能據此推算絕對溫度。
    - 消費端 2：web_api.py:api_get_devices / api_dashboard — 給 Dashboard
      在卡片上顯示「目前 26°C 冷氣」之類的提示。
    - power == "off" 時刻意保留先前的溫度/模式/風速，方便下次重新開機時沿用。

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
            # 開機時錨定「最後開機時間」（防黴算運轉時長用）；由 caller 決定何時記
            #（transition，或欄位本來就空）。欄位不存在時下面 header_to_col 會自動略過。
            if mark_on_time:
                new_values["最後開機時間"] = now_str
        else:
            # 關機 → 清掉開機錨點（沒有進行中的運轉了）。下次開機若這欄是空的就會重新錨定，
            # 比只靠 關→開 transition 偵測更穩——避免快取電源狀態漂移時錨不到、防黴永遠不觸發。
            new_values["最後開機時間"] = ""
            # 防黴收尾關用：把模式/溫度/風速還原成防黴前的設定（label 直接寫），
            # 否則會停在「送風」，UI 跟下次開機都變成吹送風而不是原本的冷氣/除濕。
            if restore_on_off:
                new_values.update(restore_on_off)
        # power == "off" 一般情況仍保留先前的溫度/模式/風速，方便下次重新開機時沿用

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
        device_status.update(device_name=rec.get("名稱", ""), fields={
            "lastPower": rec.get("最後電源", ""),
            "lastTemperature": rec.get("最後溫度", ""),
            "lastMode": rec.get("最後模式", ""),
            "lastFanSpeed": rec.get("最後風速", ""),
            "lastUpdatedAt": rec.get("最後更新時間", ""),
        })
    except Exception as e:
        print(f"[AC STATE SAVE ERROR] device={device_id}: {e}")


def _parse_int_safe(value):
    """Parse an int from a Sheet cell value; returns 0 if invalid/empty."""
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return 0


def _parse_optional_int(value):
    """Parse an int from optional action data; returns None if omitted."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return None


def _parse_auto_mode(value):
    if value is None or value == "":
        return True
    text = str(value).strip().lower()
    return text not in ("off", "false", "0", "no", "關", "關閉", "停用", "取消")


def _is_ac_off_action(schedule_row):
    """判斷某筆排程動作是否為『把 AC 關閉』。"""
    if schedule_row.get("動作") != "control_ac":
        return False
    try:
        params = json.loads(schedule_row.get("參數", "{}"))
    except (json.JSONDecodeError, TypeError):
        return False
    return params.get("power") == "off"


def maintain_ac_auto_schedule(device_name, ctx, transitioned_to_on=False):
    """維護某台 AC 的自動關機排程。

    核心規則：AC 開著 AND 沒有使用者 pending off 排程 → 需要 auto，否則不需要。
    timer anchor：只有從『關 → 開』的 transition 才重置計時，純調整（on → on）保留現有 auto。

    transitioned_to_on=True 時視為開機事件（清掉舊 auto、加新 auto）
    transitioned_to_on=False 時視為調整/重新評估（有舊 auto 就保留、沒舊 auto 就按需補）
    """
    try:
        devices = ctx.get("智能居家")
        device_row = next(
            (d for d in devices
             if d.get("名稱") == device_name
             and d.get("類型") == "空調"
             and d.get("狀態") == "啟用"),
            None,
        )
        if not device_row:
            return

        power = str(device_row.get("最後電源", "")).strip()
        hours = _parse_int_safe(device_row.get("自動關機小時數"))

        schedule_sheet = ctx.get_worksheet("排程指令")
        archive_sheet = ctx.get_worksheet("排程封存")
        all_schedules = ctx.get("排程指令")

        # 找這台 AC 的 auto 與 user-off 排程
        existing_auto = [
            (i, r) for i, r in enumerate(all_schedules)
            if r.get("設備名稱") == device_name
            and r.get("狀態") == "待執行"
            and r.get("來源") == "自動"
        ]
        user_off = [
            r for r in all_schedules
            if r.get("設備名稱") == device_name
            and r.get("狀態") == "待執行"
            and (r.get("來源") or "使用者") == "使用者"
            and _is_ac_off_action(r)
        ]

        need_auto = (power == "on" and hours > 0 and not user_off)

        def _archive_and_delete(indices_rows):
            """封存 + 刪除（倒序，避免 row index 偏移）。"""
            if not indices_rows:
                return
            archive_headers = archive_sheet.row_values(1)
            for i, row in sorted(indices_rows, key=lambda x: x[0], reverse=True):
                archive_sheet.append_row(build_row(archive_headers, {**row, "狀態": "已取消"}))
                schedule_sheet.delete_rows(i + 2)
                all_schedules.pop(i)

        def _add_auto():
            """產生一筆自動 off 排程。"""
            trigger = (now_taipei() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
            now_str = now_taipei().strftime("%Y-%m-%d %H:%M")
            headers = schedule_sheet.row_values(1)
            new_row = {
                "設備名稱": device_name,
                "動作": "control_ac",
                "參數": json.dumps({"power": "off"}, ensure_ascii=False),
                "觸發時間": trigger,
                "建立者": "系統",
                "建立時間": now_str,
                "狀態": "待執行",
                "來源": "自動",
            }
            schedule_sheet.append_row(build_row(headers, new_row))
            all_schedules.append(new_row)  # 同步 ctx 快取
            print(f"[AUTO SCHEDULE] {device_name} off @ {trigger}")

        if need_auto:
            if transitioned_to_on:
                # 開機事件：清舊、加新（timer 重置）
                _archive_and_delete(existing_auto)
                _add_auto()
            elif not existing_auto:
                # 調整時沒有舊 auto（可能剛才 user off 排程被刪了）→ 補一筆
                _add_auto()
            # else: 調整時已有舊 auto → 保留不動（timer 不重置）
        else:
            # 不需要 auto（AC 關了、或已有 user off、或功能停用）→ 清掉現有
            _archive_and_delete(existing_auto)
    except Exception as e:
        print(f"[MAINTAIN AUTO SCHEDULE ERROR] device={device_name}: {e}")


def _minutes_since_power_on(device_row, now):
    """距「最後開機時間」過了幾分鐘；欄位空白或無法解析回 None（→ 視為無法判斷，不防黴）。"""
    raw = str(device_row.get("最後開機時間", "") or "").strip()
    if not raw:
        return None
    try:
        on_dt = TZ.localize(datetime.strptime(raw, "%Y-%m-%d %H:%M"))
    except (ValueError, TypeError):
        return None
    return (now - on_dt).total_seconds() / 60


def _antimold_threshold(device_row):
    """這台 AC 的防黴運轉門檻（分）。智能居家沒設或非法 → 用 ANTIMOLD_MIN_RUNTIME_MINUTES。
    0 代表一開機就算達標（每次關都送風）。"""
    v = _parse_optional_int(device_row.get(ANTIMOLD_THRESHOLD_COL))
    return v if v is not None and v >= 0 else ANTIMOLD_MIN_RUNTIME_MINUTES


def _antimold_fan_minutes(device_row):
    """這台 AC 的防黴送風時長（分）。智能居家沒設或 < 1 → 用 ANTIMOLD_FAN_MINUTES。"""
    v = _parse_optional_int(device_row.get(ANTIMOLD_FAN_COL))
    return v if v is not None and v >= 1 else ANTIMOLD_FAN_MINUTES


def _should_antimold(device_row, now):
    """關 AC 前是否該先送風防黴：上次是「開著」的冷氣/除濕模式，且從最後一次開機算起已運轉
    ≥ 該台門檻分鐘（智能居家可逐台設，預設 30）。無法判斷開機時間（欄位空白／實體遙控器開的）
    就保守不防黴。

    必須先確認「最後電源==on」：否則對一台已經關著的 AC 再按一次關（最後模式/開機時間還是
    上次運轉留下的舊值），會誤判成要防黴 → 反而把它吹成送風（開機），結果完全相反。"""
    if str(device_row.get("最後電源", "") or "").strip() != "on":
        return False
    mode = str(device_row.get("最後模式", "") or "").strip()
    if mode not in ANTIMOLD_MODES:
        return False
    elapsed = _minutes_since_power_on(device_row, now)
    return elapsed is not None and elapsed >= _antimold_threshold(device_row)


def _schedule_antimold_off(device_name, ctx, fan_minutes, restore_mode="", restore_temp="", restore_fan=""):
    """寫一筆「防黴收尾關」排程：fan_minutes 分後把 AC 真正關掉。
    - params 帶 antimold_final=True，讓那次關機不再被攔截送風（防遞迴）。
    - params 帶 restore_*（防黴前的模式/溫度/風速）：收尾關機時把這些寫回，避免狀態停在送風。
    - 來源用 ANTIMOLD_SOURCE，跟使用者/自動關機排程區隔，maintain_ac_auto_schedule 不會誤刪它。
    - 已有未執行的防黴排程就不重複建。"""
    schedule_sheet = ctx.get_worksheet("排程指令")
    all_schedules = ctx.get("排程指令")
    for r in all_schedules:
        if (r.get("設備名稱") == device_name
                and r.get("狀態") == "待執行"
                and r.get("來源") == ANTIMOLD_SOURCE):
            return
    trigger = (now_taipei() + timedelta(minutes=fan_minutes)).strftime("%Y-%m-%d %H:%M")
    now_str = now_taipei().strftime("%Y-%m-%d %H:%M")
    headers = schedule_sheet.row_values(1)
    new_row = {
        "設備名稱": device_name,
        "動作": "control_ac",
        "參數": json.dumps({
            "power": "off", "antimold_final": True,
            "restore_mode": restore_mode, "restore_temp": restore_temp, "restore_fan": restore_fan,
        }, ensure_ascii=False),
        "觸發時間": trigger,
        "建立者": "系統",
        "建立時間": now_str,
        "狀態": "待執行",
        "來源": ANTIMOLD_SOURCE,
    }
    schedule_sheet.append_row(build_row(headers, new_row))
    all_schedules.append(new_row)  # 同步 ctx 快取
    print(f"[ANTIMOLD] {device_name} 送風 {fan_minutes} 分後關 @ {trigger}")


def _cancel_antimold_schedules(device_name, ctx):
    """取消某台 AC 未執行的防黴收尾排程（使用者/系統重新開機時呼叫，避免剛開又被收尾關掉）。"""
    try:
        all_schedules = ctx.get("排程指令")
        targets = [
            (i, r) for i, r in enumerate(all_schedules)
            if r.get("設備名稱") == device_name
            and r.get("狀態") == "待執行"
            and r.get("來源") == ANTIMOLD_SOURCE
        ]
        if not targets:
            return
        schedule_sheet = ctx.get_worksheet("排程指令")
        archive_sheet = ctx.get_worksheet("排程封存")
        archive_headers = archive_sheet.row_values(1)
        for i, row in sorted(targets, key=lambda x: x[0], reverse=True):
            archive_sheet.append_row(build_row(archive_headers, {**row, "狀態": "已取消"}))
            schedule_sheet.delete_rows(i + 2)
            all_schedules.pop(i)
        print(f"[ANTIMOLD] {device_name} 重新開機，取消防黴收尾排程")
    except Exception as e:
        print(f"[ANTIMOLD CANCEL ERROR] device={device_name}: {e}")


def handle_control_ac(data, ctx, from_auto_schedule=False):
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

    # 命令前的狀態快照：判斷「關→開」transition（auto-schedule timer 是否重置）+ 防黴判斷
    prior_row = next(
        (r for r in ctx.get("智能居家")
         if r.get("Device ID") == device_id and r.get("狀態") == "啟用"),
        {},
    )
    prior_power_on = str(prior_row.get("最後電源", "")).strip() == "on"

    power = data.get("power", "on")
    temperature = None
    mode = None
    fan = None
    if power == "off":
        # 防黴送風：冷氣/除濕運轉 ≥30 分後關機，先切送風吹乾蒸發器再排程關閉。
        # antimold_final 是防黴收尾排程自己觸發的那次關機 → 不再攔截，直接關（防遞迴）。
        _now = now_taipei()
        if not data.get("antimold_final") and _should_antimold(prior_row, _now):
            temp_keep = _parse_int_safe(prior_row.get("最後溫度")) or 27
            fan_minutes = _antimold_fan_minutes(prior_row)
            fan_result = switchbot_api.ac_set_all(device_id, temp_keep, 4, 1, "on")  # mode 4=送風, fan 1=自動
            if fan_result.get("success"):
                # 先抓「防黴前」的模式/溫度/風速，交給收尾關機時還原（下面 _save_ac_last_state
                # 寫送風會把 prior_row 改成送風，所以要在寫入前先讀）。
                restore_mode = str(prior_row.get("最後模式", "") or "").strip()
                restore_temp = prior_row.get("最後溫度", "")
                restore_fan = str(prior_row.get("最後風速", "") or "").strip()
                # 記成「送風中」的真實狀態，但不更新最後開機時間（這是延續，不是新開機）
                _save_ac_last_state(ctx, device_id, "on", temp_keep, 4, 1)
                _schedule_antimold_off(device_name, ctx, fan_minutes,
                                       restore_mode=restore_mode, restore_temp=restore_temp,
                                       restore_fan=restore_fan)
                # 刻意不呼叫 maintain_ac_auto_schedule：送風期間不要再生自動關機排程
                return f"✅ {device_name} 已運轉一陣子，先送風 {fan_minutes} 分鐘防黴，之後自動關閉 🌬️"
            print(f"[ANTIMOLD] {device_name} 送風失敗，改直接關機：{fan_result.get('error')}")
        elif not data.get("antimold_final"):
            # 沒進防黴 → 印出原因，方便從 Render log 診斷。最常見：開機時間空白（這次開機發生在
            # 部署防黴之前、或實體遙控器開的）→ 運轉時長算不出來、保守不送風直接關。
            print(f"[ANTIMOLD] {device_name} 不送風直接關："
                  f"電源={prior_row.get('最後電源', '')!r} 模式={prior_row.get('最後模式', '')!r} "
                  f"開機時間={prior_row.get('最後開機時間', '')!r} "
                  f"運轉={_minutes_since_power_on(prior_row, _now)} 分（門檻 {_antimold_threshold(prior_row)}）")
        result = switchbot_api.ac_turn_off(device_id)
    else:
        mode_str = data.get("mode", "cool")
        temperature = int(data.get("temperature", 24 if mode_str == "heat" else 27))
        fan_str = data.get("fan_speed", "auto")
        mode = switchbot_api.AC_MODE_MAP.get(mode_str, 2)
        fan = switchbot_api.AC_FAN_MAP.get(fan_str, 1)
        result = switchbot_api.ac_set_all(device_id, temperature, mode, fan, "on")

    if result.get("success"):
        transitioned = (power == "on") and not prior_power_on
        # 開機時錨定「最後開機時間」：transition（關→開）或目前欄位是空的就記。後者讓「關機會
        # 清空 → 下次開機必錨定」，即使 prior_power_on 快取漂移（如上次用實體遙控器關）也補得回。
        on_time_empty = not str(prior_row.get("最後開機時間", "") or "").strip()
        # 防黴收尾關（antimold_final）：把模式/溫度/風速還原回防黴前，避免狀態停在送風。
        restore = None
        if data.get("antimold_final"):
            restore = {}
            if data.get("restore_mode"):
                restore["最後模式"] = data["restore_mode"]
            if data.get("restore_temp") not in (None, ""):
                restore["最後溫度"] = data["restore_temp"]
            if data.get("restore_fan"):
                restore["最後風速"] = data["restore_fan"]
            restore = restore or None
        _save_ac_last_state(ctx, device_id, power, temperature, mode, fan,
                            mark_on_time=(transitioned or on_time_empty), restore_on_off=restore)
        # 重新開機（含純調整 on→on）→ 取消任何待執行的防黴收尾關，避免剛開又被關
        if power == "on":
            _cancel_antimold_schedules(device_name, ctx)
        # 自動排程 safety net：非自動排程觸發時才重算（避免 auto 觸發 → auto 再生 auto 的無限循環）
        if not from_auto_schedule:
            maintain_ac_auto_schedule(device_name, ctx, transitioned_to_on=transitioned)
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


def handle_set_dehumidifier_auto(data, ctx):
    """Configure sensor-driven dehumidifier auto mode from LINE/Siri actions."""
    auto_mode = _parse_auto_mode(data.get("auto_mode", data.get("enabled", True)))
    threshold = _parse_optional_int(
        data.get("threshold", data.get("humidity", data.get("target_humidity")))
    )
    duration_min = _parse_optional_int(data.get("duration_min"))
    sensor_name = data.get("sensor_name", "")
    targets, error = dehumidifier_auto_service.resolve_dehumidifier_targets(
        ctx,
        device_name=data.get("device_name", ""),
        scope=data.get("scope", "single"),
    )
    if error:
        return error

    if auto_mode and threshold is not None and not 30 <= threshold <= 80:
        return "❌ 自動除濕目標濕度需介於 30%～80%"
    if duration_min is not None and duration_min < 0:
        return "❌ 自動除濕等待時間不能小於 0 分鐘"

    snapshot = sensor_state.snapshot()
    successes = []
    failures = []
    for device in targets:
        device_name = device.get("名稱", "")
        if not device_name:
            continue

        if not auto_mode:
            dehumidifier_auto_service.set_auto_rule(ctx, device_name, False)
            successes.append(f"✅ {device_name} 已關閉自動除濕模式")
            continue

        if not dehumidifier_auto_service.has_control_driver(device):
            failures.append(f"⚠️ {device_name} 未啟用：缺少品牌控制資訊或 Device ID/Auth")
            continue

        sensor, sensor_error = dehumidifier_auto_service.choose_sensor_for_dehumidifier(
            ctx, device, sensor_name=sensor_name, snapshot=snapshot
        )
        if sensor_error:
            failures.append(f"⚠️ {sensor_error}")
            continue

        result = dehumidifier_auto_service.set_auto_rule(
            ctx,
            device_name,
            True,
            sensor_name=sensor["name"],
            duration_min=duration_min,
            threshold=threshold,
            snapshot=snapshot,
        )
        rule = result["rule"]
        duration_text = (
            "立即"
            if rule["duration_min"] == 0
            else f"持續 {rule['duration_min']} 分鐘"
        )
        line = (
            f"✅ {device_name} 已開啟自動除濕模式，目標 {rule['threshold']}%，"
            f"使用 {sensor['name']}，{duration_text}"
        )
        if sensor["humidity"] is not None:
            line += f"，目前濕度 {sensor['humidity']}%"
        elif not sensor["online"]:
            line += "；感測器目前沒有即時濕度，會等下一次回報後開始判斷"
        successes.append(line)

    lines = list(successes)
    if failures:
        lines.extend(failures)
    if lines:
        if failures and not successes:
            return "❌ 自動除濕模式未設定：\n" + "\n".join(failures)
        return "\n".join(lines)
    return "❌ 自動除濕模式未設定"


def _resolve_dehumidifier(device_name, ctx):
    """找出要操作的除濕機 row。回傳 (row, error_str)。
    有指定名稱找該台；沒指定且只有一台用那台；多台要求指定；都沒有回找不到。"""
    dh_devices = get_all_devices_by_type("除濕機", ctx)
    if device_name:
        for d in dh_devices:
            if d.get("名稱") == device_name:
                return d, None
    if len(dh_devices) == 1:
        return dh_devices[0], None
    if len(dh_devices) > 1:
        names = "、".join([d.get("名稱") for d in dh_devices])
        return None, f"❌ 有多台除濕機（{names}），請指定要控制哪一台"
    return None, "❌ 找不到除濕機設備，請先在「智能居家」分頁設定"


def _dehumidifier_brand(row):
    """品牌欄空值視為 Panasonic（向下相容尚未填品牌的既有 row）。"""
    return (row.get("品牌") or "Panasonic").strip()


def _control_dehumidifier_panasonic(row, device_name, power, mode, humidity):
    auth = row.get("Auth", "")
    gwid = row.get("Device ID", "")
    if not auth or not gwid:
        return f"❌ {device_name} 缺少 Auth 或 Device ID 設定"
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
    return f"❌ {device_name} 控制失敗：{result.get('error', '未知錯誤')}"


def _control_dehumidifier_lg(row, device_name, power, mode, humidity):
    device_id = row.get("Device ID", "")
    if not device_id:
        return f"❌ {device_name} 缺少 Device ID 設定"
    if power == "off":
        result = lg_api.dehumidifier_turn_off(device_id)
    elif power == "on" and not mode and not humidity:
        result = lg_api.dehumidifier_turn_on(device_id)
    else:
        turn_on_result = lg_api.dehumidifier_turn_on(device_id)
        if not turn_on_result.get("success"):
            return f"❌ {device_name} 開機失敗：{turn_on_result.get('error', '未知錯誤')}"
        result = turn_on_result
        if mode:
            result = lg_api.dehumidifier_set_mode(device_id, mode)
            if not result.get("success"):
                return f"❌ {device_name} 模式設定失敗：{result.get('error')}"
        if humidity:
            result = lg_api.dehumidifier_set_humidity(device_id, int(humidity))
    if result.get("success"):
        return f"✅ {device_name} 指令已送出"
    return f"❌ {device_name} 控制失敗：{result.get('error', '未知錯誤')}"


def handle_control_dehumidifier(data, ctx, _internal=False):
    """除濕機手動控制。_internal=True 是自動模式規則自己呼叫，跳過 lock 檢查。
    依「智能居家」品牌欄分流到 Panasonic / LG。"""
    device_name = data.get("device_name", "")
    row, error = _resolve_dehumidifier(device_name, ctx)
    if error:
        return error
    device_name = row.get("名稱", device_name)

    # 自動模式啟用中拒收外部控制（Dashboard 手動 / LINE bot / 排程都會走這條）
    if not _internal and dehumidifier_auto.is_locked(device_name):
        return f"❌ {device_name} 目前處於自動模式，請先在 Dashboard 關閉自動模式才能手動控制"

    power = data.get("power", "")
    mode = data.get("mode", "")
    humidity = data.get("humidity", "")

    if _dehumidifier_brand(row) == "LG":
        return _control_dehumidifier_lg(row, device_name, power, mode, humidity)
    return _control_dehumidifier_panasonic(row, device_name, power, mode, humidity)


def handle_query_dehumidifier(data, ctx):
    device_name = data.get("device_name", "")
    row, error = _resolve_dehumidifier(device_name, ctx)
    if error:
        return error
    device_name = row.get("名稱", device_name)

    if _dehumidifier_brand(row) == "LG":
        status = lg_api.get_dehumidifier_status(row.get("Device ID", ""))
        return lg_api.format_dehumidifier_status(status, device_name)
    auth = row.get("Auth", "")
    gwid = row.get("Device ID", "")
    status = panasonic_api.get_dehumidifier_status(auth, gwid)
    return panasonic_api.format_dehumidifier_status(status, device_name)


def handle_query_weather(data):
    date_str = data.get("date", "today")
    location = data.get("location", None)
    summary = weather_api.get_weather_summary(date_str, location)
    print(f"[WEATHER] date={date_str}, location={location}, summary={summary}")
    return weather_api.format_weather(summary)
