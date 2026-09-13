"""Explicit native remote + stable HomeButler name + exact learned buttons."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import entity_registry as er, selector
from . import DOMAIN
from .driver import resolve, commands


class IRButtonFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                source = er.async_get(self.hass).async_get(user_input["remote_entity"])
                if source is None:
                    raise ValueError()
                resolve(self.hass, source.id)
                name = user_input["name"].strip()
                if not name or len(name) > 160:
                    raise ValueError()
                if any(e.data.get("name") == name for e in self._async_current_entries()):
                    raise ValueError()
                labels = commands(user_input["buttons"])
                await self.async_set_unique_id(source.id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=name, data={
                    "source_id": source.id, "name": name, "buttons": labels})
            except (ValueError, AttributeError):
                errors["base"] = "invalid_source"
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("remote_entity"): selector.EntitySelector(selector.EntitySelectorConfig(
                filter=[{"domain": "switch", "integration": "switchbot_cloud"}])),
            vol.Required("name"): str,
            vol.Required("buttons", default="電源,風速+,風速-"): str,
        }), errors=errors)
