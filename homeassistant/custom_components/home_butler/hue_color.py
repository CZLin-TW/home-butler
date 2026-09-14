"""Colour capability/readback for selected Hue groups; no cached desired state.

The legacy catalogue stays compatible. Only HA advertises these controls. Use
HA's colour conversion (including each bulb's gamut) instead of inventing a
second colour model. Mixed groups never report an average as a real setting.
"""
import math

from homeassistant.util.color import GamutType, XYPoint, color_hs_to_xy, color_xy_to_hs

from . import hue_model as model


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def lights_for_area(area, data):
    lights = {item["id"]: item for item in data["light"]}
    containers = {(item["type"], item["id"]): item for kind in ("room", "zone", "bridge_home") for item in data[kind]}
    container = model._hue_container_for_grouped_light_id(area["id"], data)
    ids = model._hue_container_light_ids(container, containers, {i["id"]: i for i in data["device"]}, lights)
    return [lights[lid] for lid in ids]


def temperature_bounds(light):
    schema = (light.get("color_temperature") or {}).get("mirek_schema") or {}
    low, high = schema.get("mirek_minimum"), schema.get("mirek_maximum")
    if not number(low) or not number(high) or not 1 <= low <= high <= 1000:
        return None
    return low, high


def supports_color(light):
    return isinstance(light.get("color"), dict)


def current(light):
    temp = light.get("color_temperature") or {}
    mirek = temp.get("mirek")
    if temp.get("mirek_valid") is True and number(mirek) and mirek > 0:
        return "temperature", round(1000000 / mirek)
    xy = (light.get("color") or {}).get("xy") or {}
    if number(xy.get("x")) and number(xy.get("y")) and 0 <= xy["x"] <= 1 and 0 < xy["y"] <= 1:
        return "color", (xy["x"], xy["y"])
    return None, None


def describe(area, data):
    lights = lights_for_area(area, data)
    colors = [light for light in lights if supports_color(light)]
    bounds = [bounds for light in lights if (bounds := temperature_bounds(light))]
    low = max((b[0] for b in bounds), default=0)
    high = min((b[1] for b in bounds), default=0)
    # Use the common range, rounded inward: every participating bulb can accept it.
    minimum = math.ceil(1000000 / high) if high and low <= high else None
    maximum = math.floor(1000000 / low) if low and low <= high else None
    states = [current(light) for light in lights if supports_color(light) or temperature_bounds(light)]
    mode, hs, kelvin = None, None, None
    if states and all(s[0] is not None for s in states):
        mode, value = states[0]
        if any(s[0] != mode for s in states):
            mode = "mixed"
        elif mode == "temperature":
            if max(s[1] for s in states) - min(s[1] for s in states) <= 30:
                kelvin = value
            else:
                mode = "mixed"
        elif all(abs(s[1][0] - value[0]) <= .003 and abs(s[1][1] - value[1]) <= .003 for s in states):
            hs = list(color_xy_to_hs(*value))
        else:
            mode = "mixed"
    return {"color_count": len(colors), "temperature_count": len(bounds),
            "min_kelvin": minimum, "max_kelvin": maximum,
            "mode": mode, "hs": hs, "kelvin": kelvin}


def plan_color(area, data, payload):
    hs, kelvin = payload.get("hs_color"), payload.get("color_temp_kelvin")
    if hs is not None and kelvin is not None:
        raise ValueError("Choose colour or white temperature")
    info = describe(area, data)
    if hs is not None:
        if not isinstance(hs, list) or len(hs) != 2 or not all(number(v) for v in hs) or not 0 <= hs[0] <= 360 or not 0 <= hs[1] <= 100:
            raise ValueError("Invalid hue/saturation")
    elif kelvin is not None:
        if type(kelvin) is not int or info["min_kelvin"] is None or not info["min_kelvin"] <= kelvin <= info["max_kelvin"]:
            raise ValueError("Temperature outside supported range")
    else:
        return [], []
    writes, skipped = [], []
    for light in lights_for_area(area, data):
        if hs is not None and supports_color(light):
            gamut_data = light["color"].get("gamut")
            gamut = None
            if gamut_data:
                points = [gamut_data.get(key, {}) for key in ("red", "green", "blue")]
                if not all(number(p.get("x")) and number(p.get("y")) for p in points):
                    raise ValueError("Invalid bulb gamut")
                gamut = GamutType(*(XYPoint(p["x"], p["y"]) for p in points))
            x, y = color_hs_to_xy(*hs, gamut)
            body = {"color": {"xy": {"x": x, "y": y}}}
        elif kelvin is not None and temperature_bounds(light):
            low, high = temperature_bounds(light)
            body = {"color_temperature": {"mirek": max(low, min(high, round(1000000 / kelvin)))}}
        else:
            skipped.append(light["id"])
            continue
        # These are settings only. Never turn on an off bulb as a side effect.
        writes.append((light["id"], body))
    if not writes:
        raise ValueError("No supported lights")
    return writes, skipped
