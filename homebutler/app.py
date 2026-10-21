"""The service. Run with: uvicorn homebutler.app:create_app --factory

Startup brings the database to the newest structure, makes sure the default household exists and
starts the background jobs; a request never waits for any of the slow outside services.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from homebutler.api import (devices_page, devices_relay, home, home_assistant, household, lighting, line, pairing, schedules,
                            sign_in, telemetry)
from homebutler.api.auth import Caller, caller
from homebutler.domain import households
from homebutler.integrations.ha.link import Link
from homebutler.jobs.runner import Jobs
from homebutler.settings import Settings
from homebutler.store import migrate
from homebutler.store.db import Store, make_engine

log = logging.getLogger("homebutler")


def _background_jobs(app, jobs, settings, store, link, commands, zone) -> None:
    """The household's routine work: to-dos and their reminders, Notion, health alerts, backups."""
    from homebutler.domain import conversation
    from homebutler.jobs import todos as todo_jobs
    from homebutler.store.models import utcnow
    line = getattr(app.state, "line", None)
    if line is not None:
        jobs.add("todo_reminders", 300, lambda: todo_jobs.recurring_and_reminders(store, line, zone))
    else:
        from datetime import datetime
        from homebutler.domain.todos import templates
        def recurring_only():
            with store.transaction() as session:
                return templates.materialize(session, datetime.now(zone).date())
        jobs.add("todo_recurring", 300, recurring_only)
    jobs.add("light_reminders", 60, todo_jobs.LightReminders(store, commands, zone).run)
    if settings.notion_token:
        from homebutler.integrations.notion import Notion
        notion = Notion(settings.notion_token)
        jobs.add("notion", 300, lambda: todo_jobs.notion_sync(store, notion, zone))
    backup = None
    if settings.backup_folder:
        import httpx
        from homebutler.jobs.backup import Backup, DriveFile
        backup = Backup(store, DriveFile(settings.google_credentials, settings.backup_folder, httpx.Client()), zone, utcnow)
        jobs.add("backup", 300, backup.run)
    if line is not None:
        from homebutler.jobs.health import Health
        def theater():
            result = commands.theater("summary")
            if result.get("status") != "success":
                raise RuntimeError(result.get("message") or "theater summary failed")
        jobs.add("health", 300, Health(store, link, line.push, zone, utcnow, settings.offline_alert_seconds, backup, theater).run)

    def prune():
        with store.transaction() as session:
            return conversation.prune(session, utcnow())
    jobs.add("prune", 3600, prune)


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
            if settings.keys():
                from homebutler.domain import tokens
                log.info("keys from the environment: %s", tokens.from_environment(session, settings.keys()))
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
        if settings.migrate_from_old:
            def bring_old_data():
                from homebutler import migration
                from homebutler.domain import system_state
                with store.transaction() as session:
                    if system_state.get(session, "migrated_from_old_at"):
                        return                         # once only; a trial copy re-runs it by hand with --replace
                try:
                    report = migration.migrate_from(settings, store)
                    log.info("migration: read %s, written %s, skipped %d", report.read, report.written, len(report.skipped))
                except Exception:
                    log.exception("migration failed; nothing was written")
            # Before the jobs: schedules must not run against a half-filled database.
            bring_old_data()
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
    app.include_router(line.router)
    app.include_router(pairing.router)
    for module in (sign_in, household, home, devices_page, lighting):
        app.include_router(module.router)
    from homebutler.domain.remotes import Guard
    app.state.remote_guard = Guard()
    from homebutler.integrations.switchbot_push import HubRelay
    app.state.hub_relay = HubRelay(link)
    from homebutler.domain.device_targets import ACTarget, IRTarget
    from homebutler.integrations.ha.commands import Commands
    commands = Commands(link, lambda: getattr(app.state, "loop", None))
    app.state.ha_commands = commands
    app.state.schedule_targets = {"ac": ACTarget(store, commands), "ir": IRTarget(store, commands)}
    from zoneinfo import ZoneInfo
    from homebutler.assistant.actions import Services
    zone = ZoneInfo(settings.timezone)
    app.state.butler_services = Services(store, link, app.state.schedule_targets, None, zone)
    if settings.weather_api_key:
        import httpx
        from homebutler.integrations.weather import Weather, tls_context
        app.state.butler_services.weather = Weather(settings.weather_api_key, httpx.Client(verify=tls_context()), zone)
    from homebutler import modules
    modules.install(app, jobs)          # may add schedule targets and butler actions of their own
    from homebutler.jobs.schedules import Scheduler
    app.state.scheduler = app.state.butler_services.scheduler = Scheduler(store, link, app.state.schedule_targets, zone)
    if settings.schedules_enabled:
        jobs.add("schedules", 60, app.state.scheduler.run)
    import asyncio
    app.state.line_lock, app.state.background = asyncio.Lock(), set()
    if settings.line_channel_secret:
        import httpx
        from homebutler.assistant.claude import Claude
        from homebutler.assistant.line import LineBot
        from homebutler.assistant.pipeline import Butler
        from homebutler.integrations.line import Line
        from homebutler.integrations.version import DashboardVersion
        app.state.line = Line(settings.line_channel_token, httpx.Client())
        butler = Butler(app.state.butler_services, Claude(settings.assistant_model, api_key=settings.anthropic_api_key),
                        DashboardVersion(settings.dashboard_url, httpx.Client()))
        app.state.line_bot = LineBot(butler, app.state.line)
        from homebutler.jobs.daily import DailySummary
        jobs.add("daily_summary", 300, DailySummary(app.state.butler_services, butler.claude, app.state.line,
                                                    settings.daily_push_hour).run)
    _background_jobs(app, jobs, settings, store, link, commands, zone)

    @app.api_route("/", methods=["GET", "HEAD"])
    @app.get("/v2/health")
    def health():
        """For uptime checks: says nothing about the household."""
        return {"ok": True}

    @app.get("/v2/system/status")
    def system_status(_: Caller = Depends(caller("admin"))):
        return {"revision": migrate.current(store.engine, settings.database_schema), "jobs": jobs.status(),
                "modules": sorted(app.state.modules)}

    return app
