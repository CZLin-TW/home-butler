"""Read-only Hub 2 light levels using the native Cloud coordinator."""
from homeassistant.const import Platform
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from .push import PushRefresh, UPDATE_SIGNAL, SOURCES_SIGNAL

DOMAIN = "switchbot_hub_light"


async def async_setup_entry(hass, entry):
    manager = PushRefresh(hass, entry.options.get("sources", entry.data["sources"]))
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = manager
    entry.async_on_unload(async_dispatcher_connect(hass, UPDATE_SIGNAL, manager.receive))
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
    async_dispatcher_send(hass, SOURCES_SIGNAL)
    entry.async_on_unload(entry.add_update_listener(reload_entry))
    return True


async def reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR]):
        return False
    manager = hass.data[DOMAIN].pop(entry.entry_id)
    await manager.close()
    async_dispatcher_send(hass, SOURCES_SIGNAL)
    return True
