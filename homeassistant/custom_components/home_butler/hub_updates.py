"""Optional light integration bridge. Never import native API credentials."""
from homeassistant.helpers.dispatcher import async_dispatcher_send

UPDATE_SIGNAL = "home_butler_hub_update"
SOURCES_SIGNAL = "home_butler_hub_sources"


def devices(hass):
    selected = set()
    for manager in hass.data.get("switchbot_hub_light", {}).values():
        selected.update(manager.selected())
    return sorted(selected)[:20]


def receive(hass, frame):
    async_dispatcher_send(hass, UPDATE_SIGNAL, frame)
