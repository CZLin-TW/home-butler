"""Checks, manual control and rule changes for the dehumidifiers.

The dehumidifiers themselves come from the 智能居家 tab (kind 除濕機, enabled): name, location, brand and
the cloud's device id. A Panasonic unit also needs its device credential, which lives in the environment
(PANASONIC_DEVICE_AUTH), never in the spreadsheet. Room humidity comes from Home Assistant, with the
sensor's offset applied; while HA is away the sensor counts as silent.

Everything that changes a dehumidifier (the 5-minute check, a person's command, a rule change) runs one
at a time under one lock, and no database transaction stays open while a cloud call is under way.
While auto mode is on, only the rule controls the machine; anyone else's command is refused.
"""
from __future__ import annotations

import logging
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

from sqlalchemy import delete, select

from homebutler.dehumidifier import drivers, rules
from homebutler.dehumidifier.tables import Dehumidifier, DehumidifierReading
from homebutler.domain import devices
from homebutler.store.db import Store
from homebutler.store.models import DeviceSetting, utcnow

log = logging.getLogger("homebutler.dehumidifier")
RETENTION_DAYS = 400


class Refused(ValueError):
    pass


@dataclass(frozen=True)
class Unit:
    name: str
    location: str
    brand: str            # Panasonic | LG
    device_id: str


def norm(text) -> str:
    text = unicodedata.normalize("NFC", str(text or "")).strip()
    return text.replace(" ", "").replace("　", "").replace("溼", "濕").replace("卧", "臥")


class Dehumidifiers:
    def __init__(self, store: Store, link, panasonic_client, lg_client, device_auth: dict[str, str], zone: tzinfo,
                 clock=utcnow):
        self.store, self.link, self.zone, self.clock = store, link, zone, clock
        self.panasonic, self.lg, self.device_auth = panasonic_client, lg_client, device_auth
        self.lock = threading.Lock()

    # ── what exists ─────────────────────────────────────────────────────────

    def units(self) -> list[Unit]:
        with self.store.transaction() as session:
            found = session.scalars(select(DeviceSetting).where(DeviceSetting.kind == "dehumidifier", DeviceSetting.enabled.is_(True),
                                                                DeviceSetting.removed_at.is_(None)).order_by(DeviceSetting.name))
            return [Unit(s.name, s.location, "LG" if s.brand.strip().upper() == "LG" else "Panasonic", s.external_id) for s in found]

    def unit(self, name: str) -> Unit:
        for unit in self.units():
            if unit.name == name:
                return unit
        raise LookupError(name)

    def driver(self, unit: Unit):
        """None when this unit cannot be controlled (missing id, credential or account)."""
        if not unit.device_id:
            return None
        if unit.brand == "LG":
            return drivers.LGDriver(self.lg, unit.device_id) if self.lg.configured else None
        auth = self.device_auth.get(unit.device_id, "")
        return drivers.PanasonicDriver(self.panasonic, auth, unit.device_id) if auth and self.panasonic.configured else None

    def _row(self, session, name: str) -> Dehumidifier:
        row = session.get(Dehumidifier, name)
        if row is None:
            row = Dehumidifier(name=name, auto_mode=False, sensor_name="", duration_min=30, threshold=50, threshold_source="fixed",
                               phase="disabled", sensor_missing_ticks=0, last_event="", mode="", target="", status_error="")
            session.add(row)
            session.flush()
        return row

    def sensors(self) -> dict[str, dict]:
        """Enabled sensors from the tab: {name: {"location", "curve", "humidity"}}, humidity from HA (offset applied)."""
        state = self.link.state()
        live = devices.sensor_values(state)
        with self.store.transaction() as session:
            found = list(session.scalars(select(DeviceSetting).where(DeviceSetting.kind == "sensor", DeviceSetting.removed_at.is_(None))))
            out = {}
            for setting in found:
                values = devices.adjusted(live.get(setting.name, {}), setting) if state["online"] else {}
                out[setting.name] = {"location": setting.location, "curve": setting.humidity_rule_text, "enabled": setting.enabled,
                                     "humidity": values.get("humidity")}
            return out

    def choose_sensor(self, unit: Unit, wanted: str = "") -> str:
        """The named sensor, or the best enabled one in the same location (one that reports humidity first)."""
        sensors = {name: s for name, s in self.sensors().items() if s["enabled"]}
        if wanted:
            matches = [n for n in sensors if norm(n) == norm(wanted)] or [n for n in sensors if norm(wanted) in norm(n)]
            if len(matches) == 1:
                return matches[0]
            if matches:
                raise Refused(f"找到多個可能的感測器（{'、'.join(matches)}），請指定完整名稱")
            raise Refused(f"找不到感測器「{wanted}」")
        place = norm(unit.location)
        if not place:
            raise Refused(f"{unit.name} 未設定位置，無法自動配對同空間感測器")
        nearby = [n for n, s in sensors.items() if norm(s["location"]) and (norm(s["location"]) in place or place in norm(s["location"]))]
        if not nearby:
            raise Refused(f"{unit.name} 所在位置「{unit.location}」沒有啟用中的感測器")
        return sorted(nearby, key=lambda n: (sensors[n]["humidity"] is not None, place in norm(n), n), reverse=True)[0]

    # ── the 5-minute check ──────────────────────────────────────────────────

    def check(self) -> dict:
        """Read every unit's status, record it, and run the auto rule where it is on."""
        units, sensors = self.units(), self.sensors()
        now = self.clock()
        hour = now.astimezone(self.zone).hour
        done = {}
        for unit in units:
            try:
                with self.lock:
                    done[unit.name] = self._check_one(unit, sensors, now, hour)
            except Exception:
                log.exception("dehumidifier %s check failed", unit.name)
                done[unit.name] = "error"
        with self.store.transaction() as session:
            session.execute(delete(DehumidifierReading).where(DehumidifierReading.t < now - timedelta(days=RETENTION_DAYS)))
        return done

    def _read(self, unit: Unit, driver, now: datetime):
        """Read the status and keep it. Returns (status, actual) or None when it could not be read."""
        status = driver.status() if driver else {"error": "缺少控制資訊（Device ID、帳號或憑證）"}
        with self.store.transaction() as session:
            row = self._row(session, unit.name)
            if "error" in status:
                row.status_error = status["error"]
                return None
            fields = driver.fields(status)
            row.power, row.mode, row.target, row.status_at, row.status_error = fields["power"], fields["mode"], fields["target"], now, ""
            session.add(DehumidifierReading(name=unit.name, t=now, power="on" if fields["power"] else "off", mode=fields["mode"]))
        return status, driver.read(status)

    def _check_one(self, unit: Unit, sensors: dict, now: datetime, hour: int) -> str:
        driver = self.driver(unit)
        read = self._read(unit, driver, now)
        if read is None:
            return "unreadable"
        _, actual = read
        power_on = actual["power"]
        with self.store.transaction() as session:
            row = self._row(session, unit.name)
            if not row.auto_mode:
                return "manual"
            sensor = sensors.get(row.sensor_name, {})
            threshold, warning = rules.effective_threshold(row.threshold, row.threshold_source, sensor.get("curve"), hour)
            if warning:
                log.warning("%s: %s", unit.name, warning)
            expected, applied = row.expected, row.applied_threshold
            above, below, missing = row.above_since, row.below_since, row.sensor_missing_ticks
        if expected is None:
            # First check after auto mode came on (or a command that did not fully succeed): set the baseline.
            if power_on:
                full = drivers.outcome(driver.align(threshold)) == "success"
                expected = driver.expected_on(threshold) if full else None
            else:
                expected = driver.expected_off()
        elif drivers.diverged(expected, actual):
            log.info("%s: changed by hand (expected %s, found %s); auto mode ends", unit.name, expected, actual)
            self._end_auto(unit.name, "auto_disabled_manual", now)
            return "taken_over"
        if applied is not None and applied != threshold:
            above = below = None
            if power_on:
                expected = driver.expected_on(threshold) if drivers.outcome(driver.align(threshold)) == "success" else None
        humidity = sensor.get("humidity")
        if humidity is None:
            missing += 1
            if missing >= rules.DISABLE_TICKS:
                if power_on:
                    driver.fire_off()
                self._end_auto(unit.name, "auto_disabled_sensor_lost", now)
                return "sensor_lost"
            self._save(unit.name, expected=expected, applied_threshold=threshold, above_since=above, below_since=below,
                       sensor_missing_ticks=missing, **({"phase": "sensor_lost_warning", "countdown_min": None}
                                                        if missing >= rules.WARNING_TICKS else {}))
            return "sensor_silent"
        row_duration = self._duration(unit.name)
        step = rules.steady(threshold, row_duration, humidity, power_on, now, above, below)
        event = None
        if step.fire == "on":
            full = drivers.outcome(driver.fire_on(threshold)) == "success"
            # Only a fully successful command sets the baseline; otherwise the next check aligns again,
            # rather than mistaking "the command did not take" for "a person changed it".
            expected, event = (driver.expected_on(threshold) if full else None), "triggered_on"
        elif step.fire == "off":
            driver.fire_off()
            expected, event = driver.expected_off(), "triggered_off"
        changes = dict(expected=expected, applied_threshold=threshold, above_since=step.above_since, below_since=step.below_since,
                       sensor_missing_ticks=0, phase=step.phase, countdown_min=step.countdown_min)
        if event:
            changes.update(last_event=event, last_event_at=now)
        self._save(unit.name, **changes)
        return event or step.phase

    def _duration(self, name: str) -> int:
        with self.store.transaction() as session:
            return self._row(session, name).duration_min

    def _save(self, name: str, **changes) -> None:
        with self.store.transaction() as session:
            row = self._row(session, name)
            for key, value in changes.items():
                setattr(row, key, value)
            row.updated_at = self.clock()

    def _end_auto(self, name: str, event: str, now: datetime) -> None:
        """Auto mode off and its progress cleared; the machine is left as it is (unless the caller turned it off)."""
        self._save(name, auto_mode=False, phase="disabled", countdown_min=None, above_since=None, below_since=None,
                   sensor_missing_ticks=0, expected=None, applied_threshold=None, last_event=event, last_event_at=now)

    # ── people ──────────────────────────────────────────────────────────────

    def control(self, name: str, power: str | None = None, mode: str | None = None, humidity: int | None = None) -> dict:
        """A person's command. Refused while auto mode is on. Stops at the first step that does not succeed;
        an unknown result is reported as such and never sent again."""
        unit = self.unit(name)
        driver = self.driver(unit)
        if driver is None:
            raise Refused(f"{name} 缺少控制資訊（Device ID、帳號或憑證）")
        if power not in (None, "on", "off"):
            raise Refused("電源只能是 on 或 off")
        if power == "off" and (mode or humidity):
            raise Refused("關機時不能同時設定模式或濕度")
        if not (power or mode or humidity):
            raise Refused("沒有要執行的動作")
        with self.lock:
            with self.store.transaction() as session:
                if self._row(session, name).auto_mode:
                    raise Refused(f"{name} 目前處於自動模式，請先關閉自動模式才能手動控制")
            if power == "off":
                results = [driver.power(False)]
            else:
                results = [driver.power(True)]
                if drivers.ok(results[-1]) and mode:
                    results.append(driver.set_mode(mode))
                if drivers.ok(results[-1]) and humidity:
                    results.append(driver.set_humidity(int(humidity)))
            outcome = drivers.outcome(results)
            failure = next((r for r in results if not drivers.ok(r)), {})
            self._read(unit, driver, self.clock())
        message = {"success": f"{name} 指令已送出", "failed": f"{name} 控制失敗：{failure.get('error', '')}",
                   "unknown": f"{name} 指令結果未確認，請先查看除濕機；不會自動重送"}[outcome]
        return {"status": outcome, "message": message}

    def set_rule(self, name: str, auto_mode: bool, sensor_name: str | None = None, duration_min: int | None = None,
                 threshold: int | None = None, threshold_source: str | None = None) -> dict:
        """Change a unit's rule. Switching auto mode on decides at once by the plain comparison; switching it
        off leaves the machine as it is. Naming a number without a source means that fixed number."""
        unit = self.unit(name)
        if threshold is not None and not rules.THRESHOLD_MIN <= threshold <= rules.THRESHOLD_MAX:
            raise Refused(f"目標濕度需介於 {rules.THRESHOLD_MIN}%～{rules.THRESHOLD_MAX}%")
        if duration_min is not None and not 0 <= duration_min <= 24 * 60:
            raise Refused("等待時間需介於 0 至 1440 分鐘")
        if threshold_source not in (None, "fixed", rules.CUSTOM):
            raise Refused("目標濕度來源只能是 fixed 或 custom")
        driver = self.driver(unit) if auto_mode else None
        if auto_mode and driver is None:
            raise Refused(f"{name} 缺少控制資訊（Device ID、帳號或憑證），無法開啟自動模式")
        with self.lock:
            with self.store.transaction() as session:
                row = self._row(session, name)
                was_on, current_sensor = row.auto_mode, row.sensor_name
            sensor = self.choose_sensor(unit, sensor_name or ("" if not auto_mode else current_sensor)) if auto_mode else (sensor_name or current_sensor)
            with self.store.transaction() as session:
                row = self._row(session, name)
                row.threshold_source = threshold_source or ("fixed" if threshold is not None else row.threshold_source)
                row.sensor_name = sensor
                if duration_min is not None:
                    row.duration_min = duration_min
                if threshold is not None:
                    row.threshold = threshold
                row.auto_mode = auto_mode
                if was_on != auto_mode:
                    row.above_since = row.below_since = row.expected = row.applied_threshold = None
                    row.sensor_missing_ticks, row.countdown_min = 0, None
                fixed, source = row.threshold, row.threshold_source
            info = self.sensors().get(sensor, {})
            now = self.clock()
            effective, _ = rules.effective_threshold(fixed, source, info.get("curve"), now.astimezone(self.zone).hour)
            humidity, power_on, event = info.get("humidity"), None, None
            if auto_mode and not was_on:
                read = self._read(unit, driver, now)
                power_on = read[1]["power"] if read else None
                if humidity is not None and power_on is not None:
                    decision = rules.toggle_on(effective, humidity, power_on)
                    if decision == "on":
                        full = drivers.outcome(driver.fire_on(effective)) == "success"
                        self._save(name, expected=driver.expected_on(effective) if full else None, applied_threshold=effective)
                        event, power_on = "toggled_immediate_on", True
                    elif decision == "off":
                        driver.fire_off()
                        self._save(name, expected=driver.expected_off(), applied_threshold=effective)
                        event, power_on = "toggled_immediate_off", False
            changes = {"phase": rules.phase_now(effective, humidity, power_on) if auto_mode else "disabled"}
            if event:
                changes.update(last_event=event, last_event_at=now)
            self._save(name, **changes)
        return self.view(name)

    # ── reading ─────────────────────────────────────────────────────────────

    def view(self, name: str) -> dict:
        return next(v for v in self.views() if v["name"] == name)

    def views(self) -> list[dict]:
        """Every unit for the card: status as last read (with its time), the rule, and the target in effect."""
        units, sensors = self.units(), self.sensors()
        hour = self.clock().astimezone(self.zone).hour
        out = []
        with self.store.transaction() as session:
            for unit in units:
                row = self._row(session, unit.name)
                sensor = sensors.get(row.sensor_name, {})
                effective = row.applied_threshold
                if effective is None:
                    effective, _ = rules.effective_threshold(row.threshold, row.threshold_source, sensor.get("curve"), hour)
                segments, error = rules.parse_curve(sensor.get("curve")) if row.sensor_name else (None, "未選感測器")
                out.append({
                    "name": unit.name, "location": unit.location, "brand": unit.brand, "controllable": self.driver(unit) is not None,
                    "status": {"power": row.power, "mode": row.mode, "target": row.target, "updated_at": row.status_at,
                               "error": row.status_error},
                    "rule": {"auto_mode": row.auto_mode, "sensor_name": row.sensor_name, "duration_min": row.duration_min,
                             "threshold": row.threshold, "threshold_source": row.threshold_source,
                             "effective_threshold": effective, "on_at": effective + rules.ABOVE, "off_at": effective - rules.BELOW,
                             "phase": row.phase, "countdown_min": row.countdown_min, "last_event": row.last_event,
                             "last_event_at": row.last_event_at,
                             "curve": [{"hour": h, "threshold": v} for h, v in segments or []], "curve_error": "" if segments else error},
                    "sensor_humidity": sensor.get("humidity"),
                })
        return out

    def history(self, since: datetime) -> dict[str, list[dict]]:
        with self.store.transaction() as session:
            out: dict[str, list[dict]] = {}
            for reading in session.scalars(select(DehumidifierReading).where(DehumidifierReading.t >= since)
                                           .order_by(DehumidifierReading.name, DehumidifierReading.t)):
                out.setdefault(reading.name, []).append({"t": reading.t, "power": reading.power, "mode": reading.mode})
            return out

    def locked(self, name: str) -> bool:
        with self.store.transaction() as session:
            row = session.get(Dehumidifier, name)
            return bool(row and row.auto_mode)
