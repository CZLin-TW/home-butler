"""The auto rule, as plain functions (no database, no network).

A dehumidifier in auto mode follows one room sensor:
- While running, asymmetric hysteresis around the target: on when humidity ≥ target+2 for the whole
  wait, off when ≤ target−1 for the whole wait, and in between nothing changes (target 60 → 59…62).
- At the moment auto mode is switched on, one plain comparison decides at once: ≥ target on, below off.
- The target is fixed, or follows the sensor's time-of-day curve (智能居家「濕度控制規則」, e.g.
  `7=55, 23=60`: 55% from 07:00, 60% from 23:00 until 07:00). A curve that does not parse falls back to the
  fixed target, with the reason shown; it is never guessed.
- When the target changes (the curve passes a boundary), the waits start over.
- Or the target is a dew point (`dewpoint`, 17°C: above it the air feels muggy). Then the rule compares the
  room's dew point, not its relative humidity: the same air reads a higher humidity when the room is cooler,
  and a dehumidifier warms the room, which lowers the humidity it reads without removing any water. The band
  is 17.5 on / 16.7 off (°C), about the same as +2 % / −1 % at 25°C. The machine is told a fixed low target
  (`DEW_MACHINE_THRESHOLD`), so it never stops by itself and a changing room temperature never re-aligns it.
- Sensor silent for 6 checks (30 min): a warning; for 12 (60 min): auto mode ends and the machine is
  turned off.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime

ABOVE, BELOW = 2, 1
WARNING_TICKS, DISABLE_TICKS = 6, 12
THRESHOLD_MIN, THRESHOLD_MAX = 30, 80
CUSTOM = "custom"
DEWPOINT = "dewpoint"
SOURCES = ("fixed", CUSTOM, DEWPOINT)
DEW_TARGET, DEW_ABOVE, DEW_BELOW = 17.0, 0.5, 0.3
DEW_MACHINE_THRESHOLD = 50          # LG's own target 40: below any humidity a 17°C dew point gives up to 30°C
MAGNUS_A, MAGNUS_B = 17.62, 243.12


def dew_point(temperature, humidity) -> float:
    """The dew point (°C) of air at this temperature (°C) and relative humidity (%), Magnus formula."""
    gamma = math.log(max(float(humidity), 0.1) / 100) + MAGNUS_A * float(temperature) / (MAGNUS_B + float(temperature))
    return round(MAGNUS_B * gamma / (MAGNUS_A - gamma), 2)


def humidity_at(temperature, dew) -> float:
    """The relative humidity (%) that a dew point means at this temperature."""
    t, d = float(temperature), float(dew)
    return round(100 * math.exp(MAGNUS_A * d / (MAGNUS_B + d) - MAGNUS_A * t / (MAGNUS_B + t)), 1)


@dataclass(frozen=True)
class Target:
    machine: int            # the threshold the machine is set by (and that a change of re-aligns it)
    cut: float              # the plain comparison when auto mode is switched on
    on_at: float
    off_at: float
    dew: bool               # compared with the dew point (°C) instead of the humidity (%)


def target(fixed: int, source: str, curve_text, hour: int) -> tuple[Target, str]:
    """The target in effect, and a warning when the curve is unusable."""
    if source == DEWPOINT:
        return Target(DEW_MACHINE_THRESHOLD, DEW_TARGET, DEW_TARGET + DEW_ABOVE, round(DEW_TARGET - DEW_BELOW, 1), True), ""
    threshold, warning = effective_threshold(fixed, source, curve_text, hour)
    return Target(threshold, threshold, threshold + ABOVE, threshold - BELOW, False), warning


def reading(goal: Target, humidity, temperature) -> float | None:
    """What the target is compared with; None when the sensor does not give it."""
    if humidity is None:
        return None
    if goal.dew:
        return None if temperature is None else dew_point(temperature, humidity)
    return float(humidity)


def parse_curve(text) -> tuple[list[tuple[int, int]] | None, str]:
    """`7=55, 23=60` → ([(7, 55), (23, 60)], ""); anything wrong → (None, why)."""
    raw = str(text or "").strip()
    if not raw:
        return None, "未設定"
    segments, seen = [], set()
    for part in re.split(r"[,，]", raw):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d{1,2})\s*=\s*(\d{1,3})", part)
        if not match:
            return None, f"片段格式錯誤「{part}」（應為「小時=濕度」，例如 7=55）"
        hour, value = int(match.group(1)), int(match.group(2))
        if not 0 <= hour <= 23:
            return None, f"小時超出範圍：{hour}（需 0~23）"
        if not THRESHOLD_MIN <= value <= THRESHOLD_MAX:
            return None, f"濕度超出範圍：{value}（需 {THRESHOLD_MIN}~{THRESHOLD_MAX}）"
        if hour in seen:
            return None, f"小時重複：{hour}"
        seen.add(hour)
        segments.append((hour, value))
    if not segments:
        return None, "未設定"
    return sorted(segments), ""


def at_hour(segments: list[tuple[int, int]], hour: int) -> int:
    """Before the first segment the last one still applies (it runs past midnight)."""
    chosen = segments[-1][1]
    for start, value in segments:
        if start > hour:
            break
        chosen = value
    return chosen


def effective_threshold(fixed: int, source: str, curve_text, hour: int) -> tuple[int, str]:
    """(target now, warning). A warning means the curve is unusable and the fixed target is used."""
    if source != CUSTOM:
        return fixed, ""
    segments, error = parse_curve(curve_text)
    if not segments:
        return fixed, f"自訂濕度曲線無效（{error}），暫用固定 {fixed}%"
    return at_hour(segments, hour), ""


def toggle_on(cut: float, value: float, power_on: bool | None) -> str | None:
    """The immediate decision when auto mode is switched on: "on", "off" or None (already right)."""
    if float(value) >= cut:
        return "on" if power_on is False else None
    return "off" if power_on else None


@dataclass
class Step:
    fire: str | None              # "on" | "off" | None
    phase: str                    # idle_dry | idle_humid | armed_above | armed_below
    countdown_min: int | None
    above_since: datetime | None
    below_since: datetime | None


def steady(on_at: float, off_at: float, duration_min: int, value: float, power_on: bool, now: datetime,
           above_since: datetime | None, below_since: datetime | None) -> Step:
    wait = duration_min * 60
    value = float(value)
    if value >= on_at:
        above_since = above_since or now
        if power_on:
            return Step(None, "idle_humid", None, above_since, None)
        elapsed = (now - above_since).total_seconds()
        if elapsed >= wait:
            return Step("on", "idle_humid", None, None, None)
        return Step(None, "armed_above", int((wait - elapsed) // 60), above_since, None)
    if value <= off_at:
        below_since = below_since or now
        if not power_on:
            return Step(None, "idle_dry", None, None, below_since)
        elapsed = (now - below_since).total_seconds()
        if elapsed >= wait:
            return Step("off", "idle_dry", None, None, None)
        return Step(None, "armed_below", int((wait - elapsed) // 60), None, below_since)
    return Step(None, "idle_humid" if power_on else "idle_dry", None, None, None)


def phase_now(on_at: float, off_at: float, value: float | None, power_on: bool | None) -> str:
    """A best guess right after a change of the rule; the next check sets the real one."""
    if value is None:
        return "sensor_lost_warning"
    if float(value) >= on_at:
        return "idle_humid" if power_on else "armed_above"
    if float(value) <= off_at:
        return "armed_below" if power_on else "idle_dry"
    return "idle_humid" if power_on else "idle_dry"
