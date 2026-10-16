"""Optional SQL store for the machine-written data that used to live only in Sheets.

Phase 1 of moving off Sheets: the four history tabs (sensor, AC, dehumidifier, PC) and the
「系統狀態」key/value tab. Nothing changes unless DATABASE_URL is set:

- DATABASE_URL set: every history row and state write also goes to the database (Sheets are
  still written and still read). The first startup copies what the Sheet tabs hold, once per
  kind, so the database starts with the same 24 hours.
- DB_READS=1 as well: startup backfill and state reads come from the database instead,
  falling back to Sheets if the database cannot answer.

The SQL is plain enough for both SQLite and PostgreSQL, so where the database lives is only
a connection string. Rows keep the Sheet column names in a JSON field, so the existing
backfill code reads them unchanged. History is kept RETENTION_DAYS (the Sheets only held a
day), pruned as rows are added. Every function here swallows its own errors: a database
problem never stops a Sheet write, a backfill or a request.
"""
import os
import threading
import time

from sqlalchemy import (JSON, BigInteger, Column, Float, Index, Integer, MetaData, String, Table, Text,
                        create_engine, delete, func, insert, select)

RETENTION_DAYS = 400
PRUNE_EVERY = 500

metadata = MetaData()
history = Table(
    "history", metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("kind", String(32), nullable=False),
    Column("key", String(200), nullable=False),
    Column("t", Float(precision=53), nullable=False),
    Column("data", JSON, nullable=False),
    Index("history_kind_t", "kind", "t"),
)
system_state = Table(
    "system_state", metadata,
    Column("key", String(200), primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", Float(precision=53), nullable=False),
)

_lock = threading.Lock()
_engine = None
_engine_url = None
_appends = 0
_seeded = set()


def _flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return bool(os.environ.get("DATABASE_URL", "").strip())


def reads_enabled() -> bool:
    return enabled() and _flag("DB_READS")


def _url(raw):
    # Hosted Postgres hands out postgres:// or postgresql://; this code uses the psycopg 3 driver.
    for prefix in ("postgres://", "postgresql://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg://" + raw[len(prefix):]
    return raw


def _get_engine():
    global _engine, _engine_url
    raw = os.environ.get("DATABASE_URL", "").strip()
    with _lock:
        if _engine is not None and _engine_url == raw:
            return _engine
        engine = create_engine(_url(raw), pool_pre_ping=True, pool_size=2, max_overflow=2, pool_recycle=1800) \
            if not raw.startswith("sqlite") else create_engine(raw)
        metadata.create_all(engine)
        _lock_down(engine)
        _engine, _engine_url = engine, raw
        return engine


def _lock_down(engine):
    """On PostgreSQL, turn on row level security for every table here.

    Hosted Postgres such as Supabase also serves the public schema through a REST API keyed by a
    publishable key. With RLS on and no policies, that API sees nothing; this backend connects as
    the tables' owner, which RLS does not restrict, so it keeps full access. Idempotent.
    """
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY')


def _log(what, error):
    print(f"[history_store] {what} error: {type(error).__name__}: {error}")


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


# ── history ──────────────────────────────────────────────────────────────────────────────────

def append(kind: str, key: str, row: dict) -> None:
    """Store one history row (Sheet column name → value). Never raises."""
    global _appends
    if not enabled():
        return
    t = _number(row.get("timestamp"))
    if t is None or not key:
        return
    try:
        engine = _get_engine()
        with engine.begin() as conn:
            conn.execute(insert(history).values(kind=kind, key=str(key), t=t, data=row))
            with _lock:
                _appends += 1
                prune = _appends % PRUNE_EVERY == 0
            if prune:
                conn.execute(delete(history).where(history.c.t < time.time() - RETENTION_DAYS * 86400))
    except Exception as e:
        _log(f"append({kind})", e)


def records(kind: str, since: float):
    """Rows of one kind from `since` on, oldest first, shaped like Sheet records; None if unavailable."""
    if not reads_enabled():
        return None
    try:
        with _get_engine().connect() as conn:
            rows = conn.execute(select(history.c.data).where(history.c.kind == kind, history.c.t >= since)
                                .order_by(history.c.t)).scalars().all()
        return [dict(row) for row in rows]
    except Exception as e:
        _log(f"records({kind})", e)
        return None


def seed(kind: str, rows: list, key_field: str) -> int:
    """Copy Sheet records into an empty kind once, so the database starts with the same history."""
    if not enabled() or reads_enabled() or kind in _seeded:
        return 0
    try:
        engine = _get_engine()
        with engine.begin() as conn:
            if conn.execute(select(func.count()).select_from(history).where(history.c.kind == kind)).scalar():
                _seeded.add(kind)
                return 0
            values = [{"kind": kind, "key": str(r.get(key_field)), "t": _number(r.get("timestamp")), "data": dict(r)}
                      for r in rows if _number(r.get("timestamp")) is not None and r.get(key_field)]
            if values:
                conn.execute(insert(history), values)
        _seeded.add(kind)
        print(f"[history_store] seeded {len(values)} {kind} rows from Sheet")
        return len(values)
    except Exception as e:
        _log(f"seed({kind})", e)
        return 0


# ── system state key/value ───────────────────────────────────────────────────────────────────

def state_get(key: str, direct: bool = False):
    """The stored value, or None when reads are off, the key is absent or the database fails.

    `direct` reads the database even before DB_READS, for keys only the database holds."""
    if not (reads_enabled() or (direct and enabled())):
        return None
    try:
        with _get_engine().connect() as conn:
            return conn.execute(select(system_state.c.value).where(system_state.c.key == key)).scalar()
    except Exception as e:
        _log("state_get", e)
        return None


def state_set(key: str, value) -> None:
    if not enabled():
        return
    try:
        with _get_engine().begin() as conn:
            updated = conn.execute(system_state.update().where(system_state.c.key == key)
                                   .values(value=str(value), updated_at=time.time())).rowcount
            if not updated:
                conn.execute(insert(system_state).values(key=key, value=str(value), updated_at=time.time()))
    except Exception as e:
        _log("state_set", e)


def seed_state(pairs: dict) -> None:
    """Copy the Sheet's key/value rows into an empty state table once."""
    if not enabled() or reads_enabled() or "state" in _seeded:
        return
    try:
        with _get_engine().begin() as conn:
            if not conn.execute(select(func.count()).select_from(system_state)).scalar():
                now = time.time()
                values = [{"key": k, "value": str(v), "updated_at": now} for k, v in pairs.items() if k]
                if values:
                    conn.execute(insert(system_state), values)
        _seeded.add("state")
    except Exception as e:
        _log("seed_state", e)


def status() -> dict:
    """Counts for /api/system diagnostics: never the connection string."""
    if not enabled():
        return {"enabled": False}
    try:
        with _get_engine().connect() as conn:
            counts = dict(conn.execute(select(history.c.kind, func.count()).group_by(history.c.kind)).all())
            latest = dict(conn.execute(select(history.c.kind, func.max(history.c.t)).group_by(history.c.kind)).all())
            states = conn.execute(select(func.count()).select_from(system_state)).scalar()
        import db_backup
        return {"enabled": True, "reads": reads_enabled(), "history": counts, "latest": latest, "state_keys": states,
                "backup": {"last_success": db_backup.last_success(), "last_error": db_backup.last_error}}
    except Exception as e:
        return {"enabled": True, "reads": reads_enabled(), "error": type(e).__name__}


def _reset_for_tests():
    global _engine, _engine_url, _appends
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = _engine_url = None
        _appends = 0
    _seeded.clear()
