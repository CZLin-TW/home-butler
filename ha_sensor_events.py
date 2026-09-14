"""Coalesce authenticated HA light changes into one bounded automation worker.

No extra sensor polling. Re-read the selected live snapshot just before rule
evaluation; unavailable/disconnected HA never uses a saved webhook level.
"""
import asyncio

_last = {}
_pending = set()
_task = None


def notify():
    global _task
    import device_status
    import ha_sensors
    current = {}
    for row in device_status.catalog_rows():
        if row.get("類型") == "感應器" and ha_sensors.managed(row.get("名稱")) and row.get("Device ID"):
            current[row["Device ID"]] = ha_sensors.reading(row).get("light_level")
    for device_id, value in current.items():
        if value is not None and value != _last.get(device_id):
            _pending.add(device_id)
    _last.clear()
    _last.update(current)
    if _pending and (_task is None or _task.done()):
        _task = asyncio.create_task(_drain())


async def _drain():
    import lighting_auto
    while _pending:
        device_id = _pending.pop()
        try:
            await asyncio.to_thread(lighting_auto.on_light_report, device_id, None, ha_source=True)
        except Exception:
            pass  # Normal lighting worker retries at its next scheduled evaluation.
