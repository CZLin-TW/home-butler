"""Compatibility boundary for native SwitchBot runtime_data; fail closed.

Core 2026.9.2 uses api.send_command(device_id, command, command_type, parameters).
Resolve the selected registry identity and a Remote on every press, including
after a native integration reload. Never look up credentials or use HB as relay.
"""
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from switchbot_api import Remote


def resolve(hass, source_id):
    source = er.async_get(hass).async_get(source_id)
    if not source or source.platform != "switchbot_cloud" or source.disabled_by:
        raise ValueError("Native SwitchBot remote unavailable")
    if source.domain != "switch":
        raise ValueError("Select a native IR switch")
    config = hass.config_entries.async_get_entry(source.config_entry_id)
    if not config or config.state != ConfigEntryState.LOADED:
        raise ValueError("SwitchBot integration unavailable")
    runtime = getattr(config, "runtime_data", None)
    devices = getattr(getattr(runtime, "devices", None), "switches", ())
    matches = [(device, coordinator) for device, coordinator in devices
               if isinstance(device, Remote) and device.device_id == source.unique_id]
    if len(matches) != 1 or not getattr(runtime, "api", None):
        raise ValueError("Native IR remote not found")
    device, coordinator = matches[0]
    if not coordinator.last_update_success:
        raise ValueError("SwitchBot connection unavailable")
    return source, runtime.api, device


def commands(value):
    if not isinstance(value, str):
        raise ValueError("Invalid buttons")
    values = [v.strip() for v in value.split(",")]
    if (not 1 <= len(values) <= 12 or len(set(values)) != len(values)
            or any(not v or len(v) > 60 or any(ord(c) < 32 for c in v) for v in values)):
        raise ValueError("Use distinct comma separated button names")
    return values
