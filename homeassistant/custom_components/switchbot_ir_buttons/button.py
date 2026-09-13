"""Momentary learned IR commands, never a guessed fan state."""
import asyncio
import hashlib

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.core import callback

from .driver import resolve


async def async_setup_entry(hass, entry, async_add_entities):
    lock = asyncio.Lock()
    async_add_entities([IRButton(entry, label, lock) for label in entry.data["buttons"]])


class IRButton(ButtonEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:remote"

    def __init__(self, entry, label, lock):
        self.entry, self.label, self.lock = entry, label, lock
        self._attr_unique_id = entry.data["source_id"] + "_" + hashlib.sha256(label.encode()).hexdigest()[:16]
        self._attr_name = entry.data["name"] + " " + label

    @property
    def available(self):
        try:
            resolve(self.hass, self.entry.data["source_id"])
            return True
        except (ValueError, AttributeError):
            return False

    @property
    def extra_state_attributes(self):
        return {"butler_device_name": self.entry.data["name"], "ir_button": self.label,
                "state_source": "command_only"}

    async def async_added_to_hass(self):
        source = er.async_get(self.hass).async_get(self.entry.data["source_id"])
        # Group with the native device (and its room), without claiming its state.
        own = er.async_get(self.hass).async_get(self.entity_id)
        if source and own and source.device_id:
            er.async_get(self.hass).async_update_entity(self.entity_id, device_id=source.device_id)

        @callback
        def changed(event):
            self.async_write_ha_state()
        for event in ("state_changed", "entity_registry_updated", "config_entry_changed"):
            # Only wake on the selected native source; avoid self-state loops.
            @callback
            def selected_changed(event):
                current = er.async_get(self.hass).async_get(self.entry.data["source_id"])
                if event.event_type != "state_changed" or (current and event.data.get("entity_id") == current.entity_id):
                    changed(event)
            self.async_on_remove(self.hass.bus.async_listen(event, selected_changed))

    async def async_press(self):
        if self.lock.locked():
            raise HomeAssistantError("上一個遙控指令處理中，請稍後再按")
        async with self.lock:
            try:
                _, api, device = resolve(self.hass, self.entry.data["source_id"])
            except (ValueError, AttributeError):
                raise HomeAssistantError("SwitchBot 遙控來源無法使用，未送出") from None
            try:
                async with asyncio.timeout(20):
                    # Preserve the existing Dashboard power-button mapping. "電源"
                    # is a remote key, not reliable knowledge of on/off state.
                    on = {"電源", "開", "開機", "turn on", "turnon", "power on", "on"}
                    off = {"關", "關機", "turn off", "turnoff", "power off", "off"}
                    key = self.label.lower().strip()
                    command = "turnOn" if key in on else "turnOff" if key in off else self.label
                    kind = "command" if key in on or key in off else "customize"
                    await api.send_command(device.device_id, command, kind, "default")
            except Exception:
                raise HomeAssistantError("紅外線指令結果未確認，不會自動重送") from None
