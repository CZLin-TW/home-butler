"""Agent 失聯告警：把「早就算得出來的離線狀態」接到 LINE 推播。

**這個模組只觀察與通知，不控制任何設備、不動任何業務資料。** 副作用只有兩個：
LINE 推播，以及「系統狀態」KV 分頁裡的去重 marker。

## 為什麼需要

2026-07-19 兩台 PC 的 butler agent 同時中了「self-restart 孤兒 hard-crash」：
watchdog 陪葬，而 Task Scheduler 早就看到原本的 task 乾淨 exit(0)、回到 `Ready`
不再觸發——**於是沒有任何人被通知，兩台躺了三天**。

`pc_state.snapshot()` 其實一直算得出 `online`（`OFFLINE_THRESHOLD_S`），但那個值
只餵給 Dashboard 畫灰點。沒人盯著 Dashboard 的時候，等於沒有監控。這裡把那條線
接到 LINE，讓「沒人發現它死了」這個失效模式消失。

## 幾個非顯而易見的決定

- **只在狀態翻轉時推播**（離線一則、恢復一則），不是每個 tick 重發。LINE 免費方案
  每月 200 則推播額度，而 tick 是每 5 分一次——不去重的話一台機器離線一天就燒掉
  288 則，額度當天見底，真正要緊的待辦提醒跟著發不出去。
- **翻轉狀態存 Sheet**（`系統狀態` KV），跨 Render 重啟存活。純 in-memory 的話每次
  重啟都會把「早就通知過的離線」再報一次。in-memory 只當讀取快取，讓穩定狀態下不必
  每個 tick 都去打 Sheet。
- **告警門檻刻意比 Dashboard 的離線判定寬**（預設 15 分，`pc_state` 是 5 分）。
  Dashboard 畫灰點寧可靈敏，推播寧可遲鈍：agent 的 auto-update self-restart、網路
  抖動、Render 重啟後 backfill 還沒跑完，都可能造成幾分鐘的空窗，5 分鐘會誤報。
- **推播不寫進「對話暫存」**。待辦提醒會寫，是因為它拿對話內容做去重；這裡用 Sheet
  marker 去重不需要，而且維運告警灌進對話會污染 Claude 的上下文。
- **只認得曾經回報過的機器**：從沒 push 過 heartbeat 的 PC 不在 `pc_state` 裡，這裡
  也就看不到它。刻意不另外維護一份「應該有哪些機器」的清單——那會變成第二處要同步的
  設定，忘了更新時一樣是靜默失效。重啟後靠 `pc_state.backfill_from_sheet()` 從 Sheet
  撈回最近 24h，所以重啟不會失憶。
- **已知限制**：身分認 IP（與 `pc_state` 的 keying 一致）。DHCP 換 IP 會讓舊 IP 看起來
  永遠離線、報一次假警，新 IP 則重新建立基準。家裡兩台 IP 固定，接受這個代價。
"""

import asyncio
import os
import time
from datetime import datetime

from linebot.models import TextSendMessage

import agent_ws
import pc_state
from agent_ws import send_agent_command
from config import TZ, line_bot_api
from sheets import state_get, state_set

# 「家庭成員」分頁上的收件人開關欄（main.py startup 用 ensure_columns 自動補）
ALERT_COLUMN = "系統告警"

# 連續幾個 tick 打不到劇院 agent 才告警（tick 5 分 → 預設約 15 分）。
# 單次逾時很可能只是 WebSocket 中繼剛好在重連，不值得驚動任何人。
THEATER_FAIL_STREAK = 3

_marker_cache: dict[str, str] = {}
_theater_fail_streak = 0
_loop = None


def alert_offline_seconds() -> int:
    """PC agent 幾秒沒回報就算失聯。下限鎖在 300 秒——比 agent 的 60 秒推播間隔還短的
    門檻只會製造假警報。"""
    try:
        return max(300, int(os.environ.get("AGENT_OFFLINE_ALERT_SECONDS", "900")))
    except ValueError:
        return 900


def set_event_loop(loop):
    """main.py 在 async startup 抓 running loop 餵進來，sync thread 才問得到劇院 agent。
    跟 lighting_auto 同一套橋接方式。"""
    global _loop
    _loop = loop


# ── 收件人 ─────────────────────────────────────────────

def _truthy(v) -> bool:
    return str(v or "").strip().upper() in ("TRUE", "1", "YES", "Y", "ON", "是", "要")


def _alert_targets(ctx) -> list[dict]:
    """收件人＝「家庭成員」裡把 `系統告警` 勾起來的啟用成員。

    **沒有任何人勾就退回「所有啟用成員」**，這是刻意的 fail-loud：這個功能存在的唯一
    理由就是「不要靜悄悄地沒人知道」，若因為忘了設定就變成誰都不通知，等於把要修的洞
    原封不動搬進新程式碼裡。覺得吵的人去 Sheet 勾一下就只發給該勾的人。
    """
    members = [m for m in ctx.get("家庭成員") if m.get("狀態") == "啟用"]
    flagged = [m for m in members if _truthy(m.get(ALERT_COLUMN))]
    if flagged:
        return flagged
    if members:
        print(f"[health] 「家庭成員」沒有任何人勾選「{ALERT_COLUMN}」，"
              f"本次告警發給全部 {len(members)} 位啟用成員")
    return members


def _push(targets: list[dict], text: str) -> int:
    """推給每位收件人，回傳成功送出的則數。per-member 隔離，一個失敗不擋其他人。"""
    sent = 0
    for member in targets:
        user_id = member.get("Line User ID")
        if not user_id:
            continue
        try:
            line_bot_api.push_message(user_id, TextSendMessage(text=text))
            sent += 1
        except Exception as e:
            print(f"[health] push to {member.get('名稱', user_id)} failed: {e}")
    return sent


# ── 去重 marker（"ok" / "bad:<第一次偵測到的 epoch>"）────────

def _marker_get(key: str) -> str:
    if key in _marker_cache:
        return _marker_cache[key]
    value = state_get(key)
    _marker_cache[key] = value
    return value


def _marker_set(key: str, value: str) -> None:
    state_set(key, value)
    _marker_cache[key] = value


def _report(ctx, key: str, bad: bool, bad_text: str, ok_text, now=None) -> None:
    """狀態機：只有在 ok ↔ bad 之間翻轉時才推播一次。

    `ok_text` 是個 callable，收「這次故障持續了幾秒」（算不出來時 None），因為恢復
    通知最有用的資訊就是那個數字——7/19 那次的重點正是「躺了三天」。

    第一次見到某個 key 而且狀態正常 → 只寫基準、不推「恢復了」（剛部署完不該對全家
    廣播一輪「XXX 恢復正常」）。第一次見到就是壞的 → 照常告警，那是真的該知道。

    marker 寫在推播成功「之後」：全數推播失敗時不寫，下個 tick 會重試——寧可晚五分鐘
    重試，也不要標記成已通知卻其實沒送出去（那又是一次靜默失效）。

    `now` 一定要跟呼叫端用同一顆時鐘：marker 存的是「第一次偵測到異常的時間」，恢復時
    相減得出故障時長，兩邊時鐘不一致的話那個數字會是錯的。
    """
    now = now or time.time()
    try:
        prev = _marker_get(key)
    except Exception as e:
        print(f"[health] marker 讀取失敗，本 tick 跳過 {key}：{e}")
        return

    prev_bad = prev.startswith("bad")
    if prev and prev_bad == bad:
        return

    want = f"bad:{int(now)}" if bad else "ok"

    if not prev and not bad:
        try:
            _marker_set(key, want)      # 首次見到且正常：只建立基準
        except Exception as e:
            print(f"[health] marker 寫入失敗 {key}：{e}")
        return

    outage = None
    if not bad and prev_bad:
        _, _, since = prev.partition(":")
        try:
            outage = now - float(since)
        except ValueError:
            outage = None

    text = bad_text if bad else ok_text(outage)
    targets = _alert_targets(ctx)
    if not targets:
        print(f"[health] {key} 轉為 {'異常' if bad else '正常'}，但沒有可推播的對象，僅記錄狀態")
    elif _push(targets, text) == 0:
        print(f"[health] {key} 轉為 {'異常' if bad else '正常'}，"
              f"但一則都沒送出，marker 不更新（下個 tick 重試）")
        return

    try:
        _marker_set(key, want)
    except Exception as e:
        print(f"[health] marker 寫入失敗 {key}（下個 tick 可能重發一次）：{e}")


# ── 格式化 ─────────────────────────────────────────────

def _humanize(seconds) -> str:
    if seconds is None:
        return "未知時間"
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} 天 {hours} 小時"
    if hours:
        return f"{hours} 小時 {minutes} 分"
    return f"{minutes} 分鐘"


def _fmt_time(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, TZ).strftime("%m/%d %H:%M")


# ── 檢查一：PC agent ────────────────────────────────────

def check_pc_agents(ctx, now=None) -> None:
    now = now or time.time()
    threshold = alert_offline_seconds()
    for ip, pc in pc_state.snapshot().items():
        last = float(pc.get("last_heartbeat_at") or 0)
        if last <= 0:
            continue        # 沒有任何回報基準（backfill 還沒還原）→ 無從判斷，跳過
        silent = now - last
        hostname = pc.get("hostname") or ip
        _report(
            ctx,
            f"PC失聯_{ip}",
            silent >= threshold,
            (f"🔴 PC agent 失聯\n\n"
             f"{hostname}（{ip}）\n"
             f"最後回報：{_fmt_time(last)}（{_humanize(silent)}前）\n\n"
             f"處理：在該台開 PowerShell 跑\n"
             f"Start-ScheduledTask -TaskName ButlerAgent"),
            lambda outage, h=hostname, i=ip:
                f"🟢 PC agent 恢復\n\n{h}（{i}）\n失聯約 {_humanize(outage)}",
            now=now,
        )


# ── 檢查二：劇院 agent ──────────────────────────────────

def _on_event_loop_thread() -> bool:
    """現在是不是就在 event loop 那條 thread 上。

    `/notify_realtime`（手動 debug 端點）是 async 的，會在 loop 裡**同步**呼叫
    run_realtime_tick。這條路徑下再 `run_coroutine_threadsafe` 回同一顆 loop 然後 block
    等結果 = 自己等自己，整個 server 會卡到 timeout 為止，還會被誤記成一次劇院失敗。

    這個檢查本來就只是「順便看一眼」，遇到這種呼叫路徑直接跳過即可——polling thread
    每 5 分那條照常會檢查到。
    """
    try:
        return asyncio.get_running_loop() is _loop
    except RuntimeError:
        return False        # 不在任何 loop 裡 = 一般背景 thread，可以放心 block


def _theater_summary() -> dict:
    if _loop is None:
        raise RuntimeError("event loop 尚未就緒（startup 未完成）")
    future = asyncio.run_coroutine_threadsafe(
        send_agent_command("theater.summary", {}, required_capability="theater", timeout=15.0),
        _loop,
    )
    message = future.result(timeout=20.0)
    if message.get("status") != "ok":
        raise RuntimeError(message.get("error") or "theater.summary failed")
    return message.get("result") if isinstance(message.get("result"), dict) else {}


def check_theater_agent(ctx) -> None:
    """劇院 agent 存活。**只有在那台 PC 的 butler agent 還在線時才檢查。**

    這個前提是為了不重複告警：整台 PC 失聯時 check_pc_agents 已經報過了，這裡再補一則
    「劇院沒回應」只是噪音，而且會把因果講反（不是劇院掛了，是整台不見了）。

    連續失敗 THEATER_FAIL_STREAK 次才算數：單次逾時多半是 WebSocket 中繼剛好在重連。
    streak 只放 in-memory——它是「最近幾個 tick」的短期觀察，重啟後重新累積即可，真的
    掛著的話最多晚十幾分鐘再報一次。
    """
    global _theater_fail_streak

    if _loop is None:
        return          # startup 還沒把 loop 餵進來，無從檢查（不是劇院的錯，不算失敗）

    if _on_event_loop_thread():
        return          # 從 async endpoint 同步進來的，block 會自我死鎖（見該函式 docstring）

    online_theater = any(
        a.get("online") and "theater" in (a.get("capabilities") or [])
        for a in agent_ws.snapshot_agents()
    )
    if not online_theater:
        _theater_fail_streak = 0
        return          # 沒有在線的 theater agent → 這個檢查不適用（PC 那條會處理）

    try:
        _theater_summary()
        _theater_fail_streak = 0
        reason = ""
    except Exception as e:
        _theater_fail_streak += 1
        reason = str(e)
        if _theater_fail_streak < THEATER_FAIL_STREAK:
            print(f"[health] 劇院 agent 無回應（{_theater_fail_streak}/{THEATER_FAIL_STREAK}）：{reason}")
            return

    _report(
        ctx,
        "劇院agent無回應",
        bool(reason),
        (f"🔴 劇院 agent 無回應\n\n"
         f"該台 PC 的 butler agent 仍在線，但 theater.summary 連續失敗\n"
         f"錯誤：{reason[:120]}\n\n"
         f"處理：在該台開系統管理員 PowerShell 跑\n"
         f'Start-ScheduledTask -TaskName "Theater Agent"'),
        lambda outage: f"🟢 劇院 agent 恢復\n\n無回應約 {_humanize(outage)}",
    )


def run_checks(ctx, now=None) -> None:
    """由 notify.run_realtime_tick 每 5 分呼叫一次。兩項檢查各自 try/except 隔離——
    一項壞掉不該連累另一項，更不該連累 tick 上其他真正在幹活的步驟。"""
    try:
        check_pc_agents(ctx, now=now)
    except Exception as e:
        print(f"[health] PC agent 檢查失敗：{e}")
    try:
        check_theater_agent(ctx)
    except Exception as e:
        print(f"[health] 劇院 agent 檢查失敗：{e}")
