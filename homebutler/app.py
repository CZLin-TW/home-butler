"""The service. Run with: uvicorn homebutler.app:create_app --factory

Startup brings the database to the newest structure, makes sure the default household exists and
starts the background jobs; a request never waits for any of the slow outside services.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from homebutler.api import devices_relay, home_assistant, schedules, telemetry
from homebutler.api.auth import Caller, caller
from homebutler.domain import households
from homebutler.integrations.ha.link import Link
from homebutler.jobs.runner import Jobs
from homebutler.settings import Settings
from homebutler.store import migrate
from homebutler.store.db import Store, make_engine

log = logging.getLogger("homebutler")


def create_app(settings: Settings | None = None, store: Store | None = None, sheets=None, start_jobs: bool = True) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store or Store(make_engine(settings.database_url, settings.database_schema))
    jobs = Jobs()
    if sheets is None and settings.spreadsheet_id:
        from homebutler.integrations.sheets_config.source import GoogleSheetsSource
        sheets = GoogleSheetsSource(settings.spreadsheet_id, settings.google_credentials)
    if sheets is not None:
        from homebutler.integrations.sheets_config.sync import ConfigSync
        config = ConfigSync(store, sheets, settings.default_household)
        jobs.add("config", settings.config_check_seconds, config.run)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio
        app.state.loop = asyncio.get_running_loop()
        migrate.upgrade(store.engine, settings.database_schema)
        with store.transaction() as session:
            households.ensure(session, settings.default_household)
        if settings.switchbot_register_webhook:
            import threading
            def point_webhook_here():
                import httpx
                from homebutler.integrations.switchbot_push import register
                try:
                    log.info("SwitchBot webhook: %s", register(settings.public_base_url + "/v2/switchbot/webhook",
                                                              settings.switchbot_token, settings.switchbot_secret, httpx.Client()))
                except Exception as error:
                    log.warning("SwitchBot webhook not registered (%s)", type(error).__name__)
            threading.Thread(target=point_webhook_here, daemon=True).start()
        if start_jobs:
            jobs.start()
        yield
        jobs.stop()
        store.close()

    link = Link()
    from homebutler.jobs import devices as device_jobs
    jobs.add("devices", 60, lambda: device_jobs.sync_registry(store, link, settings.default_household))
    jobs.add("history", 300, lambda: device_jobs.sample_history(store, link))

    app = FastAPI(title="HomeButler", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.store, app.state.jobs = settings, store, jobs
    app.state.ha = link
    app.include_router(home_assistant.router)
    app.include_router(telemetry.router)
    app.include_router(devices_relay.router)
    app.include_router(schedules.router)
    from homebutler.integrations.switchbot_push import HubRelay
    app.state.hub_relay = HubRelay(link)
    from homebutler.domain.device_targets import ACTarget, IRTarget
    from homebutler.integrations.ha.commands import Commands
    commands = Commands(link, lambda: getattr(app.state, "loop", None))
    app.state.ha_commands = commands
    app.state.schedule_targets = {"ac": ACTarget(store, commands), "ir": IRTarget(store, commands)}
    from homebutler import modules
    modules.install(app, jobs)          # may add schedule targets of their own
    from zoneinfo import ZoneInfo
    from homebutler.jobs.schedules import Scheduler
    app.state.scheduler = Scheduler(store, link, app.state.schedule_targets, ZoneInfo(settings.timezone))
    jobs.add("schedules", 60, app.state.scheduler.run)

    @app.get("/v2/health")
    def health():
        """For uptime checks: says nothing about the household."""
        return {"ok": True}

    @app.get("/v2/system/status")
    def system_status(_: Caller = Depends(caller("admin"))):
        return {"revision": migrate.current(store.engine, settings.database_schema), "jobs": jobs.status(),
                "modules": sorted(app.state.modules)}

    return app
