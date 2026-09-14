"""The single serialization point for air-conditioner commands.

Every AC entry point — LINE/Siri handlers, the Dashboard API, scheduled
dispatch and the Homebridge bridge — takes this lock, so two callers can
never interleave a read-modify-write of the same unit's last-state row.

It lives in its own module because it is shared by callers that must not
import one another. It used to sit inside the temperature-feedback module,
which made that module impossible to remove without touching every caller.

It also holds the target-temperature rule, so callers that must stay free of
the handler package (the device-only voice entry) can validate without it.
"""
import math
from threading import RLock

CONTROL_LOCK = RLock()


def ac_temperature(value):
    """空調目標溫度：整數 16–30°C。

    語音與舊排程 payload 仍可能送半度（ARG_KEY_TYPES.temperature 是 num），
    half-up 收整而不是拒絕，避免「26.5 度」這種說法整句失敗。
    """
    if isinstance(value, bool):
        raise ValueError("空調溫度需為 16 至 30°C 的整數")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("空調溫度無效") from None
    if not math.isfinite(number):
        raise ValueError("空調溫度無效")
    # 語音可能說「26.5 度」，所以半度收得下來（half-up）；其餘小數視為輸入錯誤而拒絕，
    # 不靜悄悄把 26.2 變成 26。round(26.5) 會給 26，所以明確用 floor(x + 0.5)。
    if not 16 <= number <= 30 or not (number * 2).is_integer():
        raise ValueError("空調溫度需為 16 至 30°C 的整數")
    return math.floor(number + 0.5)
