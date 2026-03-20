from linebot import LineBotApi, WebhookHandler
from datetime import datetime
import pytz
import os
import anthropic
import panasonic_api

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
panasonic_api.PANASONIC_ACCOUNT = os.environ.get("PANASONIC_ACCOUNT", "")
panasonic_api.PANASONIC_PASSWORD = os.environ.get("PANASONIC_PASSWORD", "")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
webhook_handler = WebhookHandler(LINE_CHANNEL_SECRET)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
TZ = pytz.timezone('Asia/Taipei')


def now_taipei():
    return datetime.now(TZ)
