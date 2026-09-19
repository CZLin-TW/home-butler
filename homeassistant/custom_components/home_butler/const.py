"""Observation protocol with opt-in climate_control capability."""
DOMAIN = "home_butler"
CONF_URL = "url"
CONF_KEY = "api_key"
CONF_EXPORT = "export_entities"
CONF_SOURCES = "sources"
# Local address/key of the theater agent; never sent over the link.
CONF_THEATER_URL = "theater_url"
CONF_THEATER_KEY = "theater_key"
CONF_THEATER_ENTRY = "theater_entry"
PROTOCOL = 1
HEARTBEAT_SECONDS = 30
