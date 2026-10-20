"""One message → Claude → actions → the reply, as the old system did it (assistant.py).

How the reply is chosen, unchanged:
- An action that failed (❌), or a to-do completion/change that did not plainly succeed: the handlers'
  own words, never Claude's reply (Claude writes its reply before anything ran).
- Queries about food, to-dos, weather or a sensor: the results, phrased by a second Claude call.
- Device status, schedules, recurring to-dos, dehumidifier auto mode: the results as they are.
- Otherwise Claude's reply.
"""
from __future__ import annotations

import json
import logging
import re

from homebutler.assistant import actions, context
from homebutler.assistant.prompt import ARG_KEY_TYPES, SCHEDULE_CMD_KEYS, SYSTEM_PROMPT
from homebutler.domain import conversation

log = logging.getLogger("homebutler.assistant")

SEMANTIC = {"query_weather", "query_sensor", "query_food", "query_todo"}
REALTIME = {"query_devices", "query_dehumidifier", "set_dehumidifier_auto", "query_schedule", "query_recurring_todo"}
TRUTHFUL = {"delete_todo", "modify_todo"}
WEEKDAY = "一二三四五六日"
NOT_UNDERSTOOD = "抱歉，我沒有理解您的意思，可以再說一次嗎？"


def coerce(key: str, value):
    """The schema's string values back to the types the handlers expect; unconvertible stays a string."""
    kind = ARG_KEY_TYPES.get(key, "str")
    if not isinstance(value, str):
        return value
    try:
        if kind == "int":
            return int(float(value))
        if kind == "num":
            number = float(value)
            return int(number) if number.is_integer() else number
        if kind == "bool":
            return value.strip().lower() in ("true", "1", "yes", "是")
        if kind == "intlist":
            return [int(x) for x in re.split(r"[^0-9]+", value) if x]
    except ValueError:
        return value
    return value


def flatten(entry) -> dict:
    """{"action", "args": [{key, value}]} → a plain dict; the scheduled command's arguments become params."""
    if not isinstance(entry, dict):
        return {}
    args = entry.get("args")
    if not isinstance(args, list):
        return entry
    data = {k: v for k, v in entry.items() if k != "args"}
    for pair in args:
        if not isinstance(pair, dict) or not pair.get("key"):
            continue
        value = pair.get("value", "")
        if isinstance(value, str) and not value.strip():
            continue
        data[pair["key"]] = coerce(pair["key"], value)
    if data.get("action") == "add_schedule":
        data["params"] = {k: data.pop(k) for k in SCHEDULE_CMD_KEYS if k in data}
    elif data.get("action") == "modify_schedule":
        params = {k: data.pop(k) for k in SCHEDULE_CMD_KEYS if k in data}
        if params:
            data["params_new"] = params
    return data


def _parse(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return raw                       # prose: shown as it is
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return None


class Butler:
    def __init__(self, services: actions.Services, claude, version=lambda: "未知"):
        self.services, self.claude, self.version = services, claude, version

    def _today(self, turn) -> str:
        local = turn.local
        return f"{local.strftime('%Y-%m-%d')}（{WEEKDAY[local.weekday()]}）"

    def system_prompt(self, turn) -> tuple[str, str, list[dict]]:
        extra = self.services.extra_actions.get("_devices")
        state = self.services.link.state()
        with self.services.store.transaction() as s:
            member = turn.member(s)
            style = context.style(member)
            prompt = SYSTEM_PROMPT.format(
                today=self._today(turn), now_time=turn.local.strftime("%H:%M"), family_info=context.family(s),
                food_info=context.food_info(s, member), todo_info=context.todo_info(s, member),
                device_info=context.device_info(s, state, extra() if extra else []),
                lighting_info=context.lighting_info(s, member), schedule_info=context.schedule_info(s, self.services.zone),
                current_user=member.name, user_style=style, app_version=self.version())
            history = conversation.recent(s, member)
        return prompt, style, history

    def handle(self, turn: actions.Turn, text: str) -> str:
        """Never raises for a model or handler problem; the reply is always something to show."""
        prompt, style, history = self.system_prompt(turn)
        raw = self.claude.parse(prompt, history + [{"role": "user", "content": text}])
        if not raw:
            return NOT_UNDERSTOOD
        parsed = _parse(raw)
        if parsed is None:
            return "抱歉，系統處理時發生了一點問題，請再試一次。"
        if isinstance(parsed, str):
            return parsed
        planned = parsed if isinstance(parsed, list) else parsed.get("actions", [])
        reply = "" if isinstance(parsed, list) else str(parsed.get("reply", ""))
        planned = [flatten(a) for a in planned if isinstance(a, dict)]
        results, unexpected = [], []
        for data in planned:
            run = actions.handler(self.services, data.get("action", ""))
            if run is None:
                continue                     # an invented action is skipped, never fatal
            try:
                result = run(turn, data)
            except Exception:
                log.exception("action %s failed", data.get("action"))
                result = "❌ 處理時發生錯誤，請稍後再試"
            if result is not None:
                results.append(result)
                if data.get("action") in TRUTHFUL and not str(result).lstrip().startswith("✅"):
                    unexpected.append(result)
        log.info("butler results: %s", results)
        kinds = {d.get("action") for d in planned}
        if any("❌" in r for r in results) or unexpected:
            return "\n".join(results)
        if kinds & SEMANTIC and not kinds & REALTIME:
            data = "\n".join(r for r in results if "❌" not in r)
            if data:
                try:
                    return self.claude.phrase(text, data, kinds, style, self._today(turn)) or data
                except Exception:
                    log.exception("phrasing failed")
                    return data
            return reply or "\n".join(results)
        if kinds & REALTIME:
            return "\n".join(results)
        return reply or "\n".join(results) or NOT_UNDERSTOOD
