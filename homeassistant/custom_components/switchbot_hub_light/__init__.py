"""Read-only Hub 2 light levels using the native Cloud coordinator."""
from homeassistant.const import Platform
from .poll import PollRefresh
from .const import DEFAULT_POLL_INTERVAL

DOMAIN = "switchbot_hub_light"


async def async_setup_entry(hass, entry):
    manager = PollRefresh(hass, entry.options.get("sources", entry.data["sources"]),
                          entry.options.get("poll_interval", entry.data.get("poll_interval", DEFAULT_POLL_INTERVAL)))
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = manager
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
    manager.start()
    entry.async_on_unload(entry.add_update_listener(reload_entry))
    return True


async def reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR]):
        return False
    manager = hass.data[DOMAIN].pop(entry.entry_id)
    await manager.close()
    return True
