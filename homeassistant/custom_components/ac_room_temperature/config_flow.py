"""One native AC per pairing. Options preserve its identity and native controls."""
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, selector

from . import DOMAIN


def selected(hass, entity_id, *, climate=False):
    entry = er.async_get(hass).async_get(entity_id)
    state = hass.states.get(entity_id)
    if entry is None or state is None:
        raise ValueError("Entity is not registered")
    if climate:
        if not entity_id.startswith("climate.") or entry.platform != "switchbot_cloud":
            raise ValueError("Only native SwitchBot Cloud ACs are supported")
    elif (not entity_id.startswith("sensor.") or state.attributes.get("device_class") != "temperature"
          or state.attributes.get("unit_of_measurement") not in ("°C", "°F")):
        raise ValueError("Select a temperature sensor in Celsius or Fahrenheit")
    return entry.id


def sensor_selector():
    return selector.EntitySelector(selector.EntitySelectorConfig(filter=[
        {"domain": "sensor", "device_class": "temperature"}]))


class PairingFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                source_id = selected(self.hass, user_input["climate_entity"], climate=True)
                sensor_id = selected(self.hass, user_input["temperature_sensor"])
                await self.async_set_unique_id(source_id)
                self._abort_if_unique_id_configured()
                name = self.hass.states.get(user_input["climate_entity"]).name + "（室溫）"
                return self.async_create_entry(title=name, data={"source_id": source_id, "sensor_id": sensor_id})
            except ValueError:
                errors["base"] = "invalid_source"
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("climate_entity"): selector.EntitySelector(selector.EntitySelectorConfig(filter=[
                {"domain": "climate", "integration": "switchbot_cloud"}])),
            vol.Required("temperature_sensor"): sensor_selector(),
        }), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PairingOptions()


class PairingOptions(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        entry = self.config_entry
        if user_input is not None:
            try:
                sensor_id = selected(self.hass, user_input["temperature_sensor"])
                fixed_auto = user_input.get("fixed_auto_fan", entry.options.get("fixed_auto_fan", False))
                if fixed_auto:
                    source = er.async_get(self.hass).async_get(entry.data["source_id"])
                    state = self.hass.states.get(source.entity_id) if source else None
                    if not state or "auto" not in (state.attributes.get("fan_modes") or []):
                        return self.async_show_form(step_id="init", data_schema=self._schema(entry),
                            errors={"base": "auto_fan_unavailable"})
                return self.async_create_entry(title="", data={"sensor_id": sensor_id, "fixed_auto_fan": fixed_auto})
            except ValueError:
                errors["base"] = "invalid_source"
        return self.async_show_form(step_id="init", data_schema=self._schema(entry), errors=errors)

    def _schema(self, entry):
        sensor_id = entry.options.get("sensor_id", entry.data["sensor_id"])
        sensor = er.async_get(self.hass).async_get(sensor_id)
        field = vol.Required("temperature_sensor", default=sensor.entity_id) if sensor else vol.Required("temperature_sensor")
        return vol.Schema({field: sensor_selector(),
            vol.Optional("fixed_auto_fan", default=entry.options.get("fixed_auto_fan", False)): bool})
