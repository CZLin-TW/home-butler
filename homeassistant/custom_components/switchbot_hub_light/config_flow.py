"""Select Hub 2 devices through their existing native temperature entities."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, selector
from . import DOMAIN
from .source import select_sources
from .const import DEFAULT_POLL_INTERVAL, poll_interval


def schema(default=None, interval=DEFAULT_POLL_INTERVAL):
    return vol.Schema({vol.Required("hub_sensors", default=default or []):
        selector.EntitySelector(selector.EntitySelectorConfig(multiple=True, filter=[{
            "domain": "sensor", "integration": "switchbot_cloud", "device_class": "temperature"}])),
        vol.Required("poll_interval", default=interval): selector.NumberSelector(
            selector.NumberSelectorConfig(min=60, max=3600, step=1, mode="box", unit_of_measurement="s"))})


class HubLightFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors = {}
        if user_input is not None:
            try:
                sources = select_sources(self.hass, user_input["hub_sensors"])
                interval = poll_interval(user_input.get("poll_interval", DEFAULT_POLL_INTERVAL))
                return self.async_create_entry(title="Hub 2 光照等級", data={"sources": sources, "poll_interval": interval})
            except (ValueError, AttributeError) as err:
                errors["base"] = "invalid_poll_interval" if str(err) == "invalid_poll_interval" else "invalid_source"
        return self.async_show_form(step_id="user", data_schema=schema(), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HubLightOptions()


class HubLightOptions(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        entry = self.config_entry
        interval = entry.options.get("poll_interval", entry.data.get("poll_interval", DEFAULT_POLL_INTERVAL))
        if user_input is not None:
            try:
                sources = select_sources(self.hass, user_input["hub_sensors"])
                interval = poll_interval(user_input.get("poll_interval", interval))
                return self.async_create_entry(title="", data={"sources": sources, "poll_interval": interval})
            except (ValueError, AttributeError) as err:
                errors["base"] = "invalid_poll_interval" if str(err) == "invalid_poll_interval" else "invalid_source"
        registry = er.async_get(self.hass)
        selected = [source.entity_id for source_id in entry.options.get("sources", entry.data["sources"])
                    if (source := registry.async_get(source_id))]
        return self.async_show_form(step_id="init", data_schema=schema(selected, interval), errors=errors)
