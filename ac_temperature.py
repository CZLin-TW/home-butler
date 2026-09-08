"""Comfort targets use half degrees; SwitchBot IR always uses whole degrees."""
import math


def comfort_temperature(value):
    if isinstance(value, bool):
        raise ValueError("空調溫度需為 16 至 30°C，間隔 0.5°C")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("空調溫度無效") from None
    if not math.isfinite(number) or not 16 <= number <= 30 or not (number * 2).is_integer():
        raise ValueError("空調溫度需為 16 至 30°C，間隔 0.5°C")
    return int(number) if number.is_integer() else number


def ir_temperature(value):
    # Explicit half-up: Python round(26.5) would incorrectly produce 26.
    return math.floor(comfort_temperature(value) + 0.5)


def target_for(value, row):
    from ac_feedback import config_for
    target = comfort_temperature(value)
    return target if config_for(row)["enabled"] else ir_temperature(target)
