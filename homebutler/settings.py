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


def _keys(env, name) -> dict:
    raw = _text(env, name)
    if not raw:
        return {}
    import json
    try:
        found = json.loads(raw)
    except ValueError:
        raise SettingsError(f"{name} must be JSON like {{\"Mac mini\": \"...\"}}") from None
    if not isinstance(found, dict) or not all(isinstance(k, str) and k and isinstance(v, str) for k, v in found.items()):
        raise SettingsError(f"{name} must map names to keys")
    return found


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
    notion_token: str = field(default="", repr=False)
    # The LINE butler. Without the channel secret and token the webhook refuses everything.
    line_channel_secret: str = field(default="", repr=False)
    line_channel_token: str = field(default="", repr=False)
    anthropic_api_key: str = field(default="", repr=False)
    assistant_model: str = "claude-sonnet-5"
    # Where the Dashboard runs: the system's version number is read from it (the butler can tell it).
    dashboard_url: str = ""
    weather_api_key: str = field(default="", repr=False)      # 中央氣象署 open data
    daily_push_hour: int = 21
    offline_alert_seconds: int = 900
    # The Drive folder (shared with the service account) holding homebutler-backup.json.gz. Unset: no
    # backups, which is what a trial copy must do so it never overwrites the real one.
    backup_folder: str = ""
    # The keys of each caller, made by the family (any long random string) and set here; the Dashboard, HA
    # and each computer get the same value in their own settings. Changing one here retires the old one.
    dashboard_api_key: str = field(default="", repr=False)
    home_assistant_api_key: str = field(default="", repr=False)
    admin_api_key: str = field(default="", repr=False)
    telemetry_keys: dict = field(default_factory=dict, repr=False)     # {"Mac mini": key, ...}
    # The switch-over: bring the old system's data in once, at start, when this is "1" (see migration.py).
    migrate_from_old: bool = False
    old_database_url: str = field(default="", repr=False)    # where the old history is; default: DATABASE_URL
    default_light_area: str = ""                              # the lighting area to-do reminders use by default
    # A trial copy sets SCHEDULES=0: no automatic shutdowns are made and nothing due is sent.
    schedules_enabled: bool = True

    def keys(self) -> list[tuple[str, str, str]]:
        """(kind, label, key) for every key set in the environment."""
        found = [(kind, label, value) for kind, label, value in (
            ("dashboard", "Dashboard", self.dashboard_api_key), ("home_assistant", "Home Assistant", self.home_assistant_api_key),
            ("admin", "Admin", self.admin_api_key)) if value]
        return found + [("telemetry", label, value) for label, value in self.telemetry_keys.items()]

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
            notion_token=_text(env, "NOTION_TOKEN"),
            line_channel_secret=_text(env, "LINE_CHANNEL_SECRET"),
            line_channel_token=_text(env, "LINE_CHANNEL_ACCESS_TOKEN"),
            anthropic_api_key=_text(env, "ANTHROPIC_API_KEY"),
            assistant_model=_text(env, "ASSISTANT_MODEL", cls.assistant_model),
            dashboard_url=_text(env, "DASHBOARD_URL").rstrip("/"),
            weather_api_key=_text(env, "CWA_API_KEY"),
            daily_push_hour=_int(env, "DAILY_PUSH_HOUR", cls.daily_push_hour, 0, 23),
            offline_alert_seconds=_int(env, "AGENT_OFFLINE_ALERT_SECONDS", cls.offline_alert_seconds, 300, 86400),
            backup_folder=_text(env, "BACKUP_FOLDER"),
            dashboard_api_key=_text(env, "DASHBOARD_API_KEY"),
            home_assistant_api_key=_text(env, "HOME_ASSISTANT_API_KEY"),
            admin_api_key=_text(env, "ADMIN_API_KEY"),
            # TELEMETRY_KEY_<NAME>=<key> for each computer (simplest to paste), or all in TELEMETRY_KEYS as JSON.
            telemetry_keys={**_keys(env, "TELEMETRY_KEYS"),
                            **{name[len("TELEMETRY_KEY_"):].replace("_", " ").title(): value.strip()
                               for name, value in env.items() if name.startswith("TELEMETRY_KEY_") and value.strip()}},
            migrate_from_old=_text(env, "MIGRATE_FROM_OLD") == "1",
            old_database_url=_text(env, "OLD_DATABASE_URL"),
            default_light_area=_text(env, "DEFAULT_LIGHT_AREA"),
            schedules_enabled=_text(env, "SCHEDULES", "1") != "0",
        )
        if settings.dashboard_assertion_key and len(settings.dashboard_assertion_key) < 32:
            raise SettingsError("DASHBOARD_ASSERTION_KEY must be at least 32 characters")
        if bool(settings.line_channel_secret) != bool(settings.line_channel_token):
            raise SettingsError("LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN go together")
        if settings.line_channel_secret and not settings.anthropic_api_key:
            raise SettingsError("The LINE butler needs ANTHROPIC_API_KEY")
        lengths = [value for _, _, value in settings.keys()]
        if any(not 24 <= len(value) <= 200 for value in lengths) or len(set(lengths)) != len(lengths):
            raise SettingsError("Every key must be 24 to 200 characters and different from the others")
        if settings.migrate_from_old and not settings.spreadsheet_id:
            raise SettingsError("MIGRATE_FROM_OLD needs SPREADSHEET_ID and GOOGLE_CREDENTIALS")
        if settings.backup_folder and not settings.google_credentials:
            raise SettingsError("BACKUP_FOLDER needs GOOGLE_CREDENTIALS")
        if bool(settings.spreadsheet_id) != bool(settings.google_credentials):
            raise SettingsError("SPREADSHEET_ID and GOOGLE_CREDENTIALS go together")
        if not settings.database_schema.isidentifier() or settings.database_schema.lower() == "public":
            raise SettingsError("DATABASE_SCHEMA must be a plain name other than public")
        if not 1 <= len(settings.default_household) <= 32:
            raise SettingsError("DEFAULT_HOUSEHOLD must be 1 to 32 characters")
        return settings
