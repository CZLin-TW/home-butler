"""Local presentation pairing; independent of the Render outbound integration."""
from homeassistant.const import Platform

DOMAIN = "ac_room_temperature"
PLATFORMS = [Platform.CLIMATE]


async def async_setup_entry(hass, entry):
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
