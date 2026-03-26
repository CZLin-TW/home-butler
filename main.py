from fastapi import FastAPI, Request, HTTPException
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import json
import re
import httpx
import traceback
import threading

from config import line_bot_api, webhook_handler, claude, LINE_CHANNEL_ACCESS_TOKEN, now_taipei
from sheets import RequestContext, get_sheet
from prompt import get_user_name, get_style_instruction
from conversation import save_conversation, cleanup_conversation, ask_claude
from handlers.food import handle_add, handle_delete, handle_modify, handle_query
from handlers.todo import handle_add_todo, handle_modify_todo, handle_delete_todo, handle_query_todo
from handlers.device import (
    handle_control_ac, handle_control_ir, handle_query_sensor,
    handle_query_devices, handle_control_dehumidifier, handle_query_dehumidifier, handle_query_weather,
)
from handlers.schedule import handle_add_schedule, handle_delete_schedule, handle_query_schedule
from handlers.style import handle_set_style
from notify import router as notify_router
import switchbot_api

app = FastAPI()
app.include_router(notify_router)

# Web Dashboard REST API
from web_api import router as web_api_router
app.include_router(web_api_router)