"""Expose existing lightLevel samples, without polling or changing native entities."""
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from .source import light_level, resolve
from .push import DIAGNOSTIC_SIGNAL
from homeassistant.helpers.dispatcher import async_dispatcher_connect


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([HubLight(source_id) for source_id in entry.options.get("sources", entry.data["sources"])])


class HubLight(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "光照等級"
    _attr_icon = "mdi:brightness-6"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "級"

    def __init__(self, source_id):
        self.source_id = source_id
        self._attr_unique_id = source_id
        self._coordinator = None
        self._unsubscribe = None

    @property
    def available(self):
        try:
            source, coordinator = resolve(self.hass, self.source_id)
            state = self.hass.states.get(source.entity_id)
            return bool(state and state.state != "unavailable" and coordinator.last_update_success)
        except (ValueError, AttributeError):
            return False

    @property
    def native_value(self):
        try:
            return light_level(resolve(self.hass, self.source_id)[1].data)
        except (ValueError, AttributeError):
            return None

    @property
    def extra_state_attributes(self):
        attributes = {"level_min": 1, "level_max": 20, "source_field": "lightLevel"}
        for manager in self.hass.data.get("switchbot_hub_light", {}).values():
            attributes.update(manager.attributes(self.source_id))
        return attributes

    @callback
    def _bind(self):
        try:
            source, coordinator = resolve(self.hass, self.source_id)
        except (ValueError, AttributeError):
            coordinator = None
        if coordinator is not self._coordinator:
            if self._unsubscribe:
                self._unsubscribe()
            self._coordinator = coordinator
            self._unsubscribe = coordinator.async_add_listener(self._updated) if coordinator else None

    @callback
    def _updated(self):
        self._bind()
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        registry = er.async_get(self.hass)
        source = registry.async_get(self.source_id)
        own = registry.async_get(self.entity_id)
        if source and own and source.device_id:
            registry.async_update_entity(self.entity_id, device_id=source.device_id)
        self._bind()

        @callback
        def diagnostic_changed(source_id):
            if source_id == self.source_id:
                self.async_write_ha_state()
        self.async_on_remove(async_dispatcher_connect(self.hass, DIAGNOSTIC_SIGNAL, diagnostic_changed))

        @callback
        def source_changed(event):
            current = registry.async_get(self.source_id)
            if event.event_type == "entity_registry_updated":
                if event.data.get("entity_id") != self.entity_id:
                    self._updated()
            elif current and event.data.get("entity_id") == current.entity_id:
                # Native reload publishes unavailable then fresh source state;
                # attach to the replacement coordinator instead of the old one.
                self._updated()
        for event_type in ("state_changed", "entity_registry_updated"):
            self.async_on_remove(self.hass.bus.async_listen(event_type, source_changed))

    async def async_will_remove_from_hass(self):
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        self._coordinator = None
