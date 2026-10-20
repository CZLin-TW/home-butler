"""What happens to a LINE message, in this order (as before):

1. Only an active family member, by the sender's LINE user id, gets anything; for anyone else the id is
   logged (so the administrator can add them) and nothing else happens. Groups count by the sender too.
2. A six-digit code (optionally after 登入, spaces or dashes allowed, full-width digits too) approves a
   Dashboard sign-in, without Claude.
3. `@all <message>` goes to every active member.
4. 查看風格／我的風格／目前風格 shows the member's butler style, without Claude.
5. Anything else goes to the butler; the reply is sent, then the turn is remembered and any notices
   (for example to someone a to-do was assigned to) are pushed.
"""
from __future__ import annotations

import logging
import re

from homebutler.assistant.actions import Turn
from homebutler.domain import conversation, members, pairing
from homebutler.store.models import Member

log = logging.getLogger("homebutler.line")

STYLE_WORDS = {"查看風格", "我的風格", "目前風格"}


def login_code(text: str) -> str | None:
    stripped = text.strip()
    source = stripped[2:] if stripped.startswith("登入") else stripped if re.fullmatch(r"\d(?:[\s\-]*\d){5}", stripped) else ""
    digits = re.sub(r"\D", "", source)
    return str(int(digits)).zfill(6) if len(digits) == 6 else None


class LineBot:
    def __init__(self, butler, line):
        self.butler, self.line = butler, line

    @property
    def store(self):
        return self.butler.services.store

    def message(self, line_user_id: str | None, reply_token: str, text: str) -> None:
        now = self.butler.services.clock()
        with self.store.transaction() as s:
            member = members.active_by_line_id(s, line_user_id) if line_user_id else None
            if member is None:
                log.info("LINE denied user_id=%s", line_user_id)
                reply = "❌ 僅限已啟用的家庭成員使用。"
            else:
                member_id, name, style = member.id, member.name, member.butler_style.strip()
        if member is None:
            self.line.reply(reply_token, reply)
            return
        code = login_code(text)
        if code:
            picture = self.line.picture(line_user_id)
            with self.store.transaction() as s:
                approved = pairing.approve(s, code, s.get(Member, member_id), picture, now)
            self.line.reply(reply_token, f"✅ 已授權登入 Dashboard（以 {name} 的身分），回到網頁就會自動進入 🏠" if approved
                            else "❌ 驗證碼錯誤或已過期，請回 Dashboard 重新取得一組。")
            return
        if text.strip().startswith("@all"):
            self._broadcast(reply_token, name, text.strip()[4:].strip(), now)
            return
        if text.strip() in STYLE_WORDS:
            self.line.reply(reply_token, f"📝 您目前的自訂風格：\n{style}" if style else "📝 您目前沒有自訂風格，使用預設管家風格。")
            return
        self.line.loading(line_user_id)
        turn = Turn(self.butler.services, member_id, name)
        try:
            reply = self.butler.handle(turn, text)
        except Exception:
            log.exception("butler turn failed")
            reply = "抱歉，系統暫時出了點問題，請稍後再試。"
        self.line.reply(reply_token, reply)
        with self.store.transaction() as s:
            who = s.get(Member, member_id)
            conversation.save(s, who, "user", text, now)
            conversation.save(s, who, "assistant", reply, self.butler.services.clock())
        for target, notice in turn.notices:
            try:
                self.line.push(target, notice)
            except Exception:
                log.warning("assignment notice not delivered")

    def _broadcast(self, reply_token: str, sender: str, text: str, now) -> None:
        if not text:
            self.line.reply(reply_token, "請在 @all 後面輸入廣播內容")
            return
        message = f"📢 {sender}：{text}"
        with self.store.transaction() as s:
            everyone = [(m.id, m.line_user_id) for m in members.active(s) if m.line_user_id]
        for member_id, target in everyone:
            try:
                self.line.push(target, message)
            except Exception:
                log.warning("broadcast to one member not delivered")
                continue
            with self.store.transaction() as s:
                conversation.save(s, s.get(Member, member_id), "assistant", message, now)
        self.line.reply(reply_token, "✅ 已廣播給全體成員")
