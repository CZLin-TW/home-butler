"""Opt-in AC temperature feedback. Last temperature stays the user's target.

IR is write-only: power/mode checks use saved commands, not physical readback.
All in-process AC commands share CONTROL_LOCK. Sheets external edits and multiple
workers are not transactions. A durable blocked marker precedes every feedback
send; unknown outcomes require a new successful manual command, never a retry.
"""
import json
import math
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from threading import RLock

CONTROL_LOCK = RLock()
CONFIG_COL = "空調溫度回饋設定"
STATE_COL = "空調溫度回饋狀態"
DEFAULTS = dict(enabled=False, sensor_name="", interval_min=5, tolerance=0.5,
                step=1, min_adjust_min=10, max_offset=3)
_started = time.time()
_evaluated = {}
_samples = {}
_runtime = {}


def decode(value):
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def valid_config(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError("回饋設定欄位無效")
    cfg = {**DEFAULTS, **value}
    if type(cfg["enabled"]) is not bool or not isinstance(cfg["sensor_name"], str):
        raise ValueError("啟用狀態或感測器名稱無效")
    cfg["sensor_name"] = cfg["sensor_name"].strip()
    for key, low, high in [("interval_min", 1, 30), ("step", 1, 2),
                           ("min_adjust_min", 1, 60), ("max_offset", 1, 5)]:
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:
            raise ValueError(f"{key} 超出允許範圍")
    tolerance = cfg["tolerance"]
    if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or not 0.3 <= tolerance <= 2:
        raise ValueError("容許溫差需介於 0.3 至 2°C")
    if cfg["enabled"] and not cfg["sensor_name"]:
        raise ValueError("請選擇溫度感測器")
    return cfg


def config_for(row):
    try:
        return valid_config(decode(row.get(CONFIG_COL)))
    except ValueError:
        return dict(DEFAULTS)


def temperature(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def state_for(row):
    state = decode(row.get(STATE_COL))
    # A malformed persisted state is not permission to resume IR automation.
    for key in ("last_adjusted_at", "last_sample_at"):
        value = temperature(state.get(key, 0))
        if value is None or value < 0:
            state["blocked"] = True
            value = 0
        state[key] = value
    return state


def sensor_value(cfg, sensors, now):
    sensor = sensors.get(cfg["sensor_name"], {})
    sample = temperature(sensor.get("last_polled_at"))
    temp = temperature(sensor.get("current", {}).get("temp"))
    if not sensor.get("online") or sample is None or not 0 <= now - sample <= 660 or temp is None or not -20 <= temp <= 60:
        return None, None
    return temp, sample


def decide(row, cfg, state, sensors, now):
    """Pure bounded controller: one step per fresh sample, no power decisions."""
    if not cfg["enabled"]:
        return "disabled", None
    if state.get("blocked"):
        return "unconfirmed", None
    if row.get("最後電源") != "on":
        return "waiting_power", None
    if row.get("最後模式") not in ("冷氣", "暖氣"):
        return "waiting_mode", None
    target, sent = temperature(row.get("最後溫度")), temperature(state.get("ir_temperature"))
    if target is None or sent is None or not (16 <= target <= 30 and 16 <= sent <= 30) or not (target * 2).is_integer() or not sent.is_integer():
        return "needs_manual", None
    measured, sample = sensor_value(cfg, sensors, now)
    if measured is None:
        return "sensor_stale", None
    if sample <= state.get("last_sample_at", 0):
        return "waiting_sample", None
    if now < state.get("last_adjusted_at", now) + cfg["min_adjust_min"] * 60:
        return "settling", None
    error = measured - target
    if abs(error) <= cfg["tolerance"]:
        return "stable", None
    # Both cooling and heating: too warm -> lower thermostat setpoint.
    candidate = sent + (-cfg["step"] if error > 0 else cfg["step"])
    candidate = max(16, math.ceil(target - cfg["max_offset"]), min(30, math.floor(target + cfg["max_offset"]), candidate))
    # Tightening the bound must not cause a jump larger than one step.
    if abs(candidate - sent) > cfg["step"]:
        return "needs_manual", None
    return ("at_limit", None) if candidate == sent else ("adjusting", int(candidate))


def _persist(row, fields):
    from sheets import update_device_state_fields
    rec, applied = update_device_state_fields(row["Device ID"], fields, required_fields=fields.keys())
    row.update(applied)
    return rec


def _unique(rows, name):
    matches = [r for r in rows if r.get("名稱") == name and r.get("狀態") == "啟用"]
    if len(matches) != 1 or matches[0].get("類型") != "空調" or not matches[0].get("Device ID"):
        raise ValueError("找不到唯一啟用的空調")
    if sum(r.get("Device ID") == matches[0]["Device ID"] for r in rows) != 1:
        raise ValueError("空調 Device ID 重複")
    return matches[0]


def save_config(name, values):
    from ac_temperature import ir_temperature
    from sheets import get_sheet_records, get_sheet, ensure_columns
    import device_status
    cfg = valid_config(values)
    with CONTROL_LOCK:
        rows = get_sheet_records("智能居家")
        row = _unique(rows, name)
        if cfg["enabled"]:
            sensors = [r for r in rows if r.get("名稱") == cfg["sensor_name"] and r.get("類型") == "感應器" and r.get("狀態") == "啟用"]
            if len(sensors) != 1 or not row.get("位置") or sensors[0].get("位置") != row.get("位置"):
                raise ValueError("請選擇同位置且唯一啟用的感測器")
        ensure_columns(get_sheet("智能居家"), [CONFIG_COL, STATE_COL])
        # Saving alone never sends IR. The caller may explicitly evaluate after
        # persistence, without resetting the interval since the actual command.
        old = state_for(row)
        state = dict(old)
        if device_status.snapshot(name).get(name, {}).get("stateUncertain"):
            state["blocked"] = True
        if "ir_temperature" not in state:
            try:
                state["ir_temperature"] = ir_temperature(row.get("最後溫度"))
            except ValueError:
                state["ir_temperature"] = None
            try:
                last = datetime.fromisoformat(str(row.get("最後更新時間", "")))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone(timedelta(hours=8)))
                state["last_adjusted_at"] = last.timestamp()
            except (ValueError, TypeError, OverflowError):
                pass
        fields = {CONFIG_COL: json.dumps(cfg, ensure_ascii=False), STATE_COL: json.dumps(state)}
        if not cfg["enabled"] and temperature(row.get("最後溫度")) is not None:
            try:
                fields["最後溫度"] = ir_temperature(row["最後溫度"])
            except ValueError:
                pass  # Invalid existing state must not prevent disabling automation.
        _persist(row, fields)
        _evaluated.pop(row["Device ID"], None)
        _runtime.pop(row["Device ID"], None)
        device_status.load_catalog(rows)
        return cfg


def manual_saved_fields(ctx, power, temp):
    from ac_temperature import ir_temperature
    state = dict(ctx._feedback_state)
    state.update(blocked=False, last_adjusted_at=time.time(), last_sample_at=0)
    if power == "on" and temp is not None:
        state["ir_temperature"] = ir_temperature(temp)
    return {STATE_COL: json.dumps(state)}


def manual_control(fn):
    """Wrap every legacy caller without accepting an internal bypass parameter."""
    @wraps(fn)
    def call(data, ctx, *args, **kwargs):
        from sheets import get_sheet_records, get_device_id_by_name
        from command_result import CommandResult
        import device_status
        with CONTROL_LOCK:
            rows = ctx.get("智能居家")
            id = get_device_id_by_name(data.get("device_name", ""), ctx)
            row = next((r for r in rows if r.get("Device ID") == id and r.get("狀態") == "啟用"), None)
            if row is None:
                acs = [r for r in rows if r.get("類型") == "空調" and r.get("狀態") == "啟用"]
                row = acs[0] if len(acs) == 1 else None
            # Unconfigured installations retain their existing I/O cost.
            configured = row and (row.get(CONFIG_COL) or any(
                r.get("Device ID") == row.get("Device ID") and r.get(CONFIG_COL)
                for r in device_status.catalog_rows()))
            if not configured:
                return fn(data, ctx, *args, **kwargs)
            try:
                fresh = _unique(get_sheet_records("智能居家"), row["名稱"])
                if fresh["Device ID"] != row["Device ID"]:
                    return CommandResult.failed("空調設備已變更，請重新讀取後操作")
                row.update(fresh)
                state = state_for(row)
                ctx._feedback_state = state
                ctx._ac_state_saved = False
                # Durable uncertainty also covers manual commands racing a tick.
                _persist(row, {STATE_COL: json.dumps({**state, "blocked": True})})
                updated = dict(data)
                if updated.get("power", "on") == "on":
                    updated.setdefault("temperature", row.get("最後溫度"))
                    labels = {"冷氣": "cool", "暖氣": "heat", "除濕": "dry", "送風": "fan", "自動": "auto"}
                    updated.setdefault("mode", labels.get(row.get("最後模式"), "cool"))
                    updated.setdefault("fan_speed", {"自動": "auto", "低": "low", "中": "medium", "高": "high"}.get(row.get("最後風速"), "auto"))
                result = fn(updated, ctx, *args, **kwargs)
                if result.status == "success" and not ctx._ac_state_saved:
                    return CommandResult.unknown("❌ 指令已送出但狀態未確認，溫度回饋已暫停")
                return result
            except Exception:
                return CommandResult.unknown("❌ 空調狀態未確認，請檢查設備後重新操作；溫度回饋已暫停")
            finally:
                if hasattr(ctx, "_feedback_state"):
                    del ctx._feedback_state
    return call


def evaluate_now(name):
    """An explicit Dashboard request, serialized with settings and AC commands."""
    from sheets import get_sheet_records
    with CONTROL_LOCK:
        row = _unique(get_sheet_records("智能居家"), name)
        tick(candidates=[row], immediate=True)
        return {"status": _runtime.get(row["Device ID"], {}).get("status", "unconfirmed")}


def tick(*, candidates=None, immediate=False):
    from sheets import get_sheet_records
    import device_status
    import sensor_state
    import switchbot_api
    # No extra Sheets request for homes that have not enabled this feature.
    if candidates is None:
        candidates = [r for r in device_status.catalog_rows() if r.get("類型") == "空調" and config_for(r)["enabled"]]
    for candidate in candidates:
        id = candidate.get("Device ID")
        cfg = config_for(candidate)
        now = time.time()
        if not immediate and now - _evaluated.get(id, 0) < cfg["interval_min"] * 60:
            continue
        if not CONTROL_LOCK.acquire(blocking=False):
            continue
        try:
            rows = get_sheet_records("智能居家")
            row = _unique(rows, candidate["名稱"])
            if row["Device ID"] != id:
                _runtime[id] = {"status": "needs_manual", "evaluated_at": now}
                continue
            cfg, state = config_for(row), state_for(row)
            # Restart grace: no burst from old measurements/timers after deployment.
            if not immediate:
                state["last_adjusted_at"] = max(state.get("last_adjusted_at", _started), _started)
                state["last_sample_at"] = max(state.get("last_sample_at", 0), _samples.get(id, 0))
            # Explicit evaluation can reconsider a previously skipped sample,
            # but persisted command times/sample IDs and blocked state still apply.
            sensors = sensor_state.snapshot(include_history=False)
            status, target = decide(row, cfg, state, sensors, now)
            _evaluated[id] = now
            _runtime[id] = {"status": status, "evaluated_at": now}
            _, sample = sensor_value(cfg, sensors, now)
            if sample is not None:
                _samples[id] = sample
            if target is None:
                continue
            # Revalidate sensor identity/location from fresh metadata before IR.
            sensor_rows = [s for s in rows if s.get("名稱") == cfg["sensor_name"] and s.get("類型") == "感應器" and s.get("狀態") == "啟用"]
            if len(sensor_rows) != 1 or not row.get("位置") or sensor_rows[0].get("位置") != row["位置"]:
                _runtime[id]["status"] = "sensor_stale"
                continue
            fan = switchbot_api.AC_FAN_MAP.get({"自動": "auto", "低": "low", "中": "medium", "高": "high"}.get(row.get("最後風速")))
            if fan is None:
                _runtime[id]["status"] = "needs_manual"
                continue
            pending = {**state, "blocked": True, "last_sample_at": sample, "last_adjusted_at": now}
            _persist(row, {STATE_COL: json.dumps(pending)})
            # Full IR frame, but no handler: do not alter power-on anchors,
            # auto-off schedules, antimold schedules or the comfort target.
            result = switchbot_api.ac_set_all(id, target, 2 if row["最後模式"] == "冷氣" else 5, fan, "on")
            if not result.get("success"):
                _runtime[id]["status"] = "unconfirmed"
                continue
            final = {**pending, "blocked": False, "ir_temperature": target}
            _persist(row, {STATE_COL: json.dumps(final)})
            _runtime[id]["status"] = "compensating"
        except Exception as error:
            _runtime[id] = {"status": "unconfirmed", "evaluated_at": now}
            print(f"[AC FEEDBACK] evaluation failed: {type(error).__name__}")
        finally:
            CONTROL_LOCK.release()


def describe(row, sensors, now=None):
    now = time.time() if now is None else now
    cfg, state = config_for(row), state_for(row)
    status, _ = decide(row, cfg, state, sensors, now)
    runtime = _runtime.get(row.get("Device ID"), {})
    if status == "adjusting":
        status = runtime.get("status", "waiting_sample")
    measured, _ = sensor_value(cfg, sensors, now)
    return {"config": cfg, "status": status, "sensor_temperature": measured,
            "target_temperature": temperature(row.get("最後溫度")),
            "ir_temperature": temperature(state.get("ir_temperature")),
            "last_adjusted_at": state.get("last_adjusted_at"),
            "next_evaluation_at": max(now, runtime.get("evaluated_at", now) + cfg["interval_min"] * 60)}
