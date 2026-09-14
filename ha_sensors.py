"""HA is the sole live source for explicitly migrated sensor names.

No I/O on reads, no direct-cloud fallback, no history writes. Sheet calibration
is applied once to each projection of raw HA values, never to a cached result.
"""
import json
import os


def names():
    values = json.loads(os.environ.get("HOME_ASSISTANT_SENSOR_NAMES", "[]"))
    if (not isinstance(values, list) or len(values) > 20
            or any(not isinstance(n, str) or not n.strip() or n != n.strip() or len(n) > 160 for n in values)
            or len(set(values)) != len(values)):
        raise ValueError("Invalid HOME_ASSISTANT_SENSOR_NAMES")
    return values


def managed(name):
    try:
        return name in names()
    except (ValueError, TypeError):
        return True  # A broken migration setting must not restart cloud polling.


def reading(row):
    from home_assistant_api import link
    from handlers.device import apply_sensor_compensation
    snapshot = link.snapshot()
    values = {s["kind"]: s["value"] for s in snapshot.get("environment", [])
              if s["name"] == row.get("名稱") and s["available"]}
    temp, humidity = apply_sensor_compensation(values.get("temperature"), values.get("humidity"), row)
    return {"temperature": temp, "humidity": humidity, "co2": values.get("co2"),
            "light_level": values.get("light_level"), "source": "home_assistant",
            "received_at": snapshot["received_at"], "age_seconds": snapshot["age_seconds"],
            "available": bool(values)}


def by_device_id(device_id):
    """None means unmigrated; mapped-but-unavailable is an explicit null reading."""
    import device_status
    normalize = lambda value: str(value or "").replace(":", "").replace("-", "").upper()
    matches = [r for r in device_status.catalog_rows()
               if normalize(r.get("Device ID")) == normalize(device_id) and r.get("類型") == "感應器"]
    if not any(managed(r.get("名稱")) for r in matches):
        return None
    if len(matches) != 1:
        return {"light_level": None, "source": "home_assistant", "age_seconds": None}
    return reading(matches[0])


def overlay(out, name=""):
    import device_status
    rows = device_status.catalog_rows()
    for row in rows:
        sensor = row.get("名稱")
        if (row.get("類型") != "感應器" or not managed(sensor) or (name and name != sensor)):
            continue
        value = reading(row) if sum(r.get("名稱") == sensor for r in rows) == 1 else {}
        old = out.get(sensor, {})
        out[sensor] = {**old, "device_name": sensor, "location": row.get("位置", ""),
                       "history": old.get("history", []), "source": "home_assistant",
                       "current": {"t": value.get("received_at") or 0, "temp": value.get("temperature"),
                                   "humidity": value.get("humidity"), "co2": value.get("co2"),
                                   "light_level": value.get("light_level")},
                       "last_polled_at": value.get("received_at") or 0,
                       "online": bool(value.get("available"))}
    return out
