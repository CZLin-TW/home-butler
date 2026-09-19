"""Flags describe automation enablement, not power or process state."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, FLAGS

PARALLEL_UPDATES = 0  # The shared coordinator owns concurrency.


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(TheaterSwitch(entry, flag) for flag in FLAGS)


class TheaterSwitch(CoordinatorEntity, SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self, entry, flag):
        super().__init__(entry.runtime_data)
        self.flag = flag
        self._attr_unique_id = f"{entry.entry_id}_{flag}"
        self._attr_translation_key = flag
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name="Theater Agent",
            manufacturer="HomeButler", model="Local theater controller",
        )

    @property
    def is_on(self):
        if not self.coordinator.last_update_success or not self.coordinator.data:
            return None
        return self.coordinator.data["flags"][self.flag]

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_flags({self.flag: True})

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_flags({self.flag: False})
