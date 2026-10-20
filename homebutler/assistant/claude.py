"""The three Claude calls, as in the old system (conversation.py):

- `parse`: the member's message → {"actions": [...], "reply": ...}, with adaptive thinking and the answer
  forced into ACTION_SCHEMA (constrained decoding), so it can never come back as prose. If the API ever
  refuses the schema, it falls back to no schema and no thinking, and says so in the log.
- `phrase`: turns query results into a natural reply (to-dos by date, food by expiry, other short).
- `notify`: the daily summary message.
"""
from __future__ import annotations

import logging

from homebutler.assistant.prompt import (ACTION_SCHEMA, DEFAULT_STYLE, SEMANTIC_DEFAULT_PROMPT, SEMANTIC_FOOD_PROMPT,
                                         SEMANTIC_TODO_PROMPT)

log = logging.getLogger("homebutler.assistant")


def _text(response) -> str:
    return "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()


class Claude:
    def __init__(self, model: str, client=None, api_key: str = ""):
        self.model, self._client, self.api_key = model, client, api_key

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def parse(self, system: str, messages: list[dict]) -> str:
        import anthropic
        try:
            response = self.client.messages.create(model=self.model, max_tokens=4000, thinking={"type": "adaptive"},
                                                   output_config={"format": {"type": "json_schema", "schema": ACTION_SCHEMA}},
                                                   system=system, messages=messages)
        except anthropic.BadRequestError as error:
            log.warning("structured outputs refused by the API, falling back to no schema: %s", error)
            response = self.client.messages.create(model=self.model, max_tokens=2000, thinking={"type": "disabled"},
                                                   system=system, messages=messages)
        text = _text(response)
        if text.startswith("```"):
            text = text.split("```")[1]
            text = text[4:] if text.startswith("json") else text
        return text.strip()

    def phrase(self, question: str, data: str, actions: set, style: str, today: str) -> str:
        if "query_todo" in actions:
            system, budget = SEMANTIC_TODO_PROMPT.format(today=today) + style, 1500
        elif "query_food" in actions:
            system, budget = SEMANTIC_FOOD_PROMPT.format(today=today) + style, 1500
        else:
            system, budget = SEMANTIC_DEFAULT_PROMPT + style, 1200
        response = self.client.messages.create(model=self.model, max_tokens=budget, system=system,
                                               messages=[{"role": "user", "content": f"使用者問：{question}\n\n數據：\n{data}"}])
        return _text(response)

    def notify(self, summary: str, now_text: str, style: str = "") -> str | None:
        system = (f"你負責管理家庭的食品庫存、待辦事項和智能居家設備。現在是 {now_text}。請根據以下資料整理成一則推播訊息。"
                  "主動補充貼心提醒（快過期的催促、今天的待辦提醒注意時間、天氣提醒帶傘或注意溫差等）。"
                  "不要加開頭問候語如「早安」，直接進入內容。只回傳推播文字，不要 JSON。" + (style or f"\n{DEFAULT_STYLE}"))
        try:
            return _text(self.client.messages.create(model=self.model, max_tokens=1500, system=system,
                                                     messages=[{"role": "user", "content": summary}]))
        except Exception as error:
            log.warning("daily summary wording failed (%s)", type(error).__name__)
            return None
