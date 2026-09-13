"""Local IR buttons using the already configured native SwitchBot connection.

No Render dependency, credential copy, power/percentage inference or polling.
"""
from homeassistant.const import Platform

DOMAIN = "switchbot_ir_buttons"


async def async_setup_entry(hass, entry):
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.BUTTON])
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, [Platform.BUTTON])
