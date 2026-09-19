"""One local controller; changing its address preserves entity identity."""
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import TheaterAuthError, TheaterClient, TheaterError, normalize_url
from .const import CONF_KEY, CONF_URL, DOMAIN


def form_schema(config=None):
    config = config or {}
    return vol.Schema({
        vol.Required(CONF_URL, default=config.get(CONF_URL, "")): str,
        vol.Required(CONF_KEY, default=config.get(CONF_KEY, "")):
            selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
    })


async def validate(hass, data):
    result = {CONF_URL: normalize_url(data[CONF_URL]), CONF_KEY: data[CONF_KEY].strip()}
    client = TheaterClient(async_get_clientsession(hass), result[CONF_URL], result[CONF_KEY])
    await client.async_summary()  # Never change flags during setup or options.
    return result


class TheaterFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors = {}
        if user_input is not None:
            try:
                data = await validate(self.hass, user_input)
                return self.async_create_entry(title="Theater Agent", data=data)
            except TheaterAuthError:
                errors["base"] = "invalid_auth"
            except TheaterError:
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["base"] = "invalid_input"
        return self.async_show_form(step_id="user", data_schema=form_schema(user_input), errors=errors)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self._get_reauth_entry()
        config = {**entry.data, **entry.options}
        errors = {}
        if user_input is not None:
            try:
                data = await validate(self.hass, user_input)
                return self.async_update_reload_and_abort(entry, data_updates=data, options_updates=data)
            except TheaterAuthError:
                errors["base"] = "invalid_auth"
            except TheaterError:
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["base"] = "invalid_input"
        return self.async_show_form(step_id="reauth_confirm", data_schema=form_schema(config), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return TheaterOptions()


class TheaterOptions(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                data = await validate(self.hass, user_input)
                return self.async_create_entry(title="", data=data)
            except TheaterAuthError:
                errors["base"] = "invalid_auth"
            except TheaterError:
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["base"] = "invalid_input"
        config = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=form_schema(config), errors=errors)
