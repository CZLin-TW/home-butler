"""Dashboard 裝置配對登入（OAuth 2.0 Device Authorization Grant 風格）。

解決的問題：iOS 把 PWA 加到主畫面後，PWA 有自己獨立的儲存容器；LINE OAuth 會被
iOS 踢去 Safari 完成、cookie 落在 Safari 容器，PWA 永遠拿不到，使用者得「重新加入
主畫面」才行。

這套流程 PWA 全程**不離開容器**：
1. PWA 跟後端要一組 user_code(6 位) + device_token(長亂數，PWA 自己保管)。
2. PWA 顯示 user_code，用 device_token 每幾秒輪詢。
3. 使用者在 LINE Bot 輸入「登入 <user_code>」→ Bot 收到的 webhook user_id 是 LINE
   認證過的身分（比 OAuth 還直接）→ 確認是家庭成員 → 標記該配對 approved + 記下身分。
4. PWA 輪詢看到 approved → Dashboard 後端發 session cookie 到 PWA 容器 → 完成。

身分（lineUserId/name/picture）來自「誰在 Bot 輸入碼」，session 發給「持有 device_token
的 PWA」——知道碼只能『核准』，領 session 要有 device_token，所以碼被猜到也拿不到別人
的 session。配對 5 分鐘過期、approved 後單次使用。狀態存 Sheets「裝置配對」分頁。
"""

import secrets
import time

from sheets import get_or_create_sheet, append_record, update_row_fields

SHEET = "裝置配對"
HEADERS = ["user_code", "device_token", "status", "line_user_id", "name", "picture", "created", "expires"]
CODE_TTL = 300  # 驗證碼有效 5 分鐘


def _sheet():
    return get_or_create_sheet(SHEET, HEADERS)


def _f(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _cleanup(ws, records, now):
    """刪掉已過期的舊配對，避免分頁無限長大（best-effort，失敗不影響主流程）。"""
    try:
        stale = [i for i, r in enumerate(records) if _f(r.get("expires")) < now]
        for i in sorted(stale, reverse=True):
            ws.delete_rows(i + 2)
    except Exception as e:
        print(f"[device_auth] cleanup error: {e}")


def create_pairing():
    """PWA 取得新配對。回 {user_code, device_token, expires_in}。"""
    ws = _sheet()
    now = time.time()
    records = ws.get_all_records()
    _cleanup(ws, records, now)

    pending_codes = {
        str(r.get("user_code", "")) for r in records
        if str(r.get("status", "")) == "pending" and _f(r.get("expires")) > now
    }
    code = None
    for _ in range(20):
        candidate = f"{secrets.randbelow(1000000):06d}"
        if candidate not in pending_codes:
            code = candidate
            break
    if code is None:
        code = f"{secrets.randbelow(1000000):06d}"

    token = secrets.token_hex(16)
    append_record(ws, {
        "user_code": code,
        "device_token": token,
        "status": "pending",
        "line_user_id": "",
        "name": "",
        "picture": "",
        "created": now,
        "expires": now + CODE_TTL,
    })
    return {"user_code": code, "device_token": token, "expires_in": CODE_TTL}


def get_status(device_token):
    """PWA 輪詢用。回 {status, user?}。
    status: pending / approved / expired / consumed / not_found。
    approved 時回 user 並把狀態標成 consumed（單次使用，下次輪詢就拿不到了）。"""
    token = str(device_token or "").strip()
    if not token:
        return {"status": "not_found"}
    ws = _sheet()
    now = time.time()
    records = ws.get_all_records()
    for i, r in enumerate(records):
        if str(r.get("device_token", "")) != token:
            continue
        status = str(r.get("status", ""))
        if status == "approved":
            update_row_fields(ws, i + 2, {"status": "consumed"})
            return {"status": "approved", "user": {
                "lineUserId": str(r.get("line_user_id", "")),
                "name": str(r.get("name", "")),
                "picture": str(r.get("picture", "")),
            }}
        if status == "pending" and _f(r.get("expires")) < now:
            return {"status": "expired"}
        return {"status": status or "pending"}
    return {"status": "not_found"}


def approve(user_code, line_user_id, name, picture=""):
    """Bot 收到「登入 <code>」時呼叫。找 pending 且未過期的配對標記 approved。
    回 True=成功核准 / False=碼錯誤或已過期。"""
    code = str(user_code or "").strip()
    if not code:
        return False
    ws = _sheet()
    now = time.time()
    records = ws.get_all_records()
    for i, r in enumerate(records):
        if str(r.get("user_code", "")) != code:
            continue
        if str(r.get("status", "")) != "pending":
            continue
        if _f(r.get("expires")) < now:
            return False
        update_row_fields(ws, i + 2, {
            "status": "approved",
            "line_user_id": line_user_id,
            "name": name,
            "picture": picture or "",
        })
        return True
    return False
