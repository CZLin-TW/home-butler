"""Native HA connection reuse and bounded Hue execution with fake bridge I/O."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import time

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.home_butler.hue import HueCommands, group_options, select_groups
from homeassistant.util.color import color_hs_to_xy

G, R, L, D, S, T = [f"00000000-0000-0000-0000-{n:012d}" for n in range(1, 7)]
CATALOGUE = [
    {"type": "room", "id": R, "metadata": {"name": "客廳"}, "services": [{"rid": G, "rtype": "grouped_light"}], "children": [{"rid": D, "rtype": "device"}]},
    {"type": "grouped_light", "id": G, "owner": {"rid": R, "rtype": "room"}, "on": {"on": True}, "dimming": {"brightness": 45}},
    {"type": "device", "id": D, "services": [{"rid": L, "rtype": "light"}]},
    {"type": "light", "id": L, "owner": {"rid": D, "rtype": "device"}, "effects": {"effect_values": ["candle", "no_effect"]}, "timed_effects": {"effect_values": ["sunrise"]}},
    {"type": "scene", "id": S, "metadata": {"name": "日出"}, "group": {"rid": R, "rtype": "room"}, "palette": {"color": [{"color": {"xy": {"x": .2, "y": .3}}}]}, "status": {"active": "dynamic_palette"}},
    {"type": "smart_scene", "id": T, "metadata": {"name": "自然光"}, "group": {"rid": R, "rtype": "room"}},
]


@pytest.fixture
async def native(hass):
    entry = MockConfigEntry(domain="hue", title="Hue Bridge", data={}, state=ConfigEntryState.LOADED)
    entry.add_to_hass(hass)
    api = SimpleNamespace(request=AsyncMock(return_value=deepcopy(CATALOGUE)), writes=[], error=False, block=None)
    @asynccontextmanager
    async def create_request(method, path, **kwargs):
        api.writes.append((method, path, kwargs["json"]))
        if api.block is not None:
            await api.block.wait()
        if api.error:
            raise TimeoutError()
        yield SimpleNamespace(raise_for_status=lambda: None, json=AsyncMock(return_value={"data": [], "errors": []}))
    api.create_request = create_request
    entry.runtime_data = SimpleNamespace(api_version=2, authorized=True, api=api)
    yield entry, api, HueCommands(hass, [entry.entry_id + "/" + G])
    entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


def frame(action="hue.list_areas", payload=None, request_id="a" * 32):
    return {"type": "hue_command", "request_id": request_id, "action": action, "payload": payload or {}, "expires_at": time.time() + 15}


async def test_catalogue_preserves_uuid_scene_effect_and_explicit_selection(hass, native):
    entry, api, commands = native
    options = await group_options(hass)
    assert options[0]["value"] == entry.entry_id + "/" + G
    assert "客廳" in options[0]["label"]
    result = await commands.execute(frame())
    area = result["result"]["areas"][0]
    assert area["id"] == G and area["brightness"] == 45
    assert {s["id"] for s in area["scenes"]} == {S, T}
    assert {e["key"] for e in area["effects"]} == {"candle", "no_effect", "sunrise"}
    assert not api.writes
    with pytest.raises(ValueError):
        await select_groups(hass, [entry.entry_id + "/" + S])
    empty = await HueCommands(hass, []).execute(frame())
    assert empty["result"]["areas"] == []


@pytest.mark.parametrize(("action", "payload", "path", "body"), [
    ("hue.set_state", {"area_id": G, "on": False, "brightness": None}, f"grouped_light/{G}", {"on": {"on": False}}),
    ("hue.set_state", {"area_id": G, "brightness": 26}, f"grouped_light/{G}", {"dimming": {"brightness": 26}}),
    ("hue.recall_scene", {"scene_id": S, "action": "dynamic_palette"}, f"scene/{S}", {"recall": {"action": "dynamic_palette"}}),
    ("hue.recall_scene", {"scene_id": T, "resource_type": "smart_scene", "action": "activate"}, f"smart_scene/{T}", {"recall": {"action": "activate"}}),
    ("hue.set_effect", {"area_id": G, "effect": "sunrise"}, f"light/{L}", {"timed_effects": {"effect": "sunrise"}}),
    ("hue.breathe", {"resource_id": G}, f"grouped_light/{G}", {"alert": {"action": "breathe"}}),
])
async def test_native_commands_and_request_deduplication(native, action, payload, path, body):
    _, api, commands = native
    command = frame(action, payload)
    assert (await commands.execute(command))["status"] == "success"
    assert (await commands.execute(command))["status"] == "success"
    assert api.writes == [("put", "clip/v2/resource/" + path, body)]
    assert (await commands.execute({**command, "payload": {}}))["status"] == "failed"
    assert len(api.writes) == 1


@pytest.mark.parametrize("payload", [
    {"area_id": "../../config", "on": True}, {"area_id": G, "on": "true"},
    {"area_id": G, "brightness": True}, {"area_id": G, "brightness": 101},
    {"area_id": G, "brightness": float("nan")}, {"area_id": G, "on": True, "url": "https://other"},
    {"area_id": L, "on": True, "resource_type": "light"},
])
async def test_invalid_or_unselected_targets_do_not_write(native, payload):
    _, api, commands = native
    assert (await commands.execute(frame("hue.set_state", payload)))["status"] == "failed"
    assert not api.writes


async def test_timeout_cancellation_expiry_and_unload_never_retry(hass, native):
    entry, api, commands = native
    command = frame("hue.breathe", {"resource_id": G})
    api.error = True
    assert (await commands.execute(command))["status"] == "unknown"
    assert (await commands.execute(command))["status"] == "unknown"
    assert len(api.writes) == 1
    api.error, api.block = False, asyncio.Event()
    command2 = frame("hue.breathe", {"resource_id": G}, "b" * 32)
    task = asyncio.create_task(commands.execute(command2))
    for _ in range(20):
        if len(api.writes) == 2:
            break
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await commands.execute(command2))["status"] == "unknown"
    assert len(api.writes) == 2
    command3 = {**frame("hue.breathe", {"resource_id": G}, "c" * 32), "expires_at": time.time() - 1}
    assert (await commands.execute(command3))["status"] == "failed"
    entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
    assert (await commands.execute(frame("hue.breathe", {"resource_id": G}, "d" * 32)))["status"] == "failed"
    assert len(api.writes) == 2


async def test_partial_effect_failure_and_unselected_scene_are_not_replayed(native):
    _, api, commands = native
    second = "00000000-0000-0000-0000-000000000010"
    data = api.request.return_value
    data[2]["services"].append({"rid": second, "rtype": "light"})
    data.append({**deepcopy(data[3]), "id": second})
    original = api.create_request
    @asynccontextmanager
    async def failing(method, path, **kwargs):
        if path.endswith(second):
            api.error = True
        async with original(method, path, **kwargs) as response:
            yield response
    api.create_request = failing
    request = frame("hue.set_effect", {"area_id": G, "effect": "candle"})
    result = await commands.execute(request)
    assert result["status"] == "unknown"
    assert result["result"]["applied_light_ids"] == [L]
    assert (await commands.execute(request))["status"] == "unknown"
    assert len(api.writes) == 2
    data[4]["group"]["rid"] = second
    assert (await commands.execute(frame("hue.recall_scene", {"scene_id": S}, "e" * 32)))["status"] == "failed"
    assert len(api.writes) == 2


def add_color(api):
    light = api.request.return_value[3]
    light["color"] = {"xy": {"x": .3127, "y": .3290}}
    light["color_temperature"] = {"mirek": 333, "mirek_valid": True,
        "mirek_schema": {"mirek_minimum": 153, "mirek_maximum": 500}}
    return light


async def test_color_white_readback_and_capabilities(native):
    _, api, commands = native
    light = add_color(api)
    area = (await commands.execute(frame()))["result"]["areas"][0]
    info = area["color_control"]
    assert info == {"color_count": 1, "temperature_count": 1, "min_kelvin": 2000, "max_kelvin": 6535,
                    "mode": "temperature", "hs": None, "kelvin": 3003}
    request = frame("hue.set_state", {"area_id": G, "hs_color": [280, 40]}, "b" * 32)
    assert (await commands.execute(request))["status"] == "success"
    x, y = color_hs_to_xy(280, 40)
    assert api.writes[-1] == ("put", f"clip/v2/resource/light/{L}", {"color": {"xy": {"x": x, "y": y}}})
    assert (await commands.execute(request))["status"] == "success"
    assert len(api.writes) == 1
    # A settings command preserves power. Readback comes from the bridge, not request.
    light["color_temperature"]["mirek_valid"] = False
    light["color"]["xy"] = {"x": x, "y": y}
    info = (await commands.execute(frame(request_id="c" * 32)))["result"]["areas"][0]["color_control"]
    assert info["mode"] == "color" and abs(info["hs"][0] - 280) < 2 and abs(info["hs"][1] - 40) < 2
    assert (await commands.execute(frame("hue.set_state", {"area_id": G, "color_temp_kelvin": 4000}, "d" * 32)))["status"] == "success"
    assert api.writes[-1][2] == {"color_temperature": {"mirek": 250}}


@pytest.mark.parametrize("settings", [
    {"hs_color": [360, 101]}, {"hs_color": [-1, 20]}, {"hs_color": [True, 20]},
    {"hs_color": [float("nan"), 20]}, {"hs_color": [40]}, {"hs_color": "red"},
    {"hs_color": [40, 20], "color_temp_kelvin": 3000}, {"color_temp_kelvin": 6536},
    {"color_temp_kelvin": 1999}, {"color_temp_kelvin": 3000.5},
])
async def test_color_validation_precedes_all_writes(native, settings):
    _, api, commands = native
    add_color(api)
    result = await commands.execute(frame("hue.set_state", {"area_id": G, "on": True, **settings}))
    assert result["status"] == "failed"
    assert not api.writes


async def test_mixed_color_group_does_not_average_or_write_unsupported_bulb(native):
    _, api, commands = native
    add_color(api)
    second = "00000000-0000-0000-0000-000000000010"
    data = api.request.return_value
    data[2]["services"].append({"rid": second, "rtype": "light"})
    data.append({"type": "light", "id": second, "color_temperature": {
        "mirek": 250, "mirek_valid": True, "mirek_schema": {"mirek_minimum": 200, "mirek_maximum": 454}}})
    info = (await commands.execute(frame()))["result"]["areas"][0]["color_control"]
    assert info["mode"] == "mixed" and info["hs"] is None and info["kelvin"] is None
    assert info["color_count"] == 1 and info["temperature_count"] == 2
    assert info["min_kelvin"] == 2203 and info["max_kelvin"] == 5000
    result = await commands.execute(frame("hue.set_state", {"area_id": G, "hs_color": [0, 0]}, "b" * 32))
    assert result["status"] == "success" and result["result"]["skipped_light_ids"] == [second]
    assert len(api.writes) == 1 and api.writes[0][1].endswith(L)


async def test_partial_color_failure_is_unknown_and_never_replayed(native):
    _, api, commands = native
    add_color(api)
    api.error = True
    request = frame("hue.set_state", {"area_id": G, "color_temp_kelvin": 3000})
    assert (await commands.execute(request))["status"] == "unknown"
    assert (await commands.execute(request))["status"] == "unknown"
    assert len(api.writes) == 1
