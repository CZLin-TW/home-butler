"""Local theater switches work independently of Render and HomeButler."""
from homeassistant.const import Platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import TheaterClient
from .const import CONF_KEY, CONF_URL
from .coordinator import TheaterCoordinator

PLATFORMS = [Platform.SWITCH]


async def async_setup_entry(hass, entry):
    config = {**entry.data, **entry.options}
    client = TheaterClient(async_get_clientsession(hass), config[CONF_URL], config[CONF_KEY])
    coordinator = TheaterCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
