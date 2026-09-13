"""Real HA button platform, registry, flow and service dispatch; fake cloud I/O."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from switchbot_api import Remote
from custom_components.switchbot_ir_buttons.driver import resolve, commands
from custom_components.home_butler.ir_buttons import IRCommands, select_buttons, snapshot

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def setup_fan(hass):
    native = MockConfigEntry(domain="switchbot_cloud", title="SwitchBot", data={})
    native.add_to_hass(hass)
    with patch("homeassistant.components.switchbot_cloud.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(native.entry_id)
    remote = Remote(deviceId="remote123", deviceName="Fan", remoteType="DIY Fan", hubDeviceId="hub123")
    api = SimpleNamespace(send_command=AsyncMock())
    native.runtime_data = SimpleNamespace(api=api, devices=SimpleNamespace(switches=[
        (remote, SimpleNamespace(last_update_success=True))]))
    source = er.async_get(hass).async_get_or_create("switch", "switchbot_cloud", "remote123", config_entry=native)
    hass.states.async_set(source.entity_id, "unknown", {"friendly_name": "Fan"})
    entry = MockConfigEntry(domain="switchbot_ir_buttons", title="客廳電扇", unique_id=source.id,
        data={"source_id": source.id, "name": "客廳電扇", "buttons": ["電源", "風速+", "風速-"]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    ids = {hass.states.get(e.entity_id).attributes["ir_button"]: e.entity_id for e in entities}
    return source, native, entry, api, ids


async def test_real_button_platform_calls_exact_sdk_commands_and_never_invents_state(hass):
    source, native, entry, api, ids = await setup_fan(hass)
    assert set(ids) == {"電源", "風速+", "風速-"}
    for label, command, kind in [("風速+", "風速+", "customize"), ("風速-", "風速-", "customize"), ("電源", "turnOn", "command")]:
        await hass.services.async_call("button", "press", {"entity_id": ids[label]}, blocking=True)
        api.send_command.assert_awaited_with("remote123", command, kind, "default")
    assert api.send_command.await_count == 3
    assert hass.states.get(source.entity_id).state == "unknown"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_ha_bridge_selected_button_service_and_deduplication(hass):
    source, native, entry, api, ids = await setup_fan(hass)
    sources = select_buttons(hass, [ids["風速+"]])
    controller = IRCommands(hass, sources)
    frame = {"type": "ir_command", "request_id": uuid4().hex, "id": sources[0]["id"],
             "name": "客廳電扇", "button": "風速+", "expires_at": time.time() + 15}
    assert (await controller.execute(frame))["status"] == "success"
    assert (await controller.execute(frame))["status"] == "success"
    assert (await controller.execute({**frame, "button": "電源"}))["status"] == "failed"
    assert api.send_command.await_count == 1
    for override in ({"expires_at": time.time()-1}, {"button": "電源"}, {"id": "b"*32}, {"service": "restart"}):
        assert (await controller.execute({**frame, "request_id": uuid4().hex, **override}))["status"] == "failed"
    assert api.send_command.await_count == 1
    api.send_command.side_effect = RuntimeError("secret must not escape")
    unknown = {**frame, "request_id": uuid4().hex}
    assert (await controller.execute(unknown))["status"] == "unknown"
    await controller.execute(unknown)
    assert api.send_command.await_count == 2


async def test_native_reload_rename_remove_and_unrelated_entities_fail_closed(hass):
    source, native, entry, api, ids = await setup_fan(hass)
    registry = er.async_get(hass)
    registry.async_update_entity(source.entity_id, new_entity_id="switch.renamed_fan")
    assert resolve(hass, source.id)[2].device_id == "remote123"
    replacement_api = SimpleNamespace(send_command=AsyncMock())
    native.runtime_data.api = replacement_api
    await hass.services.async_call("button", "press", {"entity_id": ids["風速-"]}, blocking=True)
    replacement_api.send_command.assert_awaited_once()
    api.send_command.assert_not_awaited()
    with pytest.raises(ValueError):
        select_buttons(hass, ["switch.renamed_fan"])
    registry.async_remove("switch.renamed_fan")
    await hass.async_block_till_done()
    assert hass.states.get(ids["風速-"]).state == "unavailable"
    assert not snapshot(hass, select_buttons(hass, [ids["風速-"]]))[0]["available"]


async def test_config_flow_requires_real_remote_and_no_duplicate_buttons(hass):
    source, native, entry, api, ids = await setup_fan(hass)
    for value in ("風速+,風速+", "", "a,,b"):
        with pytest.raises(ValueError):
            commands(value)
    result = await hass.config_entries.flow.async_init("switchbot_ir_buttons", context={"source": "user"},
        data={"remote_entity": source.entity_id, "name": "新名稱", "buttons": "風速+,風速-"})
    assert result["type"] == "abort" and result["reason"] == "already_configured"
    result = await hass.config_entries.flow.async_init("switchbot_ir_buttons", context={"source": "user"},
        data={"remote_entity": "switch.missing", "name": "未選取", "buttons": "風速+"})
    assert result["errors"]["base"] == "invalid_source"
