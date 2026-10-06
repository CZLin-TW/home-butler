"""Disabled-by-default status-only pilot; persistent digest registry, one authority.

No enrollment, secret discovery, filesystem creation, or networking at import/startup.
Deployment still requires separately approved persistent storage and TLS termination.
"""
from contextlib import contextmanager, asynccontextmanager
import fcntl
import math
import os
from pathlib import Path
import sqlite3
import stat
from urllib.parse import quote

from vision_hub import CredentialRegistry, ControlError, TokenRecord, VisionHub, hash_token
from vision_protocol import IDENTIFIER, validate_command

SCHEMA = '''
CREATE TABLE registry_meta (version INTEGER NOT NULL CHECK(version=1));
INSERT INTO registry_meta VALUES (1);
CREATE TABLE credentials (
 record_id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('service','device')),
 device_id TEXT NOT NULL, scope TEXT NOT NULL CHECK(scope='status'),
 expires_at REAL NOT NULL, revoked INTEGER NOT NULL CHECK(revoked IN (0,1))
);
'''

class PilotConfigurationError(ValueError):
    """Safe public error; never includes paths, database text, or credentials."""

def private_path(path, *, existing=True):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or path.parent.resolve() != path.parent:
        raise PilotConfigurationError('private_registry_required')
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise PilotConfigurationError('private_registry_required')
    if existing:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 1048576:
            raise PilotConfigurationError('private_registry_required')
    return path

@contextmanager
def open_database(path, *, writable=False):
    private_path(path)
    uri = 'file:' + quote(str(path), safe='/') + ('?mode=rw' if writable else '?mode=ro')
    db = sqlite3.connect(uri, uri=True, timeout=.1)
    try:
        db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA journal_mode').fetchone() != ('delete',):
            raise ValueError('unsupported_journal_mode')
        db.execute('BEGIN IMMEDIATE' if writable else 'BEGIN')
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def read_records(db):
    if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise ValueError('registry_invalid')
    tables = set(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
    if tables != {('registry_meta',), ('credentials',)} or db.execute('SELECT version FROM registry_meta').fetchall() != [(1,)]:
        raise ValueError('registry_invalid')
    columns = [row[1] for row in db.execute('PRAGMA table_info(credentials)')]
    if columns != ['record_id','digest','kind','device_id','scope','expires_at','revoked']:
        raise ValueError('registry_invalid')
    rows = db.execute('SELECT record_id,digest,kind,device_id,scope,expires_at,revoked FROM credentials LIMIT 257').fetchall()
    records = []
    for rid, digest, kind, device, scope, expires, revoked in rows:
        if scope != 'status' or type(revoked) is not int or revoked not in (0,1):
            raise ValueError('registry_invalid')
        records.append(TokenRecord(rid,digest,kind,frozenset({device}),frozenset({'status'}),expires,bool(revoked)))
    return CredentialRegistry(records)

class PersistentRegistry:
    """Reads the existing DB each authorization; storage errors always deny."""
    def __init__(self, path):
        try:
            self.available = True
            self.lock_identity = None
            self.path = private_path(path)
            info = self.path.stat()
            self.identity = (info.st_dev, info.st_ino)
            self._load()
        except Exception:
            raise PilotConfigurationError('registry_unavailable') from None
    def _load(self):
        try:
            if not self.available:
                raise ValueError('authority_closed')
            if self.lock_identity is not None:
                lock = private_path(str(self.path)+'.authority.lock').stat()
                if (lock.st_dev,lock.st_ino) != self.lock_identity:
                    raise ValueError('replaced_lock')
            info = private_path(self.path).stat()
            if (info.st_dev, info.st_ino) != self.identity:
                raise ValueError('replaced_database')
            with open_database(self.path) as db:
                registry = read_records(db)
            after = private_path(self.path).stat()
            if (after.st_dev, after.st_ino) != self.identity:
                raise ValueError('replaced_database')
            return registry
        except Exception:
            raise ControlError('registry_unavailable',503) from None
    def identify(self, token, kind, now):
        return self._load().identify(token,kind,now)
    def authorize(self, token, kind, device_id, scope, now):
        return self._load().authorize(token,kind,device_id,scope,now)
    def check(self, record_id, kind, device_id, scope, now):
        return self._load().check(record_id,kind,device_id,scope,now)

class StatusOnlyHub(VisionHub):
    async def request(self, token, raw):
        # config.get shares the original status scope, so scope checking alone is insufficient.
        command = validate_command(raw,self.clock(),allow_expired=True)
        self.registry.identify(token,'service',self.clock())
        if command['action'] != 'status.get':
            raise ControlError('status_only_pilot',403)
        return await super().request(token,command)

class PilotAuthority:
    def __init__(self, path):
        self.fd = None
        try:
            path = private_path(path)
            lock_path = private_path(str(path)+'.authority.lock')
            self.fd = os.open(lock_path,os.O_RDONLY | os.O_NOFOLLOW)
            fcntl.flock(self.fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.registry = PersistentRegistry(path)
            locked = os.fstat(self.fd)
            visible = lock_path.stat()
            if (visible.st_dev,visible.st_ino) != (locked.st_dev,locked.st_ino):
                raise ValueError('replaced_lock')
            self.registry.lock_identity = (locked.st_dev,locked.st_ino)
            self.hub = StatusOnlyHub(self.registry)
        except Exception:
            self.close()
            raise PilotConfigurationError('authority_unavailable') from None
    def close(self):
        if hasattr(self,'registry'):
            self.registry.available = False
        if hasattr(self,'hub'):
            for device, session in list(self.hub.sessions.items()):
                self.hub.disconnect(device,session.nonce)
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def install_status_pilot(app, *, environ=None):
    """Explicit injected env for fixtures; no runtime config or credential mutation."""
    env = os.environ if environ is None else environ
    if env.get('VISION_STATUS_PILOT_ENABLED') != '1':
        return None
    if (env.get('VISION_STATUS_SINGLE_AUTHORITY_ACK') != '1' or
        env.get('VISION_STATUS_TLS_PROXY_ACK') != '1' or
        env.get('WEB_CONCURRENCY') != '1' or
        env.get('UVICORN_WORKERS','1') != '1'):
        raise PilotConfigurationError('pilot_deployment_guards_required')
    if getattr(app.state,'_vision_control_installed',False):
        raise PilotConfigurationError('vision_already_installed')
    authority = PilotAuthority(env.get('VISION_STATUS_REGISTRY_DB',''))
    try:
        from vision_api import create_vision_router
        app.include_router(create_vision_router(authority.hub))
        previous_lifespan = app.router.lifespan_context
        @asynccontextmanager
        async def pilot_lifespan(application):
            try:
                async with previous_lifespan(application) as state:
                    yield state
            finally:
                authority.close()
        app.router.lifespan_context = pilot_lifespan
        app.state._vision_control_installed = True
        app.state.vision_status_authority = authority
        return authority
    except Exception:
        authority.close()
        raise


def initialize_registry(path):
    path = private_path(path,existing=False)
    fd = os.open(path,os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,0o600)
    os.close(fd)
    lock_path = Path(str(path)+'.authority.lock')
    lock_created = False
    try:
        lock_fd = os.open(lock_path,os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,0o600)
        os.close(lock_fd)
        lock_created = True
        with open_database(path,writable=True) as db:
            db.executescript(SCHEMA)
    except Exception:
        path.unlink()
        if lock_created:
            lock_path.unlink()
        raise


def enroll(path, *, record_id, token, kind, device_id, expires_at):
    record = TokenRecord(record_id,hash_token(token),kind,frozenset({device_id}),frozenset({'status'}),expires_at)
    CredentialRegistry([record])
    if not math.isfinite(expires_at):
        raise ValueError('invalid_expiry')
    with open_database(path,writable=True) as db:
        registry = read_records(db)
        if len(registry._records) >= 256:
            raise ValueError('registry_full')
        db.execute('INSERT INTO credentials VALUES (?,?,?,?,?,?,0)',(record_id,record.token_hash,kind,device_id,'status',expires_at))


def revoke(path, record_id):
    if not isinstance(record_id,str) or not IDENTIFIER.fullmatch(record_id):
        raise ValueError('invalid_record')
    with open_database(path,writable=True) as db:
        read_records(db)
        cursor = db.execute('UPDATE credentials SET revoked=1 WHERE record_id=?',(record_id,))
        if cursor.rowcount != 1:
            raise ValueError('record_not_found')
