from linebot import LineBotApi, WebhookHandler
from datetime import datetime
import pytz
import os
import anthropic

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
HOME_BUTLER_API_KEY = os.environ.get("HOME_BUTLER_API_KEY", "")

# 使用者體感版本：與 Dashboard package.json 的 version 同步維護。
# Dashboard 那邊 bump 時，這裡也要跟著改（見 AGENTS.md 版本管理章節）。
<<<<<<< HEAD
APP_VERSION = "1.4.9"
=======
APP_VERSION = "1.5.7"
>>>>>>> 4a05d40 (feat(schedule): add modify_schedule for full-row edit (device/action/params/time))

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
webhook_handler = WebhookHandler(LINE_CHANNEL_SECRET)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
TZ = pytz.timezone('Asia/Taipei')


def now_taipei():
    return datetime.now(TZ)
