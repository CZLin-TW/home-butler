"""Shared sensor reads; live AC feedback never writes chart history or Sheets."""
import time
from threading import Lock

_guard = Lock()
_locks = {}
_attempts = {}
_results = {}
READ_INTERVAL_S = 60


def refresh(row):
    """Serialize per physical device and reuse attempts within each minute slot.

    Failed attempts are throttled too, without refreshing the old sample's age.
    Compensation is applied once per successful cloud read.
    """
    import switchbot_api
    import sensor_state
    import device_status
    from handlers.device import apply_sensor_compensation
    id, name = row.get("Device ID"), row.get("名稱")
    if not id or not name or row.get("類型") != "感應器" or row.get("狀態") != "啟用":
        return {"error": "invalid sensor"}
    with _guard:
        lock = _locks.setdefault(id, Lock())
    with lock:
        # Fixed slots avoid a slightly early scheduler tick reusing a 59.99s-old
        # attempt and accidentally turning minute polling into two-minute polling.
        slot = int(time.monotonic() // READ_INTERVAL_S)
        if _attempts.get(id) == slot:
            return dict(_results[id])
        try:
            result = switchbot_api.get_hub_sensor(id)
            if not isinstance(result, dict) or "error" in result:
                result = {"error": "sensor read failed"}
            else:
                temp, humidity = apply_sensor_compensation(result.get("temperature"), result.get("humidity"), row)
                co2 = result.get("co2")
                if temp is None and humidity is None and co2 is None:
                    result = {"error": "empty sensor reading"}
                else:
                    sensor_state.update_current(name, row.get("位置", ""), temp, humidity, co2)
                    device_status.update(name, {"temperature": temp, "humidity": humidity})
                    result = {"temperature": temp, "humidity": humidity, "co2": co2}
        except Exception as error:
            print(f"[sensor poll] read failed: {type(error).__name__}")
            result = {"error": "sensor read failed"}
        _results[id] = result
        _attempts[id] = slot
        return dict(result)


def feedback_sensors(rows, statuses):
    """Choose unique enabled sensors used by powered cooling/heating ACs."""
    from ac_feedback import config_for
    selected = {}
    for ac in rows:
        if ac.get("類型") != "空調" or ac.get("狀態") != "啟用":
            continue
        cfg = config_for(ac)
        latest = statuses.get(ac.get("名稱"), {})
        if (not cfg["enabled"] or latest.get("stateUncertain")
                or latest.get("lastPower", ac.get("最後電源")) != "on"
                or latest.get("lastMode", ac.get("最後模式")) not in ("冷氣", "暖氣")):
            continue
        matches = [r for r in rows if r.get("名稱") == cfg["sensor_name"]]
        if len(matches) != 1:
            continue
        sensor = matches[0]
        id = sensor.get("Device ID")
        if (sensor.get("類型") == "感應器" and sensor.get("狀態") == "啟用" and id
                and ac.get("位置") and sensor.get("位置") == ac["位置"]
                and sum(r.get("Device ID") == id for r in rows) == 1):
            selected[id] = sensor
    return list(selected.values())


def poll_feedback():
    import device_status
    for sensor in feedback_sensors(device_status.catalog_rows(), device_status.snapshot()):
        refresh(sensor)


def feedback_tick():
    import ac_feedback
    poll_feedback()
    ac_feedback.tick()  # Evaluate after current readings; retain all controller guards.
