"""One authority for polls, HA switches and HomeButler requests."""
import asyncio
from datetime import timedelta
import logging
import time

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import TheaterAuthError, TheaterError, TheaterUnknownError, validate_flags
from .const import POLL_SECONDS

LOGGER = logging.getLogger(__name__)


class TheaterCommandError(HomeAssistantError):
    def __init__(self, message, status="failed"):
        super().__init__(message)
        self.status = status


class TheaterCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, client):
        super().__init__(hass, LOGGER, name="Theater Agent", config_entry=entry,
                         update_interval=timedelta(seconds=POLL_SECONDS))
        self.client = client
        self.command_lock = asyncio.Lock()

    async def _read(self):
        try:
            return await self.client.async_summary()
        except TheaterAuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from None
        except TheaterError as exc:
            raise UpdateFailed(str(exc)) from None

    async def _async_update_data(self):
        # DataUpdateCoordinator publishes the returned value without another
        # await, before a waiting command can acquire this lock.
        async with self.command_lock:
            return await self._read()

    async def async_get_summary(self):
        async with self.command_lock:
            try:
                data = await self._read()
            except (UpdateFailed, ConfigEntryAuthFailed) as exc:
                self.async_set_update_error(exc)
                raise TheaterCommandError(str(exc)) from None
            self.async_set_updated_data(data)
            return data

    async def async_set_flags(self, flags, *, expires_at=None):
        try:
            flags = validate_flags(flags)
        except ValueError as exc:
            raise TheaterCommandError(str(exc)) from None
        # Refuse concurrent writes rather than queueing stale user intentions.
        if self.command_lock.locked():
            raise TheaterCommandError("Theater is busy; no command was sent")
        async with self.command_lock:
            if expires_at is not None and time.time() >= expires_at:
                raise TheaterCommandError("Theater command expired; no command was sent")
            wrote = False
            try:
                await self.client.async_set_flags(flags)
                wrote = True
                data = await self._read()
            except asyncio.CancelledError:
                self.async_set_update_error(UpdateFailed("Theater write was interrupted; read current flags"))
                raise
            except (TheaterError, UpdateFailed, ConfigEntryAuthFailed) as exc:
                self.async_set_update_error(UpdateFailed(str(exc)))
                unknown = wrote or isinstance(exc, TheaterUnknownError)
                raise TheaterCommandError(str(exc), "unknown" if unknown else "failed") from None
            self.async_set_updated_data(data)
            if any(data["flags"][key] != value for key, value in flags.items()):
                raise TheaterCommandError("Theater readback differs; check the current flags", "unknown")
            return {"success": True, "flags": dict(data["flags"])}
