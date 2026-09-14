"""HB decides which todos are due; HA executes only the selected Hue target.

Preserves one breathe per area per minute. The legacy agent endpoint is empty
while HA owns Hue, avoiding two reminder senders. No private todo content goes
to HA: only the group UUID and the fixed breathe command.
"""
import time
from lighting_transport import ha_enabled

_attempted = {}


def tick():
    if not ha_enabled():
        return
    from home_assistant_api import link
    if not link.snapshot()["hue_available"]:
        return
    from web_api import collect_todo_light_reminders
    from lighting_transport import send_command_sync
    reminders = collect_todo_light_reminders()["reminders"]
    targets = {r["light_area_id"] for r in reminders if r.get("light_area_id")
               and r.get("light_area_resource_type") == "grouped_light"}
    minute = int(time.time() // 60)
    for target in targets:
        if _attempted.get(target) == minute:
            continue
        _attempted[target] = minute  # An unknown result cannot be replayed in this minute.
        try:
            send_command_sync("hue.breathe", {"resource_id": target, "resource_type": "grouped_light"})
        except Exception:
            print("[Hue reminder] HA result unavailable; no immediate retry")
    for key in list(_attempted):
        if key not in targets:
            _attempted.pop(key, None)
