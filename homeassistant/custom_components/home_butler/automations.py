"""Opt-in, read-only report of this home's automations and their recent runs.

Nothing here can enable, disable, edit or trigger an automation. Every value is checked
against what the backend accepts: a single rejected snapshot would drop the whole link,
and AC control rides on that link.
"""
from collections import deque
import math
import re

ENTITY = re.compile(r"automation\.[a-z0-9_]+")
MAX_RULES, MAX_RUNS = 200, 30


def _seconds(value):
    try:
        seconds = float(value.timestamp())
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


class AutomationLog:
    def __init__(self, hass):
        self.hass = hass
        # Kept in memory only: a restart of HA starts the list again, while each rule's
        # own "last triggered" comes back from HA's restored state.
        self.runs = deque(maxlen=MAX_RUNS)

    def record(self, event):
        """Remember one automation_triggered event; returns whether it was usable."""
        data = event.data if isinstance(getattr(event, "data", None), dict) else {}
        entity_id, name, at = data.get("entity_id"), str(data.get("name") or "")[:160], _seconds(event.time_fired)
        if not isinstance(entity_id, str) or not ENTITY.fullmatch(entity_id) or not name or at is None:
            return False
        self.runs.append({"entity_id": entity_id, "name": name, "at": at, "source": str(data.get("source") or "")[:200]})
        return True

    def snapshot(self):
        rules = []
        for state in sorted(self.hass.states.async_all("automation"), key=lambda item: item.entity_id):
            if len(rules) == MAX_RULES or not ENTITY.fullmatch(state.entity_id):
                continue
            rules.append({"entity_id": state.entity_id, "name": str(state.name or state.entity_id)[:160],
                          "enabled": state.state == "on",
                          "last_triggered": _seconds(state.attributes.get("last_triggered"))})
        return {"rules": rules, "runs": list(self.runs)}
