"""Mirror a native IR AC plus a selectable room thermometer.

Sensor events only publish state. Commands always target the native registry ID,
with no Render dependency, feedback control, retries, or state overwrites.
"""
import asyncio
import math

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_conversion import TemperatureConverter

MODES = [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT, HVACMode.DRY, HVACMode.FAN_ONLY, HVACMode.HEAT_COOL]


async def async_setup_entry(hass, entry, async_add_entities):
    entity = RoomTemperatureClimate(entry)
    entry.runtime_data = entity
    async_add_entities([entity])


def finite(value):
    try:
        number = float(value)
        return number if not isinstance(value, bool) and math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


class RoomTemperatureClimate(ClimateEntity):
    _attr_should_poll = False
    _attr_assumed_state = True  # IR offers no physical power/compressor readback.
    _attr_target_temperature_step = 1
    _attr_supported_features = (ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE
                                | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF)

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = entry.data["source_id"]
        self._attr_name = entry.title
        self._source = None
        self._temperature = None
        self._source_entity_id = None
        self._sensor_entity_id = None

    def _resolve(self, id):
        return er.async_get(self.hass).async_get(id)

    @callback
    def _refresh(self):
        source = self._resolve(self.entry.data["source_id"])
        sensor = self._resolve(self.entry.options.get("sensor_id", self.entry.data["sensor_id"]))
        self._source_entity_id = source.entity_id if source else None
        self._sensor_entity_id = sensor.entity_id if sensor else None
        self._source = self.hass.states.get(source.entity_id) if source and source.platform == "switchbot_cloud" else None
        reading = self.hass.states.get(sensor.entity_id) if sensor else None
        self._temperature = None
        if reading and reading.attributes.get("device_class") == "temperature":
            value = finite(reading.state)
            unit = reading.attributes.get("unit_of_measurement")
            if value is not None and unit in ("°C", "°F"):
                celsius = TemperatureConverter.convert(value, unit, "°C")
                if -50 <= celsius <= 100:
                    self._temperature = TemperatureConverter.convert(value, unit, self.temperature_unit)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        @callback
        def changed(event):
            if event.event_type == "state_changed" and event.data.get("entity_id") not in (
                    self._source_entity_id, self._sensor_entity_id):
                return
            self._refresh()
            self.async_write_ha_state()
        for event in ("state_changed", "entity_registry_updated"):
            self.async_on_remove(self.hass.bus.async_listen(event, changed))
        self._refresh()

    @property
    def available(self):
        # HomeKit otherwise retains its default 21°C or an old sensor reading.
        return bool(self._source and self._source.state in MODES and self._temperature is not None)

    @property
    def temperature_unit(self):
        return self.hass.config.units.temperature_unit

    @property
    def current_temperature(self):
        return self._temperature

    @property
    def hvac_mode(self):
        return HVACMode(self._source.state) if self._source and self._source.state in MODES else None

    @property
    def hvac_modes(self):
        return [mode for mode in MODES if mode in self._attr("hvac_modes", MODES)]

    def _attr(self, name, default=None):
        return self._source.attributes.get(name, default) if self._source else default

    @property
    def target_temperature(self):
        return finite(self._attr("temperature"))

    @property
    def min_temp(self):
        return max(self._attr("min_temp", TemperatureConverter.convert(16, "°C", self.temperature_unit)),
                   TemperatureConverter.convert(16, "°C", self.temperature_unit))

    @property
    def max_temp(self):
        return min(self._attr("max_temp", TemperatureConverter.convert(30, "°C", self.temperature_unit)),
                   TemperatureConverter.convert(30, "°C", self.temperature_unit))

    @property
    def fan_mode(self):
        return self._attr("fan_mode")

    @property
    def fan_modes(self):
        return self._attr("fan_modes", ["auto", "low", "medium", "high"])

    @property
    def extra_state_attributes(self):
        return {"source_climate": self._source_entity_id, "room_temperature_sensor": self._sensor_entity_id,
                "state_source": "native_ir_last_command"}

    async def _call(self, service, data=None):
        # Resolve again at dispatch; never address a newly created namesake.
        self._refresh()
        if not self._source or self._source.state not in MODES:
            raise HomeAssistantError("原生空調目前無法使用")
        try:
            async with asyncio.timeout(25):
                await self.hass.services.async_call("climate", service,
                    {"entity_id": self._source_entity_id, **(data or {})}, blocking=True, context=self._context)
        except Exception:
            raise HomeAssistantError("空調指令結果未確認，請查看設備；不會自動重送") from None
        self._refresh()
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode not in self.hvac_modes:
            raise HomeAssistantError("空調模式不支援")
        await self._call("set_hvac_mode", {"hvac_mode": hvac_mode})

    async def async_set_temperature(self, **kwargs):
        value = finite(kwargs.get("temperature"))
        if value is None or not self.min_temp <= value <= self.max_temp:
            raise HomeAssistantError("目標溫度超出範圍")
        celsius = TemperatureConverter.convert(value, self.temperature_unit, "°C")
        data = {"temperature": TemperatureConverter.convert(math.floor(celsius + 0.5), "°C", self.temperature_unit)}
        if "hvac_mode" in kwargs:
            if kwargs["hvac_mode"] not in self.hvac_modes:
                raise HomeAssistantError("空調模式不支援")
            data["hvac_mode"] = kwargs["hvac_mode"]
        await self._call("set_temperature", data)

    async def async_set_fan_mode(self, fan_mode):
        if fan_mode not in self.fan_modes:
            raise HomeAssistantError("空調風速不支援")
        await self._call("set_fan_mode", {"fan_mode": fan_mode})

    async def async_turn_on(self):
        await self._call("turn_on")

    async def async_turn_off(self):
        await self._call("turn_off")
