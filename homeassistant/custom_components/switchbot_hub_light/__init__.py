"""Read-only Hub 2 light levels using the native Cloud coordinator."""
from homeassistant.const import Platform

DOMAIN = "switchbot_hub_light"


async def async_setup_entry(hass, entry):
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
    entry.async_on_unload(entry.add_update_listener(reload_entry))
    return True


async def reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR])
