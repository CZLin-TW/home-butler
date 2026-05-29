"""Shared service for configuring dehumidifier auto mode.

Dashboard REST API and LINE/Siri natural-language actions both use this layer
so sensor lookup, immediate evaluation, and rule writes stay consistent.
"""

import unicodedata

import dehumidifier_auto
import dehumidifier_driver
import sensor_state


def _norm(value):
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    return text.replace(" ", "").replace("　", "").replace("溼", "濕").replace("卧", "臥")


def _same_location(a, b):
    left = _norm(a)
    right = _norm(b)
    return bool(left and right and (left == right or left in right or right in left))


def _is_sensor(row):
    return row.get("狀態") == "啟用" and row.get("類型") in ("感應器", "感測器")


def _enabled_dehumidifiers(ctx):
    return [
        r for r in ctx.get("智能居家")
        if r.get("狀態") == "啟用" and r.get("類型") == "除濕機"
    ]


def _enabled_sensors(ctx):
    return [r for r in ctx.get("智能居家") if _is_sensor(r)]


def get_dehumidifier_row(ctx, device_name):
    target = _norm(device_name)
    for row in _enabled_dehumidifiers(ctx):
        if _norm(row.get("名稱")) == target:
            return row
    return None


def has_control_driver(device_row):
    """Return True when the dehumidifier row has enough data to control it."""
    return dehumidifier_driver.make_driver(device_row) is not None


def resolve_dehumidifier_targets(ctx, device_name="", scope="single"):
    """Resolve one or all target dehumidifiers from a natural-language action."""
    devices = _enabled_dehumidifiers(ctx)
    if not devices:
        return [], "❌ 找不到除濕機設備，請先在「智能居家」分頁設定"

    target = _norm(device_name)
    scope_norm = _norm(scope)
    if scope_norm in ("all", "全部", "全家", "所有") or target in ("全部", "全家", "所有"):
        return devices, None

    if target:
        exact = [d for d in devices if _norm(d.get("名稱")) == target]
        if len(exact) == 1:
            return exact, None

        fuzzy = [
            d for d in devices
            if target in _norm(d.get("名稱"))
            or target == _norm(d.get("位置"))
            or (_norm(d.get("位置")) and _norm(d.get("位置")) in target)
        ]
        if len(fuzzy) == 1:
            return fuzzy, None
        if len(fuzzy) > 1:
            names = "、".join(d.get("名稱", "") for d in fuzzy)
            return [], f"❌ 找到多台可能的除濕機（{names}），請指定設備名稱"
        return [], f"❌ 找不到除濕機「{device_name}」，請確認設備名稱"

    if len(devices) == 1:
        return devices, None
    names = "、".join(d.get("名稱", "") for d in devices)
    return [], f"❌ 有多台除濕機（{names}），請指定要設定哪一台，或說「全家除濕機」"


def choose_sensor_for_dehumidifier(ctx, device_row, sensor_name=None, snapshot=None):
    """Pick the best sensor for a dehumidifier.

    If sensor_name is provided, only that sensor is accepted. Otherwise the
    selection is limited to sensors in the same location as the dehumidifier.
    """
    sensors = _enabled_sensors(ctx)
    sensor_snapshot = snapshot if snapshot is not None else sensor_state.snapshot()
    target = _norm(sensor_name)

    if target:
        matches = [
            s for s in sensors
            if _norm(s.get("名稱")) == target or target in _norm(s.get("名稱"))
        ]
        if len(matches) == 1:
            return _sensor_choice(matches[0], sensor_snapshot), None
        if len(matches) > 1:
            names = "、".join(s.get("名稱", "") for s in matches)
            return None, f"找到多個可能的感測器（{names}），請指定完整名稱"
        return None, f"找不到感測器「{sensor_name}」"

    location = _norm(device_row.get("位置"))
    device_name = device_row.get("名稱", "")
    if not location:
        return None, f"{device_name} 未設定位置，無法自動配對同空間感測器"

    same_location = [s for s in sensors if _same_location(s.get("位置"), location)]
    if not same_location:
        return None, f"{device_name} 所在位置「{device_row.get('位置', '')}」沒有啟用中的感測器"

    def score(sensor):
        state = sensor_snapshot.get(sensor.get("名稱", ""), {})
        has_humidity = state.get("online", False) and state.get("current", {}).get("humidity") is not None
        name_has_location = location in _norm(sensor.get("名稱"))
        return (has_humidity, name_has_location, bool(sensor.get("Device ID")), sensor.get("名稱", ""))

    return _sensor_choice(sorted(same_location, key=score, reverse=True)[0], sensor_snapshot), None


def _sensor_choice(sensor_row, sensor_snapshot):
    name = sensor_row.get("名稱", "")
    state = sensor_snapshot.get(name, {})
    humidity = None
    if state.get("online", False):
        humidity = state.get("current", {}).get("humidity")
    return {
        "row": sensor_row,
        "name": name,
        "online": bool(state.get("online", False)),
        "humidity": humidity,
    }


def set_auto_rule(ctx, device_name, auto_mode, sensor_name=None,
                  duration_min=None, threshold=None, on_mode=None, snapshot=None):
    """Set one dehumidifier auto rule and preserve Dashboard API behavior."""
    sensor_humidity = None
    power_now = None
    driver = None
    device_row = None

    if auto_mode:
        device_row = get_dehumidifier_row(ctx, device_name)
        effective_sensor_name = sensor_name or dehumidifier_auto.get_all_rules().get(
            device_name, {}
        ).get("sensor_name", "")
        if effective_sensor_name:
            sensor_snapshot = snapshot if snapshot is not None else sensor_state.snapshot()
            sensor = sensor_snapshot.get(effective_sensor_name, {})
            if sensor.get("online", False):
                sensor_humidity = sensor.get("current", {}).get("humidity")

        if device_row is not None:
            driver = dehumidifier_driver.make_driver(device_row)
            if driver is not None:
                try:
                    status = driver.get_status()
                    if isinstance(status, dict) and "error" not in status:
                        power_now = driver.is_power_on(status)
                except Exception as e:
                    print(f"[dehum-auto service] status fetch error: {e}")

    rule = dehumidifier_auto.set_rule(
        device_name=device_name,
        auto_mode=auto_mode,
        sensor_name=sensor_name,
        duration_min=duration_min,
        threshold=threshold,
        on_mode=on_mode,
        sensor_humidity=sensor_humidity,
        power_now=power_now,
        driver=driver,
    )
    return {
        "rule": rule,
        "device_row": device_row,
        "driver_ready": driver is not None,
        "sensor_humidity": sensor_humidity,
        "power_now": power_now,
    }
