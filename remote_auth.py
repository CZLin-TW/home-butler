"""遙控器模式登入：用一組共用密碼換取受限的 kid 身分（不需 LINE、不需家長核准）。

為什麼需要：iOS 把網頁「加到主畫面」後，那個圖示是獨立的儲存容器、且啟動網址不帶
?kid=1，所以「靠網址參數決定兒童模式」在 PWA 場景行不通——每次都落到一般登入。
改成在登入畫面提供「遙控器模式」+ 密碼，模式選擇在 UI 上，PWA 容器內就能完成，
與啟動網址無關。Dashboard 端驗過後簽發 role=kid 的 session（沿用裝置頁限制）。

密碼存「遙控器」分頁（名稱 / 密碼 / 啟用），可多筆（不同遙控器不同密碼）。比對用
constant-time。因為 Dashboard 是公開網址，加「連續錯誤鎖定」防暴力嘗試（全域、in-memory；
home-butler 單實例，重啟即重置，家用足夠）。所有嘗試都經 Dashboard → 這裡，所以全域
計數抓得到。
"""

import time
import secrets

from sheets import get_or_create_sheet

SHEET = "遙控器"
HEADERS = ["名稱", "密碼", "啟用"]

MAX_FAILS = 5         # 連續錯幾次就鎖
LOCKOUT_SEC = 300     # 鎖多久（秒）

_fail_count = 0
_locked_until = 0.0


def _truthy(v):
    return str(v or "").strip().upper() in ("TRUE", "1", "YES", "Y", "ON", "是", "要")


def verify(password):
    """回 {ok, user?, locked?, retry_after?}。
    user = {"name": ..., "role": "kid"}。鎖定中直接回 locked，不查密碼。"""
    global _fail_count, _locked_until
    now = time.time()

    if now < _locked_until:
        return {"ok": False, "locked": True, "retry_after": int(_locked_until - now)}

    pw = str(password or "")
    if pw:
        ws = get_or_create_sheet(SHEET, HEADERS)
        for r in ws.get_all_records():
            if not _truthy(r.get("啟用")):
                continue
            stored = str(r.get("密碼", ""))
            # constant-time 比對，避免 timing 推測；兩邊都轉成 str（Sheet 可能把純數字存成 int）。
            if stored and secrets.compare_digest(pw, stored):
                _fail_count = 0
                name = str(r.get("名稱", "") or "遙控器")
                return {"ok": True, "user": {"name": name, "role": "kid"}}

    # 沒對中：累計失敗，達門檻就鎖一段時間。
    _fail_count += 1
    if _fail_count >= MAX_FAILS:
        _locked_until = now + LOCKOUT_SEC
        _fail_count = 0
        return {"ok": False, "locked": True, "retry_after": LOCKOUT_SEC}
    return {"ok": False}
