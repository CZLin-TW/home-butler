"""Every setting the service reads, with its type, in one place.

Read once at startup; a value that is present but invalid stops the service with a message naming the
variable (never the value). Secrets are only ever read here and passed on to the code that needs them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping


class SettingsError(ValueError):
    pass


def _text(env, name, default=""):
    return (env.get(name) or default).strip()


def _int(env, name, default, low, high):
    raw = _text(env, name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SettingsError(f"{name} must be a whole number") from None
    if not low <= value <= high:
        raise SettingsError(f"{name} must be between {low} and {high}")
    return value


@dataclass(frozen=True)
class Settings:
    # Where the data lives. Without it the service runs on a local SQLite file (development, tests).
    database_url: str = "sqlite:///homebutler.db"
    # PostgreSQL only: the schema the tables live in, apart from the old system's tables in "public".
    database_schema: str = "hb"
    # Google: the spreadsheet that still holds the four hand-edited configuration tabs.
    spreadsheet_id: str = ""
    google_credentials: str = field(default="", repr=False)
    # How often to ask Google whether that spreadsheet changed (a cheap metadata read).
    config_check_seconds: int = 60
    # The household new members join until they are given another one.
    default_household: str = "A"
    # Shared with the Dashboard: it signs a short-lived assertion of who the user is.
    dashboard_assertion_key: str = field(default="", repr=False)
    timezone: str = "Asia/Taipei"
    # SwitchBot (only to point its webhook here; HA does all device work). Registration is off unless asked,
    # because an account has one webhook address: a trial copy must never take it from the live service.
    switchbot_token: str = field(default="", repr=False)
    switchbot_secret: str = field(default="", repr=False)
    switchbot_register_webhook: bool = False
    public_base_url: str = ""
    notion_token: str = field(default="", repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        settings = cls(
            database_url=_text(env, "DATABASE_URL", cls.database_url),
            database_schema=_text(env, "DATABASE_SCHEMA", cls.database_schema),
            spreadsheet_id=_text(env, "SPREADSHEET_ID"),
            google_credentials=_text(env, "GOOGLE_CREDENTIALS"),
            config_check_seconds=_int(env, "CONFIG_CHECK_SECONDS", cls.config_check_seconds, 15, 3600),
            default_household=_text(env, "DEFAULT_HOUSEHOLD", cls.default_household),
            dashboard_assertion_key=_text(env, "DASHBOARD_ASSERTION_KEY"),
            timezone=_text(env, "TZ_NAME", cls.timezone),
            switchbot_token=_text(env, "SWITCHBOT_TOKEN"),
            switchbot_secret=_text(env, "SWITCHBOT_SECRET"),
            switchbot_register_webhook=_text(env, "SWITCHBOT_REGISTER_WEBHOOK") == "1",
            public_base_url=_text(env, "PUBLIC_BASE_URL").rstrip("/"),
            notion_token=_text(env, "NOTION_TOKEN"),
        )
        if settings.switchbot_register_webhook and not (settings.switchbot_token and settings.switchbot_secret
                                                         and settings.public_base_url.startswith("https://")):
            raise SettingsError("SWITCHBOT_REGISTER_WEBHOOK needs SWITCHBOT_TOKEN, SWITCHBOT_SECRET and an https PUBLIC_BASE_URL")
        if settings.dashboard_assertion_key and len(settings.dashboard_assertion_key) < 32:
            raise SettingsError("DASHBOARD_ASSERTION_KEY must be at least 32 characters")
        if bool(settings.spreadsheet_id) != bool(settings.google_credentials):
            raise SettingsError("SPREADSHEET_ID and GOOGLE_CREDENTIALS go together")
        if not settings.database_schema.isidentifier() or settings.database_schema.lower() == "public":
            raise SettingsError("DATABASE_SCHEMA must be a plain name other than public")
        if not 1 <= len(settings.default_household) <= 32:
            raise SettingsError("DEFAULT_HOUSEHOLD must be 1 to 32 characters")
        return settings
