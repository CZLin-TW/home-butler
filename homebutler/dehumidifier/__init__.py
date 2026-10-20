"""Dehumidifiers (Panasonic, LG), kept apart so the whole module can be removed later.

They stay in this service and not in Home Assistant because they are to be retired (docs/refactor-plan.md
§8). Nothing outside this folder imports it; `install` adds its routes, its 5-minute check and its entry
in app.state.modules. To remove it: delete this folder and its line in homebutler/modules.py, and add a
migration that drops its two tables.

Its settings, read here only:
  PANASONIC_ACCOUNT, PANASONIC_PASSWORD   the Panasonic Smart App account
  PANASONIC_DEVICE_AUTH                   JSON {"<Device ID>": "<the unit's auth value>"}
  LG_PAT, LG_COUNTRY (TW), LG_CLIENT_ID, LG_API_BASE   LG ThinQ Connect
"""
from __future__ import annotations

import json
import os
from zoneinfo import ZoneInfo

import httpx

from homebutler.settings import SettingsError

CHECK_SECONDS = 300


def _device_auth(env) -> dict[str, str]:
    raw = (env.get("PANASONIC_DEVICE_AUTH") or "").strip()
    if not raw:
        return {}
    try:
        found = json.loads(raw)
    except ValueError:
        raise SettingsError("PANASONIC_DEVICE_AUTH must be JSON") from None
    if not isinstance(found, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in found.items()):
        raise SettingsError("PANASONIC_DEVICE_AUTH must map device ids to text")
    return found


def build(store, link, settings, env=None, http=None):
    from homebutler.dehumidifier.lg import LGClient
    from homebutler.dehumidifier.panasonic import PanasonicClient
    from homebutler.dehumidifier.service import Dehumidifiers
    env = os.environ if env is None else env
    http = http or httpx.Client(timeout=20)
    panasonic = PanasonicClient((env.get("PANASONIC_ACCOUNT") or "").strip(), env.get("PANASONIC_PASSWORD") or "", http)
    lg = LGClient((env.get("LG_PAT") or "").strip(), (env.get("LG_COUNTRY") or "TW").strip(),
                  (env.get("LG_CLIENT_ID") or "home-butler-client").strip(), http, (env.get("LG_API_BASE") or "").strip())
    return Dehumidifiers(store, link, panasonic, lg, _device_auth(env), ZoneInfo(settings.timezone))


def install(app, jobs, env=None, http=None) -> None:
    from homebutler.dehumidifier import routes
    service = build(app.state.store, app.state.ha, app.state.settings, env, http)
    app.state.modules["dehumidifier"] = service
    app.include_router(routes.router)
    jobs.add("dehumidifiers", CHECK_SECONDS, service.check)
