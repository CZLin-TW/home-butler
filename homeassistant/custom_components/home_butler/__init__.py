"""Selected observations and opt-in native AC control; FP2 pairing is unchanged."""
import asyncio

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_KEY, CONF_SOURCES, CONF_URL, DOMAIN
from .observations import snapshot
from .transport import AuthError, LinkError, OutboundLink, validate_connection
from .climates import ClimateCommands, snapshot as climate_snapshot
from .ir_buttons import IRCommands, snapshot as ir_snapshot


async def async_setup_entry(hass, entry):
    session = async_get_clientsession(hass)
    try:
        await validate_connection(session, entry.data[CONF_URL], entry.data[CONF_KEY])
    except AuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from None
    except LinkError as err:
        raise ConfigEntryNotReady(str(err)) from None
    sources = entry.options.get(CONF_SOURCES, entry.data.get(CONF_SOURCES, []))
    climates = entry.options.get("climates", [])
    buttons = entry.options.get("ir_buttons", [])
    link = OutboundLink(session, entry.data[CONF_URL], entry.data[CONF_KEY], lambda: snapshot(hass, sources),
                        climates=(lambda: climate_snapshot(hass, climates)) if climates else None,
                        commands=ClimateCommands(hass, climates) if climates or buttons else None,
                        ir_buttons=(lambda: ir_snapshot(hass, buttons)) if buttons else None,
                        ir_commands=IRCommands(hass, buttons) if buttons else None)

    @callback
    def changed(event):
        # Only selected states are serialized; other events just wake a coalesced
        # sender when an entity/area is renamed or removed.
        if event.event_type != "state_changed":
            link.notify()
            return
        from .observations import current_entity_ids
        if event.data.get("entity_id") in current_entity_ids(hass, sources + climates + buttons):
            link.notify()

    for event_type in ("state_changed", "entity_registry_updated", "area_registry_updated", "device_registry_updated"):
        entry.async_on_unload(hass.bus.async_listen(event_type, changed))
    task = hass.async_create_background_task(link.run(), "HomeButler outbound link")
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = (link, task)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    link, task = hass.data[DOMAIN].pop(entry.entry_id)
    link.closed = True
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return True
