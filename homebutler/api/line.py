"""LINE's webhook. The signature is checked on the raw body; then the request is answered at once and the
messages are handled one at a time in the background (the reply token stays valid for that long)."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from homebutler.integrations.line import signature_ok

log = logging.getLogger("homebutler.line")
router = APIRouter()
MAX_BODY = 1_000_000


@router.post("/v2/line/webhook")
async def webhook(request: Request):
    bot = getattr(request.app.state, "line_bot", None)
    body = await request.body()
    if bot is None or len(body) > MAX_BODY or not signature_ok(request.app.state.settings.line_channel_secret, body,
                                                               request.headers.get("X-Line-Signature", "")):
        raise HTTPException(400, "bad signature")
    try:
        events = json.loads(body).get("events", [])
    except (ValueError, AttributeError):
        raise HTTPException(400, "bad body") from None
    lock = request.app.state.line_lock
    for event in events:
        message = event.get("message") if isinstance(event, dict) else None
        if event.get("type") != "message" or not isinstance(message, dict) or message.get("type") != "text":
            continue
        user = (event.get("source") or {}).get("userId")
        token, text = event.get("replyToken", ""), str(message.get("text", ""))

        async def handle(user=user, token=token, text=text):
            async with lock:
                try:
                    await run_in_threadpool(bot.message, user, token, text)
                except Exception:
                    log.exception("LINE message not handled")
        request.app.state.background.add(task := asyncio.create_task(handle()))
        task.add_done_callback(request.app.state.background.discard)
    return {"ok": True}
