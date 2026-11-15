"""The auto rule, as plain functions (no database, no network).

A dehumidifier in auto mode follows one room sensor against a target humidity (%), which is either:
- fixed: one number chosen on the card; or
- dewpoint: the lower of two lines, one for how the air feels and one for what grows on the walls.
  * A dew point (°C), one for the day and one for the night (the night one may be None: no dew-point line
    at night, e.g. for quiet), turned into the humidity it means at the room's temperature now. Relative
    humidity moves with the temperature (the same air reads higher in a cooler room, and a running
    dehumidifier warms the room and lowers the reading without removing water); the dew point is the
    water itself, which is what feels muggy.
  * A humidity ceiling against mould and dust mites, whatever the hour. Walls colder than the room hold
    a higher humidity than the room's air, so in winter this is the line that acts.
  Without a temperature only the ceiling applies.
- While running, asymmetric hysteresis around the target: on when humidity ≥ target+2 for the whole
  wait, off when ≤ target−1 for the whole wait, and in between nothing changes (target 60 → 59…62).
- At the moment auto mode is switched on, one plain comparison decides at once: ≥ target on, below off.
- A new fixed target restarts the waits. A dew-point target moves with the temperature and the hour and
  restarts nothing; the machine is then told a fixed low target (`DEW_MACHINE_THRESHOLD`), so it never
  stops by itself and is never re-aligned.
- Sensor silent for 6 checks (30 min): a warning; for 12 (60 min): auto mode ends and the machine is
  turned off.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

ABOVE, BELOW = 2, 1
WARNING_TICKS, DISABLE_TICKS = 6, 12
THRESHOLD_MIN, THRESHOLD_MAX = 30, 80
FIXED, DEWPOINT = "fixed", "dewpoint"
SOURCES = (FIXED, DEWPOINT)
DEW_MIN, DEW_MAX = 15, 21
CAP_MIN, CAP_MAX = 50, 80
DEFAULTS = {"dew_day": 18, "dew_night": 19, "night_start": 23, "night_end": 7, "rh_cap": 65}
DEW_MACHINE_THRESHOLD = 50          # LG's own target 40: below any target the dew-point mode can give up to 30°C
MAGNUS_A, MAGNUS_B = 17.62, 243.12


def dew_point(temperature, humidity) -> float:
    """The dew point (°C) of air at this temperature (°C) and relative humidity (%), Magnus formula."""
    gamma = math.log(max(float(humidity), 0.1) / 100) + MAGNUS_A * float(temperature) / (MAGNUS_B + float(temperature))
    return round(MAGNUS_B * gamma / (MAGNUS_A - gamma), 2)


def humidity_at(temperature, dew) -> float:
    """The relative humidity (%) that a dew point means at this temperature."""
    t, d = float(temperature), float(dew)
    return round(100 * math.exp(MAGNUS_A * d / (MAGNUS_B + d) - MAGNUS_A * t / (MAGNUS_B + t)), 1)


def is_night(hour: int, start: int, end: int) -> bool:
    """From `start` o'clock up to (not including) `end`, across midnight when start > end; never when equal."""
    if start == end:
        return False
    return start <= hour < end if start < end else hour >= start or hour < end


@dataclass(frozen=True)
class DewRule:
    dew_day: int
    dew_night: int | None
    night_start: int
    night_end: int
    rh_cap: int


@dataclass(frozen=True)
class Goal:
    humidity: float         # the target (%) now
    basis: str              # fixed | day | night | cap: which line sets it
    dew: int | None         # the dew point in effect (dew-point mode), even when the ceiling is lower
    machine: int            # the threshold the machine is set by (a change of it re-aligns the machine)
    no_temperature: bool = False


def goal(source: str, fixed: int, dew_rule: DewRule, temperature, hour: int) -> Goal:
    if source != DEWPOINT:
        return Goal(float(fixed), "fixed", None, fixed)
    night = is_night(hour, dew_rule.night_start, dew_rule.night_end)
    dew = dew_rule.dew_night if night else dew_rule.dew_day
    cap = Goal(float(dew_rule.rh_cap), "cap", dew, DEW_MACHINE_THRESHOLD, no_temperature=dew is not None and temperature is None)
    if dew is None or temperature is None:
        return cap
    humidity = humidity_at(temperature, dew)
    return Goal(humidity, "night" if night else "day", dew, DEW_MACHINE_THRESHOLD) if humidity < dew_rule.rh_cap else cap


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
