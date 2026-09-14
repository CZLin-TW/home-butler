"""Configure only the backend URL/key and explicitly shared sensors."""
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_EXPORT, CONF_KEY, CONF_SOURCES, CONF_THEATER_KEY, CONF_THEATER_URL, CONF_URL, DOMAIN
from .observations import current_entity_ids, select_sources
from .transport import AuthError, LinkError, normalize_url, validate_connection
from .climates import select_climates
from .ir_buttons import select_buttons
from .environment import select_environment, current_mapping
from .hue import group_options, select_groups
from .theater import normalize_url as normalize_theater_url


def export_selector():
    return selector.EntitySelector(selector.EntitySelectorConfig(multiple=True, filter=[
        {"domain": "binary_sensor", "device_class": ["occupancy", "presence"]},
        {"domain": "sensor", "device_class": "illuminance"},
    ]))


def password():
    return selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))


class HomeButlerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                url = normalize_url(user_input[CONF_URL])
                sources = select_sources(self.hass, user_input.get(CONF_EXPORT, []))
                if len(user_input[CONF_KEY]) < 32:
                    raise AuthError()
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                await validate_connection(async_get_clientsession(self.hass), url, user_input[CONF_KEY])
                return self.async_create_entry(title="Home Butler", data={
                    CONF_URL: url, CONF_KEY: user_input[CONF_KEY], CONF_SOURCES: sources})
            except AuthError:
                errors["base"] = "invalid_auth"
            except LinkError:
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["base"] = "invalid_input"
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required(CONF_URL): str, vol.Required(CONF_KEY): password(),
            vol.Optional(CONF_EXPORT, default=[]): export_selector(),
        }), errors=errors)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        entry = self._get_reauth_entry()
        if user_input:
            try:
                await validate_connection(async_get_clientsession(self.hass), entry.data[CONF_URL], user_input[CONF_KEY])
                return self.async_update_reload_and_abort(entry, data_updates={CONF_KEY: user_input[CONF_KEY]})
            except AuthError:
                errors["base"] = "invalid_auth"
            except LinkError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({
            vol.Required(CONF_KEY): password(),
        }), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HomeButlerOptionsFlow()


class HomeButlerOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        entry = self.config_entry
        if user_input is not None:
            try:
                sources = select_sources(self.hass, user_input.get(CONF_EXPORT, []))
                climates = select_climates(self.hass, user_input.get("climate_entities", []), entry.options.get("climates", []))
                buttons = select_buttons(self.hass, user_input.get("ir_entities", []))
                environment = select_environment(self.hass, user_input.get("sensor_mapping", current_mapping(self.hass, entry.options.get("environment", []))))
                groups = await select_groups(self.hass, user_input.get("hue_groups", entry.options.get("hue_groups", [])), entry.options.get("hue_groups", []))
                theater_url = str(user_input.get(CONF_THEATER_URL, "")).strip()
                theater_key = str(user_input.get(CONF_THEATER_KEY, "")).strip()
                if theater_url:
                    theater_url = normalize_theater_url(theater_url)
                    if not theater_key:
                        raise ValueError("Theater key required")
                return self.async_create_entry(title="", data={CONF_SOURCES: sources, "climates": climates, "ir_buttons": buttons, "environment": environment, "hue_groups": groups,
                                                               CONF_THEATER_URL: theater_url, CONF_THEATER_KEY: theater_key if theater_url else ""})
            except ValueError:
                errors["base"] = "invalid_input"
        sources = entry.options.get(CONF_SOURCES, entry.data.get(CONF_SOURCES, []))
        hue_options = await group_options(self.hass, entry.options.get("hue_groups", []))
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Optional(CONF_EXPORT, default=current_entity_ids(self.hass, sources)): export_selector(),
            vol.Optional("sensor_mapping", default=current_mapping(self.hass, entry.options.get("environment", []))): selector.ObjectSelector(),
            vol.Optional("hue_groups", default=entry.options.get("hue_groups", [])):
                selector.SelectSelector(selector.SelectSelectorConfig(multiple=True, options=hue_options)),
            vol.Optional("climate_entities", default=current_entity_ids(self.hass, entry.options.get("climates", []))):
                selector.EntitySelector(selector.EntitySelectorConfig(multiple=True, filter=[
                    {"domain": "climate", "integration": "switchbot_cloud"}])),
            vol.Optional("ir_entities", default=current_entity_ids(self.hass, entry.options.get("ir_buttons", []))):
                selector.EntitySelector(selector.EntitySelectorConfig(multiple=True, filter=[
                    {"domain": "button", "integration": "switchbot_ir_buttons"}])),
            vol.Optional(CONF_THEATER_URL, default=entry.options.get(CONF_THEATER_URL, "")): str,
            vol.Optional(CONF_THEATER_KEY, default=entry.options.get(CONF_THEATER_KEY, "")): password(),
        }), errors=errors)
