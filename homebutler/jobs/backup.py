"""The daily backup: every table except the long histories, as gzip'd JSON, into one Drive file the
family owns (BACKUP_FOLDER/homebutler-backup.json.gz), after 03:00 local time, once a day.

A service account has no Drive storage and cannot create files in a personal Drive, but it can replace
the content of a file the owner shared with it; Drive keeps each file's versions for 30 days, so the
one file holds a month of daily backups. The time of the last success is kept in system_state; the
health check warns when it is more than 30 hours old.

Restore (by hand): python -m homebutler.jobs.backup restore <downloaded file>   (DATABASE_URL set;
it empties those tables first).
"""
from __future__ import annotations

import gzip
import json
import logging
from datetime import date, datetime, time as time_of_day

from sqlalchemy import Date, DateTime, Integer, Time, select

from homebutler import modules
from homebutler.domain import system_state
from homebutler.store.models import Base

log = logging.getLogger("homebutler.jobs.backup")
FILE_NAME = "homebutler-backup.json.gz"
HOUR = 3
STALE_HOURS = 30
SKIP = {"sensor_readings", "ac_history", "computer_metrics", "dehumidifier_history"}
MARKER = "backup_last_success"
DRIVE = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


def tables():
    modules.load()
    return [t for t in Base.metadata.sorted_tables if t.name not in SKIP]


def payload(engine, exported_at: float) -> bytes:
    found = {}
    with engine.connect() as connection:
        for table in tables():
            found[table.name] = [dict(row._mapping) for row in connection.execute(select(table))]
    document = {"format": "homebutler-backup", "version": 2, "exported_at": exported_at,
                "counts": {name: len(rows) for name, rows in found.items()}, "tables": found}
    return gzip.compress(json.dumps(document, ensure_ascii=False, default=lambda v: v.isoformat()).encode())


def restore(engine, data: bytes) -> dict:
    """Load a backup, replacing what those tables hold. Children are emptied first, parents loaded first."""
    document = json.loads(gzip.decompress(data))
    if document.get("format") != "homebutler-backup":
        raise ValueError("not a HomeButler backup")
    known = tables()
    loaded = {}
    with engine.begin() as connection:
        for table in reversed(known):
            if table.name in document["tables"]:
                connection.execute(table.delete())
        for table in known:
            rows = document["tables"].get(table.name)
            if not rows:
                continue
            for row in rows:
                for column in table.columns:
                    value = row.get(column.name)
                    if isinstance(value, str):
                        kind = column.type.impl if hasattr(column.type, "impl") else column.type
                        if isinstance(kind, DateTime):
                            row[column.name] = datetime.fromisoformat(value)
                        elif isinstance(kind, Date):
                            row[column.name] = date.fromisoformat(value)
                        elif isinstance(kind, Time):
                            row[column.name] = time_of_day.fromisoformat(value)
            connection.execute(table.insert(), rows)
            loaded[table.name] = len(rows)
        if engine.dialect.name == "postgresql":
            from sqlalchemy import text
            for table in known:      # ids came from the backup: move each counter past them
                if "id" in table.columns and isinstance(table.columns["id"].type, Integer):
                    connection.execute(text(f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                                            f"COALESCE((SELECT MAX(id) FROM \"{table.name}\"), 0) + 1, false)"))
    return loaded


class DriveFile:
    """Replaces the content of one file in one folder of the owner's Drive."""

    def __init__(self, credentials_json: str, folder: str, http):
        from google.oauth2.service_account import Credentials
        self.folder, self.http = folder, http
        self.credentials = Credentials.from_service_account_info(json.loads(credentials_json),
                                                                 scopes=["https://www.googleapis.com/auth/drive"])

    def _headers(self) -> dict:
        from homebutler.integrations.sheets_config.source import _HttpxRequest
        if not self.credentials.valid:
            self.credentials.refresh(_HttpxRequest(self.http))
        return {"Authorization": "Bearer " + self.credentials.token}

    def _one(self, query: str, what: str) -> str:
        reply = self.http.get(DRIVE, params={"q": query, "fields": "files(id)", "pageSize": 10, "supportsAllDrives": "true",
                                             "includeItemsFromAllDrives": "true"}, headers=self._headers(), timeout=30)
        reply.raise_for_status()
        files = reply.json().get("files", [])
        if len(files) != 1:
            raise RuntimeError(f"expected one {what}, found {len(files)}")
        return files[0]["id"]

    def write(self, data: bytes) -> None:
        name = self.folder.replace("\\", "\\\\").replace("'", "\\'")
        folder = self._one(f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false", "backup folder")
        file_id = self._one(f"name = '{FILE_NAME}' and '{folder}' in parents and trashed = false", "backup file")
        reply = self.http.patch(f"{UPLOAD}/{file_id}", params={"uploadType": "media", "supportsAllDrives": "true"}, content=data,
                                headers={**self._headers(), "Content-Type": "application/gzip"}, timeout=60)
        reply.raise_for_status()


class Backup:
    def __init__(self, store, target, zone, clock):
        self.store, self.target, self.zone, self.clock = store, target, zone, clock
        self.started = clock()
        self.last_error: str | None = None

    def last_success(self) -> datetime | None:
        with self.store.transaction() as session:
            value = system_state.get(session, MARKER)
        return datetime.fromisoformat(value) if value else None

    def run(self) -> str:
        now = self.clock()
        local = now.astimezone(self.zone)
        last = self.last_success()
        if local.hour < HOUR or (last and last.astimezone(self.zone).date() == local.date()):
            return "not due"
        try:
            data = payload(self.store.engine, now.timestamp())
            self.target.write(data)
        except Exception as error:
            self.last_error = type(error).__name__
            log.warning("backup failed (%s)", self.last_error)
            return "failed"
        with self.store.transaction() as session:
            system_state.set_(session, MARKER, now.isoformat())
        self.last_error = None
        return f"wrote {len(data)} bytes"

    def stale(self) -> bool:
        last = self.last_success() or self.started
        return (self.clock() - last).total_seconds() > STALE_HOURS * 3600


if __name__ == "__main__":
    import sys
    from homebutler.settings import Settings
    from homebutler.store.db import make_engine
    if len(sys.argv) != 3 or sys.argv[1] != "restore":
        raise SystemExit("usage: python -m homebutler.jobs.backup restore <file>")
    settings = Settings.from_env()
    with open(sys.argv[2], "rb") as handle:
        print(restore(make_engine(settings.database_url, settings.database_schema), handle.read()))
