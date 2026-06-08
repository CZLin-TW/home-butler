"""共用的 in-memory ring-buffer + Sheet backup 小工具。

pc_state / sensor_state / ac_history / dehumidifier_history 四個模組的「資料形狀」
各不相同（point 欄位、keying(ip vs device_name)、snapshot 結構、last_*_at 欄名、
sensor 的 co2 migration、pc 的 last_heartbeat 還原）。這些差異是刻意的，**故意不**
抽成繼承基類——會需要大量 hook、對控制 Dashboard 圖表與 PC 上線判定的運作中程式
增加抽象風險，不划算。

但 to_float_or_none 與 trim_sheet 兩段是逐字重複、與資料形狀無關的「純機制」，抽到
這裡共用，避免「修一個漏三個」。
"""

import time


def to_float_or_none(v):
    """Sheet 讀回的字串 → float；空字串 / None / 非數字回 None。"""
    if v == "" or v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def trim_sheet(ws, *, log_prefix, keep_points, hard_limit):
    """刪掉 timestamp < now-24h 的舊 row。row 1 是 header，從 row 2 起算。

    假設 row 按 append 順序排（=按 timestamp 升序），過期 row 都在最前面，一次
    batch delete 一段最便宜（不必逐筆 API call）。

    keep_points：hard-limit 防呆觸發時要保留的 row 數。多台裝置共用「同一張」分頁時，
    caller 應傳 max_points × 裝置數——原本各模組寫死單裝置的 max_points，多裝置時會
    少留資料（這就是把四份重複抽出來、一次修掉的那個 bug）。
    """
    try:
        records = ws.get_all_records()
        cutoff = time.time() - 86400
        last_old_idx = -1
        for i, r in enumerate(records):
            try:
                t = float(r.get("timestamp", 0))
            except (ValueError, TypeError):
                continue
            if t < cutoff:
                last_old_idx = i
            else:
                break  # 遇到第一個 fresh 即停（chronological 假設）

        # Hard limit 防呆：即使 timestamp 都 fresh，row 數量爆了也強制砍
        if last_old_idx < 0 and len(records) > hard_limit:
            last_old_idx = max(0, len(records) - keep_points - 1)
            print(f"{log_prefix} hard-limit trim: row count {len(records)} > {hard_limit}")

        if last_old_idx >= 0:
            count = last_old_idx + 1
            ws.delete_rows(2, 2 + count - 1)
            print(f"{log_prefix} trimmed {count} old rows")
    except Exception as e:
        print(f"{log_prefix} trim error: {e}")
