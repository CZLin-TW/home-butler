"""The auto rule, as plain functions (no database, no network).

A dehumidifier in auto mode follows one room sensor:
- While running, asymmetric hysteresis around the target: on when humidity ≥ target+2 for the whole
  wait, off when ≤ target−1 for the whole wait, and in between nothing changes (target 60 → 59…62).
- At the moment auto mode is switched on, one plain comparison decides at once: ≥ target on, below off.
- The target is fixed, or follows the sensor's time-of-day curve (智能居家「濕度控制規則」, e.g.
  `7=55, 23=60`: 55% from 07:00, 60% from 23:00 until 07:00). A curve that does not parse falls back to the
  fixed target, with the reason shown; it is never guessed.
- When the target changes (the curve passes a boundary), the waits start over.
- Sensor silent for 6 checks (30 min): a warning; for 12 (60 min): auto mode ends and the machine is
  turned off.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

ABOVE, BELOW = 2, 1
WARNING_TICKS, DISABLE_TICKS = 6, 12
THRESHOLD_MIN, THRESHOLD_MAX = 30, 80
CUSTOM = "custom"


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


def toggle_on(threshold: int, humidity: float, power_on: bool | None) -> str | None:
    """The immediate decision when auto mode is switched on: "on", "off" or None (already right)."""
    if float(humidity) >= threshold:
        return "on" if power_on is False else None
    return "off" if power_on else None


@dataclass
class Step:
    fire: str | None              # "on" | "off" | None
    phase: str                    # idle_dry | idle_humid | armed_above | armed_below
    countdown_min: int | None
    above_since: datetime | None
    below_since: datetime | None


def steady(threshold: int, duration_min: int, humidity: float, power_on: bool, now: datetime,
           above_since: datetime | None, below_since: datetime | None) -> Step:
    wait = duration_min * 60
    value = float(humidity)
    if value >= threshold + ABOVE:
        above_since = above_since or now
        if power_on:
            return Step(None, "idle_humid", None, above_since, None)
        elapsed = (now - above_since).total_seconds()
        if elapsed >= wait:
            return Step("on", "idle_humid", None, None, None)
        return Step(None, "armed_above", int((wait - elapsed) // 60), above_since, None)
    if value <= threshold - BELOW:
        below_since = below_since or now
        if not power_on:
            return Step(None, "idle_dry", None, None, below_since)
        elapsed = (now - below_since).total_seconds()
        if elapsed >= wait:
            return Step("off", "idle_dry", None, None, None)
        return Step(None, "armed_below", int((wait - elapsed) // 60), None, below_since)
    return Step(None, "idle_humid" if power_on else "idle_dry", None, None, None)


def phase_now(threshold: int, humidity: float | None, power_on: bool | None) -> str:
    """A best guess right after a change of the rule; the next check sets the real one."""
    if humidity is None:
        return "sensor_lost_warning"
    if float(humidity) >= threshold + ABOVE:
        return "idle_humid" if power_on else "armed_above"
    if float(humidity) <= threshold - BELOW:
        return "armed_below" if power_on else "idle_dry"
    return "idle_humid" if power_on else "idle_dry"
