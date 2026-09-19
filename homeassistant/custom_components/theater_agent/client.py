"""Fixed local API, with no retries or optimistic write results."""
import asyncio
from urllib.parse import urlsplit

import aiohttp

from .const import FLAGS, REQUEST_TIMEOUT


class TheaterError(Exception):
    """A safe message, never a response body, URL or credential."""


class TheaterAuthError(TheaterError):
    pass


class TheaterUnknownError(TheaterError):
    """A write may have reached the agent. Read again; never replay it."""


def normalize_url(value):
    value = str(value).strip()
    parts = urlsplit(value)
    if (parts.scheme not in ("http", "https") or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in ("", "/") or any(c.isspace() for c in value)):
        raise ValueError("Use an HTTP(S) origin without credentials or a path")
    parts.port  # Validate the optional port as well.
    return value.rstrip("/")


def validate_flags(value, *, complete=False):
    if (not isinstance(value, dict) or not value or set(value) - set(FLAGS)
            or any(type(v) is not bool for v in value.values())
            or (complete and set(value) != set(FLAGS))):
        raise ValueError("Expected known boolean theater flags")
    return dict(value)


class TheaterClient:
    def __init__(self, session, url, key):
        self.session, self.url, self.key = session, normalize_url(url), key
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Theater API key is required")

    async def _request(self, method, path, body=None):
        error = TheaterUnknownError if method == "POST" else TheaterError
        try:
            async with self.session.request(
                method, self.url + path, json=body,
                headers={"x-api-key": self.key}, allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                if response.status in (401, 403):
                    raise TheaterAuthError("Theater authentication failed")
                if response.status != 200:
                    raise error("Theater API did not confirm the request")
                data = await response.json()
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            raise error("Theater API did not return a valid response") from None

    async def async_summary(self):
        data = await self._request("GET", "/summary")
        try:
            validate_flags(data.get("flags"), complete=True)
        except ValueError:
            raise TheaterError("Theater returned invalid flags") from None
        health = data.get("health", {})
        if not isinstance(health, dict):
            raise TheaterError("Theater returned invalid health")
        if health.get("flags_error"):
            raise TheaterError("Theater reports a configuration error")
        return data

    async def async_set_flags(self, flags):
        flags = validate_flags(flags)
        data = await self._request("POST", "/flags", flags)
        try:
            actual = validate_flags(data.get("flags"), complete=True)
        except ValueError:
            raise TheaterUnknownError("Theater write result is unknown") from None
        if data.get("success") is not True or any(actual[k] != v for k, v in flags.items()):
            raise TheaterUnknownError("Theater did not confirm the requested flags")
