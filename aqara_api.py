"""
Aqara Cloud Open API v3.0 封裝模組（Presence Sensor FP2 等 Wi-Fi 直連裝置用）

FP2 是 Wi-Fi 直連、不掛 Zigbee 網關的裝置，官方沒有本地 API（本地只開 HomeKit HAP，
要接得在家裡多架一個 HomeKit controller）。要讓跑在 Render 上的 home-butler 讀它，
成本最低的路就是官方雲端 Open API：cloud → cloud，跟 switchbot_api.py 同一個形狀。

**跟 SwitchBot 最大的差別是認證有狀態。** SwitchBot 是 token + secret 每次現簽、無狀態；
Aqara 是 OAuth 式的 accessToken / refreshToken，而且 **refreshToken 每次刷新都會換一把**
（舊的當場失效）。Render free instance 會重啟、記憶體會清空，所以權杖一定要落地——存在
「系統狀態」KV 分頁（跟每日推播 marker 同一張表），見下方「權杖生命週期」。

⚠️ **這個模組沒有對真的 FP2 跑過**（開發環境的 egress proxy 擋掉 aqara.com）。協定形狀
取自 Aqara 官方 Home Assistant 整合的 aiot_cloud.py（簽名字串、intent 名稱、header
大小寫都照抄），能離線確定的都對齊了；只有「你家帳號在哪一區」「FP2 的 resource id
各是什麼」這兩件事必須連上真機才知道——這兩件都做成了 runtime 探索，不是寫死的常數：

- 區域：`/aqara/probe` 把已知的六個機房都打一次，看哪一區認得你的 App 憑證。
- resource id：`query.resource.info` 會回該 model 開放的資源清單，`read_device()` 就是
  「先問清單、再照清單讀值」，所以**這裡沒有任何猜出來的 resource id 常數**。想知道
  FP2 到底有哪些欄位，打 `/aqara/devices/{did}/values` 看實際回什麼。

「哪個欄位是有沒有人」目前用名稱關鍵字猜（`_PRESENCE_NAME_HINTS`），猜不到就回 None
而不是瞎猜一個——確認之後把 resource id 填進環境變數 `AQARA_FP2_PRESENCE_RESOURCE`
釘死，就不再依賴關鍵字比對。

## 權杖生命週期

    /aqara/auth/code   → config.auth.getAuthCode  → Aqara 寄授權碼到你的帳號（Email/簡訊）
    /aqara/auth/token  → config.auth.getToken     → 拿 accessToken + refreshToken，寫進 Sheet
    （之後全自動）      → config.auth.refreshToken → 到期前主動換，或吃到 code 108 補換

只有第一步需要人（要去信箱抄授權碼），之後都是自動的。**權杖寫 Sheet 的順序是
refreshToken 先寫**：中途掛掉的話，「新 refresh + 舊 access」還能靠 108 自動補救，
「舊 refresh + 新 access」則是死局（舊 refresh 已經被伺服器作廢），只能重跑一次人工授權。
"""

import hashlib
import random
import string
import threading
import time

import httpx

from config import (
    AQARA_APP_ID,
    AQARA_KEY_ID,
    AQARA_APP_KEY,
    AQARA_REGION,
    AQARA_API_BASE,
    AQARA_ACCOUNT,
    AQARA_ACCOUNT_TYPE,
    AQARA_TOKEN_VALIDITY,
    AQARA_FP2_DID,
    AQARA_FP2_PRESENCE_RESOURCE,
)
import sheets
from ttl_cache import TTLCache

API_PATH = "/v3.0/open/api"
REQUEST_TIMEOUT = 15

# 官方 SDK（aqara/home-assistant aiot_cloud.py）列了前五個機房。
# SG 沒出現在那份清單裡，但官方文件說新加坡有機房，而台灣帳號有機會落在那一區——
# 猜錯的代價只是 probe 多回一個錯誤，所以留著一起試。
REGION_DOMAINS = {
    "CN": "open-cn.aqara.com",
    "USA": "open-usa.aqara.com",
    "KR": "open-kr.aqara.com",
    "RU": "open-ru.aqara.com",
    "GER": "open-ger.aqara.com",
    "SG": "open-sg.aqara.com",
}

# Presence Sensor FP2 的 model。Aqara 的 model 字串就是裝置型號的唯一識別
# （query.device.info 回的 "model" 欄）。
FP2_MODELS = ("lumi.motion.agl001",)

# accessToken 過期／異常。官方 SDK 就是靠這個碼觸發自動 refresh。
CODE_TOKEN_INVALID = 108

# 「這個欄位是不是『有沒有人』」的關鍵字。中英都收，因為 resource 的 name 會隨
# Lang header 與韌體版本變。命中多個時取第一個（catalog 順序）。
_PRESENCE_NAME_HINTS = (
    "presence", "occupanc", "somebody", "someone", "human",
    "有人", "無人", "人體", "存在", "在室",
)

# query.resource.value 一次帶幾個 resource id。官方沒寫上限，切小塊純粹是防呆：
# FP2 開放的資源不多，多打一兩個 round-trip 也無感。
_VALUE_BATCH = 20

# resource 清單（query.resource.info）是韌體層級的靜態資料，換韌體才會變。
# 快取 6 小時，讓每次讀值不用多打一次 API。只快取成功結果（見 ttl_cache 模組說明）。
_catalog_cache = TTLCache(ttl_seconds=6 * 3600, max_entries=16)

_client = httpx.Client(timeout=REQUEST_TIMEOUT)

# ── 權杖狀態（記憶體 + Sheet）────────────────────────────

STATE_ACCESS_TOKEN = "Aqara存取權杖"
STATE_REFRESH_TOKEN = "Aqara更新權杖"
STATE_TOKEN_EXPIRES = "Aqara權杖到期"

# 到期前這麼多秒就主動換，不要等真的過期才被動吃 108。
REFRESH_MARGIN_S = 600

# RLock：refresh 會在持鎖狀態下呼叫 _invoke，而 _invoke 又要讀權杖（同一條 thread
# 再進一次）。同時 polling thread 與 FastAPI threadpool 會並行走到這裡。
_token_lock = threading.RLock()
_tokens = {"access": "", "refresh": "", "expires_at": 0.0}
_tokens_loaded = False
_persist_dirty = False      # 上次寫 Sheet 失敗，下次有機會補寫


def configured() -> bool:
    """三把 App 憑證都有才算設定完成（缺任何一把，簽名一定過不了）。"""
    return bool(AQARA_APP_ID and AQARA_KEY_ID and AQARA_APP_KEY)


# ── 簽名與請求 ────────────────────────────────────────────

def _nonce(length: int = 16) -> str:
    seq = string.ascii_uppercase + string.digits
    return "".join(random.choice(seq) for _ in range(length))


def _sign(access_token: str, nonce: str, timestamp: str) -> str:
    """Headers 的 Sign。

    ⚠️ **整串（含 AppKey 與 AccessToken）都要 .lower() 之後才 MD5**，這是官方 SDK 的
    行為不是筆誤——少了那一步或只 lower 一部分，伺服器一律回簽名錯誤，而錯誤訊息不會
    告訴你錯在大小寫。欄位順序（AccessToken → Appid → Keyid → Nonce → Time → AppKey）
    也是簽名的一部分，不能重排。
    """
    s = f"Appid={AQARA_APP_ID}&Keyid={AQARA_KEY_ID}&Nonce={nonce}&Time={timestamp}{AQARA_APP_KEY}"
    if access_token:
        s = f"AccessToken={access_token}&{s}"
    return hashlib.md5(s.lower().encode("utf-8")).hexdigest()


def _headers(access_token: str) -> dict:
    nonce = _nonce()
    timestamp = str(int(round(time.time() * 1000)))
    headers = {
        "Content-Type": "application/json",
        "Appid": AQARA_APP_ID,
        "Keyid": AQARA_KEY_ID,
        "Nonce": nonce,
        "Time": timestamp,
        "Sign": _sign(access_token, nonce, timestamp),
        "Lang": "zh",
    }
    if access_token:
        headers["Accesstoken"] = access_token
    return headers


def _url(region: str | None = None) -> str:
    """region 帶了就強制用那一區（probe 用）；沒帶就走環境變數設定。"""
    if region:
        domain = REGION_DOMAINS.get(region.upper())
        if not domain:
            raise ValueError(f"未知的 Aqara 區域 {region}（可用：{'/'.join(REGION_DOMAINS)}）")
        return f"https://{domain}{API_PATH}"
    if AQARA_API_BASE:
        return AQARA_API_BASE.rstrip("/") + API_PATH
    domain = REGION_DOMAINS.get((AQARA_REGION or "CN").upper())
    if not domain:
        raise ValueError(
            f"AQARA_REGION={AQARA_REGION} 不在已知清單（{'/'.join(REGION_DOMAINS)}）；"
            f"要用清單外的機房請改設 AQARA_API_BASE"
        )
    return f"https://{domain}{API_PATH}"


def _invoke(intent: str, data, use_token: bool = True,
            token_maintenance: bool = True, region: str | None = None) -> dict:
    """打一次 Open API，回伺服器原始 JSON（{code, message, result, ...}）。

    連線層失敗也包成同一個形狀（code=-1），呼叫端只要看 code 就好，不用同時處理例外。

    `token_maintenance=False` 是防遞迴用的：refresh 自己那一發、以及 108 之後的那次重試
    都不可以再觸發權杖維護，否則權杖真的壞掉時會無限迴圈。
    """
    if not configured():
        return {"code": -1, "message": "Aqara App 憑證未設定（AQARA_APP_ID / AQARA_KEY_ID / AQARA_APP_KEY）"}

    token = ""
    if use_token:
        token = _access_token_for_request(allow_refresh=token_maintenance)
        if not token:
            return {"code": -1, "message": "尚未完成 Aqara 授權（先打 /aqara/auth/code 再 /aqara/auth/token）"}

    try:
        url = _url(region)
    except ValueError as e:
        return {"code": -1, "message": str(e)}

    try:
        resp = _client.post(url, headers=_headers(token), json={"intent": intent, "data": data})
        jo = resp.json()
    except Exception as e:
        return {"code": -1, "message": f"Aqara API 連線失敗（{intent}）：{e}"}

    if not isinstance(jo, dict):
        return {"code": -1, "message": f"Aqara API 回了非預期格式（{intent}）：{str(jo)[:200]}"}

    if jo.get("code") == CODE_TOKEN_INVALID and use_token and token_maintenance:
        refreshed = refresh_now()
        # 只有真的換到新權杖才重試；沒換到就把原本的 108 原樣回去，讓呼叫端看到真相
        # （通常代表 refreshToken 也死了，需要人重跑一次授權）。
        if refreshed.get("code") == 0:
            return _invoke(intent, data, use_token=True, token_maintenance=False, region=region)

    return jo


def _result(jo: dict, what: str):
    """把 {code, message, result} 拆成 (result, error_message)。成功時 error 為 None。"""
    if jo.get("code") == 0:
        return jo.get("result"), None
    msg = jo.get("message") or jo.get("msg") or "未知錯誤"
    return None, f"{what}失敗（code={jo.get('code')}）：{msg}"


# ── 權杖生命週期 ──────────────────────────────────────────

def _ensure_loaded_locked() -> None:
    """從 Sheet 讀回權杖。失敗時**不**標記已載入，下次呼叫再試——一次 Sheets 抖動
    不該讓整個服務退化成「未授權」直到重啟。"""
    global _tokens_loaded
    if _tokens_loaded:
        return
    try:
        access = sheets.state_get(STATE_ACCESS_TOKEN)
        refresh = sheets.state_get(STATE_REFRESH_TOKEN)
        expires = sheets.state_get(STATE_TOKEN_EXPIRES)
    except Exception as e:
        print(f"[AQARA] 從 Sheet 讀權杖失敗（下次呼叫再試）：{e}")
        return
    _tokens["access"] = access or ""
    _tokens["refresh"] = refresh or ""
    try:
        _tokens["expires_at"] = float(expires or 0)
    except (TypeError, ValueError):
        _tokens["expires_at"] = 0.0
    _tokens_loaded = True
    if _tokens["access"]:
        print(f"[AQARA] 已從 Sheet 還原權杖（到期 {_fmt_expiry(_tokens['expires_at'])}）")


def _persist_locked() -> bool:
    """把三個值寫回「系統狀態」KV。

    **refreshToken 先寫**：每次 refresh 伺服器都會作廢舊的 refreshToken，寫到一半掛掉時
    「新 refresh + 舊 access」還能靠 108 自動補救，「舊 refresh + 新 access」則救不回來
    （Sheet 裡那把 refresh 已經是作廢的），只能重跑一次人工授權。
    """
    global _persist_dirty
    try:
        sheets.state_set(STATE_REFRESH_TOKEN, _tokens["refresh"])
        sheets.state_set(STATE_ACCESS_TOKEN, _tokens["access"])
        sheets.state_set(STATE_TOKEN_EXPIRES, int(_tokens["expires_at"]))
        _persist_dirty = False
        return True
    except Exception as e:
        # 記憶體裡還是新的那把，服務照常跑；但這台一重啟就會讀回 Sheet 上舊的（已作廢）權杖。
        _persist_dirty = True
        print(f"[AQARA] ⚠️ 權杖寫入 Sheet 失敗，重啟後需要重新授權：{e}")
        return False


def _expiring_soon() -> bool:
    """expires_at 為 0 代表「伺服器沒告訴我們有效期」——那就不主動換，
    改成被動吃 108 再換（主動換的前提是算得出到期時間）。"""
    if not _tokens["expires_at"]:
        return False
    return time.time() >= _tokens["expires_at"] - REFRESH_MARGIN_S


def _access_token_for_request(allow_refresh: bool = True) -> str:
    with _token_lock:
        _ensure_loaded_locked()
        if _persist_dirty and _tokens["access"]:
            _persist_locked()      # 上次沒寫成功，趁這次補寫
        if allow_refresh and _tokens["refresh"] and _expiring_soon():
            _refresh_locked()
        return _tokens["access"]


def _store_tokens_locked(result: dict) -> None:
    """把 getToken / refreshToken 的 result 寫進記憶體 + Sheet。

    expiresIn 是「還有幾秒」，官方回的可能是字串。拿不到就存 0（＝有效期未知），
    之後靠 108 被動換。"""
    global _tokens_loaded
    _tokens["access"] = str(result.get("accessToken") or "")
    _tokens["refresh"] = str(result.get("refreshToken") or "")
    try:
        expires_in = int(float(result.get("expiresIn") or 0))
    except (TypeError, ValueError):
        expires_in = 0
    _tokens["expires_at"] = (time.time() + expires_in) if expires_in > 0 else 0.0
    _tokens_loaded = True
    _persist_locked()


def _refresh_locked() -> dict:
    """換新權杖。必須在 _token_lock 內呼叫。

    刻意帶著（可能已過期的）accessToken 送出——官方 SDK 就是這樣做的：它的 108 處理
    路徑本身就是「拿舊 token 的 header 去打 refreshToken」。token_maintenance=False
    保證這一發不會再觸發權杖維護。
    """
    if not _tokens["refresh"]:
        return {"code": -1, "message": "沒有 refreshToken，需要重跑人工授權（/aqara/auth/code）"}
    jo = _invoke("config.auth.refreshToken", {"refreshToken": _tokens["refresh"]},
                 use_token=True, token_maintenance=False)
    if jo.get("code") == 0 and isinstance(jo.get("result"), dict):
        _store_tokens_locked(jo["result"])
        print(f"[AQARA] 權杖已更新（到期 {_fmt_expiry(_tokens['expires_at'])}）")
    else:
        print(f"[AQARA] 權杖更新失敗：code={jo.get('code')} {jo.get('message')}")
    return jo


def refresh_now() -> dict:
    """主動換權杖（debug 端點 / 內部 108 處理共用）。回原始 JSON。"""
    with _token_lock:
        return _refresh_locked()


def _fmt_expiry(expires_at: float) -> str:
    if not expires_at:
        return "未知"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(expires_at))


def _mask(token: str) -> str:
    """只露頭尾，log 與 API 回應都不該出現完整權杖。"""
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}…{token[-4:]}"


def token_status() -> dict:
    """權杖現況（遮蔽過，可安全回給 debug 端點）。"""
    with _token_lock:
        _ensure_loaded_locked()
        expires_at = _tokens["expires_at"]
        return {
            "已設定App憑證": configured(),
            "區域": (AQARA_REGION or "CN").upper(),
            "endpoint": AQARA_API_BASE or REGION_DOMAINS.get((AQARA_REGION or "CN").upper(), ""),
            "已授權": bool(_tokens["access"]),
            "accessToken": _mask(_tokens["access"]),
            "refreshToken": _mask(_tokens["refresh"]),
            "到期時間": _fmt_expiry(expires_at),
            "剩餘秒數": int(expires_at - time.time()) if expires_at else None,
            "Sheet寫入待補": _persist_dirty,
        }


def request_auth_code(account: str | None = None, account_type: int | None = None,
                      validity: str | None = None) -> dict:
    """第一步：叫 Aqara 寄一組授權碼到帳號（Email 或手機簡訊）。

    ⚠️ 這是**有副作用**的呼叫（真的會寄信/簡訊），所以 probe 不會用它。
    """
    account = account or AQARA_ACCOUNT
    if not account:
        return {"code": -1, "message": "沒有帳號可用（帶 account 參數或設 AQARA_ACCOUNT）"}
    return _invoke("config.auth.getAuthCode", {
        "account": account,
        "accountType": AQARA_ACCOUNT_TYPE if account_type is None else account_type,
        "accessTokenValidity": validity or AQARA_TOKEN_VALIDITY,
    }, use_token=False)


def exchange_token(auth_code: str, account: str | None = None,
                   account_type: int | None = None) -> dict:
    """第二步：用收到的授權碼換權杖，成功就寫進「系統狀態」KV。"""
    account = account or AQARA_ACCOUNT
    if not account:
        return {"code": -1, "message": "沒有帳號可用（帶 account 參數或設 AQARA_ACCOUNT）"}
    jo = _invoke("config.auth.getToken", {
        "authCode": auth_code,
        "account": account,
        "accountType": AQARA_ACCOUNT_TYPE if account_type is None else account_type,
    }, use_token=False)
    if jo.get("code") == 0 and isinstance(jo.get("result"), dict):
        with _token_lock:
            _store_tokens_locked(jo["result"])
        print(f"[AQARA] 授權完成（到期 {_fmt_expiry(_tokens['expires_at'])}）")
        # 別把權杖原文回給呼叫端（會進 log / 瀏覽器歷史）
        return {"code": 0, "message": "授權完成，權杖已存進「系統狀態」分頁", "status": token_status()}
    return jo


# ── 探索：這個帳號在哪一區 ────────────────────────────────

def probe_regions() -> dict:
    """六個機房各打一次 query.device.info（**不帶權杖**），回原始 code / message。

    怎麼讀結果：App 憑證是綁機房的，所以
    - 回「權杖」相關錯誤（code 108 之類）＝ 這一區認得你的 AppId/簽名，**就是這一區**，
      只是還沒授權而已。
    - 回 appid / sign / 找不到應用之類的錯誤 ＝ 不是這一區。
    找到之後把區碼填進環境變數 AQARA_REGION（清單外的機房則填 AQARA_API_BASE）。

    刻意不用 getAuthCode 來探測——那個會真的寄六封信給你。
    """
    if not configured():
        return {"error": "Aqara App 憑證未設定（AQARA_APP_ID / AQARA_KEY_ID / AQARA_APP_KEY）"}
    out = {}
    for region in REGION_DOMAINS:
        jo = _invoke("query.device.info", {"pageNum": 1, "pageSize": 1},
                     use_token=False, token_maintenance=False, region=region)
        out[region] = {
            "endpoint": REGION_DOMAINS[region],
            "code": jo.get("code"),
            "message": jo.get("message") or jo.get("msg"),
        }
    return {
        "說明": "回權杖相關錯誤的那一區就是你的機房（AppId 認得出來但還沒授權）；"
                "回 appid/sign 錯誤的不是。",
        "regions": out,
    }


def raw_call(intent: str, data=None) -> dict:
    """任意 intent 的逃生口（給還沒封裝的功能探路，例如訊息推送訂閱
    config.resource.subscribe）。回伺服器原始 JSON，不做任何解讀。"""
    return _invoke(intent, {} if data is None else data)


# ── 裝置 ────────────────────────────────────────────────

def get_devices(page_size: int = 50, max_pages: int = 20):
    """帳號下所有裝置（自動翻頁）。回 list，失敗回 {"error": ...}。"""
    devices = []
    for page in range(1, max_pages + 1):
        jo = _invoke("query.device.info", {"pageNum": page, "pageSize": page_size})
        result, err = _result(jo, "查詢裝置")
        if err:
            return {"error": err}
        # result 正常是 {"data": [...], "totalCount": n}；防禦性地也接受直接給 list
        batch = result.get("data") if isinstance(result, dict) else result
        if not batch:
            break
        devices.extend(batch)
        if len(batch) < page_size:
            break
    return devices


def get_device(did: str):
    """單一裝置資訊。找不到回 {"error": ...}。"""
    jo = _invoke("query.device.info", {"dids": [did]})
    result, err = _result(jo, "查詢裝置")
    if err:
        return {"error": err}
    batch = result.get("data") if isinstance(result, dict) else result
    if not batch:
        return {"error": f"找不到裝置 {did}"}
    return batch[0]


def find_fp2_devices():
    """帳號裡所有 FP2。回 list（可能是空的），查詢失敗回 {"error": ...}。"""
    devices = get_devices()
    if isinstance(devices, dict):
        return devices
    return [d for d in devices if _is_fp2(d.get("model", ""))]


def _is_fp2(model: str) -> bool:
    model = (model or "").strip().lower()
    # 精確比對之外多留一個寬鬆條件：Aqara 同型號改版時 model 尾巴可能會變
    # （例如 .v2），寧可多認出來，也不要因為差一個字就整台看不見。
    return model in FP2_MODELS or "motion.agl001" in model


# ── 資源（resource）──────────────────────────────────────

def get_resource_catalog(model: str):
    """某 model 開放了哪些 resource（id / 名稱 / 說明）。快取 6 小時。

    這是整個模組不用寫死 resource id 的關鍵：先問清單，再照清單讀值。
    """
    cached = _catalog_cache.get(model)
    if cached is not None:
        return cached
    jo = _invoke("query.resource.info", {"model": model})
    result, err = _result(jo, f"查詢 {model} 資源清單")
    if err:
        return {"error": err}
    entries = result.get("data") if isinstance(result, dict) else result
    entries = entries or []
    _catalog_cache.set(model, entries)     # 只快取成功結果
    return entries


def get_resource_values(did: str, resource_ids):
    """讀指定 resource 的當下值。回 {resourceId: {"value":..., "timeStamp":...}}。"""
    resource_ids = [str(r) for r in (resource_ids or []) if str(r).strip()]
    if not resource_ids:
        return {}
    values = {}
    for i in range(0, len(resource_ids), _VALUE_BATCH):
        chunk = resource_ids[i:i + _VALUE_BATCH]
        jo = _invoke("query.resource.value",
                     {"resources": [{"subjectId": did, "resourceIds": chunk}]})
        result, err = _result(jo, "查詢資源值")
        if err:
            return {"error": err}
        rows = result.get("data") if isinstance(result, dict) else result
        for row in rows or []:
            rid = str(row.get("resourceId", ""))
            if rid:
                values[rid] = {"value": row.get("value"), "timeStamp": row.get("timeStamp")}
    return values


def read_device(did: str, model: str | None = None):
    """把一台裝置**所有**開放資源的當下值讀回來（先問 catalog、再照 catalog 讀值）。

    回 {resourceId: {"name":..., "description":..., "value":..., "timeStamp":...}}。
    新裝置想知道有哪些欄位可用，打這個看就對了。
    """
    if not model:
        device = get_device(did)
        if isinstance(device, dict) and "error" in device:
            return device
        model = device.get("model", "")
    if not model:
        return {"error": f"裝置 {did} 沒有 model，無法查資源清單"}

    catalog = get_resource_catalog(model)
    if isinstance(catalog, dict) and "error" in catalog:
        return catalog

    meta = {}
    for entry in catalog:
        rid = str(entry.get("resourceId", "") or "")
        if rid:
            meta[rid] = {
                "name": entry.get("name"),
                "description": entry.get("description"),
                "unit": entry.get("unit"),
            }
    if not meta:
        return {"error": f"{model} 沒有回任何開放資源（帳號權限或 model 不對？）"}

    values = get_resource_values(did, list(meta))
    if isinstance(values, dict) and "error" in values:
        return values

    out = {}
    for rid, info in meta.items():
        row = values.get(rid) or {}
        out[rid] = {**info, "value": row.get("value"), "timeStamp": row.get("timeStamp")}
    return out


def write_resource(did: str, resource_id: str, value) -> dict:
    """寫一個 resource（FP2 的偵測靈敏度之類的設定項）。

    ⚠️ 這個 intent 的 data 是 **list** 不是 dict（官方 SDK 的 list_data=True），
    包成 dict 送會被伺服器拒絕。
    """
    return _invoke("write.resource.device", [{
        "subjectId": did,
        "resources": [{"resourceId": str(resource_id), "value": str(value)}],
    }])


# ── FP2 語意層 ───────────────────────────────────────────

def _pick_presence_resource(resources: dict) -> str | None:
    """從讀回來的資源裡挑出「有沒有人」那一個。

    環境變數釘死優先；否則用名稱關鍵字猜。**猜不到就回 None**——與其挑一個看起來像的
    欄位、讓上層拿著錯的值長出自動化，不如誠實承認不知道（回應裡照樣附上全部原始資源，
    人眼掃一遍就能確認到底是哪個 id）。
    """
    if AQARA_FP2_PRESENCE_RESOURCE:
        return AQARA_FP2_PRESENCE_RESOURCE if AQARA_FP2_PRESENCE_RESOURCE in resources else None
    for rid, info in resources.items():
        text = f"{info.get('name') or ''} {info.get('description') or ''}".lower()
        if any(hint in text for hint in _PRESENCE_NAME_HINTS):
            return rid
    return None


def _as_presence(value) -> bool | None:
    """resource 的 value 一律是字串。'1'/'true' 有人，'0'/'false' 沒人，其他回 None。"""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("1", "true", "on", "yes"):
        return True
    if text in ("0", "false", "off", "no"):
        return False
    return None


def fp2_snapshot(did: str | None = None) -> dict:
    """一台 FP2 的當下狀態。

    did 沒帶就用環境變數 AQARA_FP2_DID，再沒有就自己去帳號裡找第一台 FP2
    （多打一次 API，適合探索期；固定下來之後把 did 填進環境變數省掉這一趟）。

    回 {"did", "model", "deviceName", "presence", "presence_resource", "resources"}。
    presence 為 None 代表「這次沒判斷出來」——可能是還沒對到 resource id，也可能是
    讀值本身缺漏；raw 資源都在 resources 裡，不會因為語意層看不懂就丟掉。
    """
    model = None
    device_name = None
    did = did or AQARA_FP2_DID
    if not did:
        found = find_fp2_devices()
        if isinstance(found, dict) and "error" in found:
            return found
        if not found:
            return {"error": "帳號裡找不到 FP2（model lumi.motion.agl001）；"
                             "確認裝置已加進這個 Aqara 帳號、而且授權時勾了它所在的家庭"}
        did = found[0].get("did")
        model = found[0].get("model")
        device_name = found[0].get("deviceName")

    resources = read_device(did, model)
    if isinstance(resources, dict) and "error" in resources:
        return resources

    presence_rid = _pick_presence_resource(resources)
    presence = _as_presence(resources[presence_rid]["value"]) if presence_rid else None
    return {
        "did": did,
        "model": model,
        "deviceName": device_name,
        "presence": presence,
        "presence_resource": presence_rid,
        "resources": resources,
    }
