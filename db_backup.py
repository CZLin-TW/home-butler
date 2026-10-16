"""Daily backup of the database's own tables into one Drive file the user owns.

With the data off Sheets, Google's version history no longer protects it, and Supabase's free
plan has no downloadable backups. Once a day after BACKUP_HOUR (local time) every table except
`history` (the charts' samples, kept 400 days in the database and not worth backing up) is
written as gzip'd JSON into `homebutler-backup.json.gz` inside the Drive folder BACKUP_FOLDER.

A service account cannot create files in a personal Drive (it has no storage quota), but it
can replace the content of a file the user owns in a folder shared with it. Drive keeps each
file's earlier versions for 30 days, so the one file holds a month of daily backups: restore by
downloading a version ("Manage versions") and loading its tables. The file's owner pays the
quota; each version is well under a megabyte.

Does nothing unless DATABASE_URL is set. The time of the last success is kept in the database
(`system_state`); health_alert warns once when it is more than STALE_HOURS old.
"""
import gzip
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import history_store

FILE_NAME = "homebutler-backup.json.gz"
BACKUP_HOUR = 3
STALE_HOURS = 30
SKIP_TABLES = {"history"}
LAST_OK_KEY = "最後資料庫備份時間"
DRIVE = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
# Same zone as config.TZ; read here so this module does not pull in the LINE SDK through config.
TZ = ZoneInfo("Asia/Taipei")

_first_seen = time.time()
last_error = None


def folder_name() -> str:
    return os.environ.get("BACKUP_FOLDER", "").strip() or "HomeButler備份"


def payload(engine, now=None) -> bytes:
    """Every table except the skipped ones, as gzip'd JSON with row counts."""
    from sqlalchemy import select
    tables = {}
    with engine.connect() as conn:
        for table in history_store.metadata.sorted_tables:
            if table.name in SKIP_TABLES:
                continue
            tables[table.name] = [dict(row._mapping) for row in conn.execute(select(table))]
    doc = {"format": "homebutler-db-backup", "version": 1, "exported_at": now or time.time(),
           "counts": {name: len(rows) for name, rows in tables.items()}, "tables": tables}
    return gzip.compress(json.dumps(doc, ensure_ascii=False, default=str).encode())


def _session():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(json.loads(os.environ.get("GOOGLE_CREDENTIALS", "")),
                                                  scopes=["https://www.googleapis.com/auth/drive"])
    return AuthorizedSession(creds)


def _quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")


def _find_one(session, query: str, what: str) -> str:
    response = session.get(DRIVE, params={"q": query, "fields": "files(id,name)", "pageSize": 10,
                                          "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"},
                           timeout=30)
    response.raise_for_status()
    files = response.json().get("files", [])
    if len(files) != 1:
        raise RuntimeError(f"expected one {what}, found {len(files)}")
    return files[0]["id"]


def upload(data: bytes, session=None) -> None:
    """Replace the content of the backup file; raises if the folder or file is missing or ambiguous."""
    session = session or _session()
    folder = _find_one(session, f"name = '{_quote(folder_name())}' and mimeType = "
                                "'application/vnd.google-apps.folder' and trashed = false", "backup folder")
    file_id = _find_one(session, f"name = '{FILE_NAME}' and '{folder}' in parents and trashed = false", "backup file")
    response = session.patch(f"{UPLOAD}/{file_id}", params={"uploadType": "media", "supportsAllDrives": "true"},
                             data=data, headers={"Content-Type": "application/gzip"}, timeout=60)
    response.raise_for_status()


def last_success():
    value = history_store.state_get(LAST_OK_KEY, direct=True)
    try:
        return float(value) if value else None
    except ValueError:
        return None


def run_if_due(now=None, session=None) -> bool:
    """Back up once per local day after BACKUP_HOUR. Returns True when a backup was written."""
    global last_error
    if not history_store.enabled():
        return False
    now = now or datetime.now(TZ)
    if now.hour < BACKUP_HOUR:
        return False
    last = last_success()
    if last and datetime.fromtimestamp(last, TZ).date() == now.date():
        return False
    try:
        data = payload(history_store._get_engine(), now.timestamp())
        upload(data, session)
        history_store.state_set(LAST_OK_KEY, now.timestamp())
        last_error = None
        print(f"[db_backup] wrote {len(data)} bytes to {folder_name()}/{FILE_NAME}")
        return True
    except Exception as e:
        last_error = f"{type(e).__name__}: {e}"
        print(f"[db_backup] backup failed: {last_error}")
        return False


def stale(now=None) -> bool:
    """True when backups are on and none has succeeded for STALE_HOURS (counting from startup if never)."""
    if not history_store.enabled():
        return False
    now = now or time.time()
    return now - (last_success() or _first_seen) > STALE_HOURS * 3600


def restore(data: bytes, engine=None) -> dict:
    """Load a backup's tables, replacing what those tables hold now. Returns rows loaded per table.

    Run by hand when needed:  DATABASE_URL=... python db_backup.py restore <downloaded file>
    """
    doc = json.loads(gzip.decompress(data))
    if doc.get("format") != "homebutler-db-backup":
        raise ValueError("not a HomeButler database backup")
    engine = engine or history_store._get_engine()
    known = {t.name: t for t in history_store.metadata.sorted_tables}
    loaded = {}
    with engine.begin() as conn:
        for name, rows in doc["tables"].items():
            table = known.get(name)
            if table is None or name in SKIP_TABLES:
                continue
            conn.execute(table.delete())
            if rows:
                conn.execute(table.insert(), rows)
            loaded[name] = len(rows)
    return loaded


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3 or sys.argv[1] != "restore":
        raise SystemExit("usage: DATABASE_URL=... python db_backup.py restore <backup file>")
    with open(sys.argv[2], "rb") as handle:
        print(restore(handle.read()))
