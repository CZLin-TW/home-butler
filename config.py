from linebot import LineBotApi, WebhookHandler
from datetime import datetime
import pytz
import os
import time
import anthropic
import httpx

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
HOME_BUTLER_API_KEY = os.environ.get("HOME_BUTLER_API_KEY", "")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "")
# Siri 捷徑語音入口（/api/assistant）沒帶 user_id 時的「匿名」fallback 身分。
# 正確用法：每位家人（含自己）的捷徑各自帶自己的 Line User ID，後端才認得出是誰
# （拿到對應名字、自訂風格、各自的對話記憶）。
# 這個值刻意維持中性、不要設成任何家人的真實 Line ID——否則「忘了填 user_id」的請求
# 會靜默變成那個人、冒名操作並污染他的對話記憶。沒帶 ID 就當匿名訪客：能正常控制家電，
# 但無名字/無風格/對話記憶獨立一份。
SIRI_USER_ID = os.environ.get("SIRI_USER_ID", "siri")

# LG ThinQ Connect（thinq.dev 官方 API）。除濕機等 LG 智慧家電用。
# LG_PAT：thinq.dev 產生的 Personal Access Token（需勾裝置讀取 + 控制權限）。
# LG_COUNTRY：國碼，台灣 = TW（決定打哪個區域 endpoint）。
# LG_CLIENT_ID：自訂的 client 識別字串，固定一組即可（ThinQ 要求每個 client 穩定）。
LG_PAT = os.environ.get("LG_PAT", "")
LG_COUNTRY = os.environ.get("LG_COUNTRY", "TW")
LG_CLIENT_ID = os.environ.get("LG_CLIENT_ID", "home-butler-client")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
webhook_handler = WebhookHandler(LINE_CHANNEL_SECRET)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
TZ = pytz.timezone('Asia/Taipei')


def now_taipei():
    return datetime.now(TZ)


# 使用者體感版本：source of truth 是 Dashboard 的 package.json:version。
# 透過 Dashboard /api/version runtime 撈，避免 bump 版本要 push 兩個 repo。
# 失敗時用上一次 cache 的值，最壞 fallback「未知」（LINE bot 仍能回應，只是版本講不準）。
_VERSION_TTL = 3600
_version_cache = {"value": None, "ts": 0.0}


def get_app_version():
    """從 Dashboard 撈使用者體感版本。Cache 1 小時，fetch 失敗用上次的或 fallback。"""
    now = time.time()
    if _version_cache["value"] and now - _version_cache["ts"] < _VERSION_TTL:
        return _version_cache["value"]
    if not DASHBOARD_URL:
        return _version_cache["value"] or "未知"
    try:
        r = httpx.get(f"{DASHBOARD_URL.rstrip('/')}/api/version", timeout=5.0)
        v = r.json().get("version")
        if v:
            _version_cache["value"] = v
            _version_cache["ts"] = now
            return v
    except Exception as e:
        print(f"[VERSION FETCH] {e}")
    return _version_cache["value"] or "未知"
