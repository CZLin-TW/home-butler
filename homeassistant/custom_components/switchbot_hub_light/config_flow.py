"""Select Hub 2 devices through their existing native temperature entities."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, selector
from . import DOMAIN
from .source import select_sources


def schema(default=None):
    return vol.Schema({vol.Required("hub_sensors", default=default or []):
        selector.EntitySelector(selector.EntitySelectorConfig(multiple=True, filter=[{
            "domain": "sensor", "integration": "switchbot_cloud", "device_class": "temperature"}]))})


class HubLightFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors = {}
        if user_input is not None:
            try:
                sources = select_sources(self.hass, user_input["hub_sensors"])
                return self.async_create_entry(title="Hub 2 光照等級", data={"sources": sources})
            except (ValueError, AttributeError):
                errors["base"] = "invalid_source"
        return self.async_show_form(step_id="user", data_schema=schema(), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HubLightOptions()


class HubLightOptions(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                sources = select_sources(self.hass, user_input["hub_sensors"])
                return self.async_create_entry(title="", data={"sources": sources})
            except (ValueError, AttributeError):
                errors["base"] = "invalid_source"
        entry = self.config_entry
        registry = er.async_get(self.hass)
        selected = [source.entity_id for source_id in entry.options.get("sources", entry.data["sources"])
                    if (source := registry.async_get(source_id))]
        return self.async_show_form(step_id="init", data_schema=schema(selected), errors=errors)
