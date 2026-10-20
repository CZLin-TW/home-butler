"""LINE Messaging API: check a webhook's signature, reply, push, a member's picture, the typing dots.

Plain HTTPS calls (no SDK). Reply and push raise when LINE did not accept the message, so callers can
record "sent" only after it really went out.
"""
from __future__ import annotations

import base64
import hashlib
import hmac

API = "https://api.line.me/v2/bot"


def signature_ok(secret: str, body: bytes, signature: str) -> bool:
    if not secret or not signature:
        return False
    expected = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, signature)


class LineError(RuntimeError):
    pass


class Line:
    def __init__(self, token: str, http):
        self.token, self.http = token, http

    def _post(self, path: str, body: dict) -> None:
        response = self.http.post(API + path, headers={"Authorization": f"Bearer {self.token}"}, json=body, timeout=15)
        if response.status_code != 200:
            raise LineError(f"LINE {path}: HTTP {response.status_code}")

    @staticmethod
    def _messages(text: str) -> list[dict]:
        return [{"type": "text", "text": text[:5000]}]

    def reply(self, reply_token: str, text: str) -> None:
        self._post("/message/reply", {"replyToken": reply_token, "messages": self._messages(text)})

    def push(self, line_user_id: str, text: str) -> None:
        """The Messenger port."""
        self._post("/message/push", {"to": line_user_id, "messages": self._messages(text)})

    def loading(self, line_user_id: str) -> None:
        try:
            self._post("/chat/loading/start", {"chatId": line_user_id, "loadingSeconds": 60})
        except Exception:
            pass                              # only the typing dots

    def picture(self, line_user_id: str) -> str:
        try:
            response = self.http.get(f"{API}/profile/{line_user_id}", headers={"Authorization": f"Bearer {self.token}"}, timeout=10)
            return str(response.json().get("pictureUrl") or "") if response.status_code == 200 else ""
        except Exception:
            return ""
