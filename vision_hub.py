"""Opt-in vision control hub. In-memory, single-process; never registered at import.

Registry defaults empty and has no persistence/enrollment mechanism. Deployment must
provision separate high-entropy service/device tokens and an expiry/revocation store.
No existing family/HA key, environment variable, or application state is imported.
"""
import asyncio
import copy
import hashlib
import hmac
import math
import secrets
import time
from dataclasses import dataclass, field, replace

from vision_protocol import (PROTOCOL, MAX_DEADLINE, SCOPES, IDENTIFIER,
    ProtocolError, validate_command, validate_request, validate_result,
    request_fingerprint, make_result)


class ControlError(Exception):
    def __init__(self, code, status=403):
        self.code, self.status = code, status
        super().__init__(code)


def hash_token(token):
    if not isinstance(token, str) or not 32 <= len(token) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ControlError('unauthorized', 401)
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class TokenRecord:
    record_id: str
    token_hash: str = field(repr=False)
    kind: str = 'device'
    device_ids: frozenset = frozenset()
    scopes: frozenset = frozenset()
    expires_at: float = 0
    revoked: bool = False


class CredentialRegistry:
    def __init__(self, records=()):
        self._records = {}
        for record in records:
            if not isinstance(record, TokenRecord) or not isinstance(record.record_id, str) or not IDENTIFIER.fullmatch(record.record_id) or record.kind not in ('service', 'device') or not isinstance(record.token_hash, str) or len(record.token_hash) != 64 or any(c not in '0123456789abcdef' for c in record.token_hash):
                raise ValueError('invalid credential registry')
            if not isinstance(record.device_ids, frozenset) or not record.device_ids or any(not isinstance(d, str) or not IDENTIFIER.fullmatch(d) for d in record.device_ids) or not isinstance(record.scopes, frozenset) or not record.scopes or not record.scopes <= {'status', 'edit'}:
                raise ValueError('invalid credential registry')
            if type(record.expires_at) not in (float, int) or not math.isfinite(record.expires_at) or type(record.revoked) is not bool:
                raise ValueError('invalid credential registry')
            if record.record_id in self._records or any(r.token_hash == record.token_hash for r in self._records.values()) or len(self._records) >= 256:
                raise ValueError('invalid credential registry')
            self._records[record.record_id] = record

    def identify(self, token, kind, now):
        digest = hash_token(token)
        record = next((r for r in self._records.values() if hmac.compare_digest(r.token_hash, digest)), None)
        if record is None:
            raise ControlError('unauthorized', 401)
        if record.kind != kind or record.revoked or record.expires_at <= now:
            raise ControlError('forbidden', 403)
        return record

    def authorize(self, token, kind, device_id, scope, now):
        record = self.identify(token, kind, now)
        return self.check(record.record_id, kind, device_id, scope, now)

    def check(self, record_id, kind, device_id, scope, now):
        record = self._records.get(record_id)
        if record is None or record.revoked or record.expires_at <= now:
            raise ControlError('credential_revoked', 403)
        if record.kind != kind or device_id not in record.device_ids or scope is not None and scope not in record.scopes:
            raise ControlError('forbidden', 403)
        return record

    def revoke(self, record_id):
        if record_id in self._records:
            self._records[record_id] = replace(self._records[record_id], revoked=True)


@dataclass
class _Session:
    device_id: str
    nonce: str
    record_id: str
    expires_at: float
    send: object = field(repr=False)


@dataclass
class _Entry:
    fingerprint: str
    request: dict
    service_record: str
    future: object = field(repr=False)
    retain_until: float = 0


class VisionHub:
    def __init__(self, registry=None, clock=time.time, max_inflight=16, max_results=128, max_sessions=32):
        if any(type(v) is not int or not 1 <= v <= 4096 for v in (max_inflight, max_results, max_sessions)) or max_results < max_inflight:
            raise ValueError('invalid capacity')
        self.registry = registry if registry is not None else CredentialRegistry()
        self.clock = clock
        self.max_inflight, self.max_results, self.max_sessions = max_inflight, max_results, max_sessions
        self.sessions = {}
        self._entries = {}

    def _session(self, device_id, nonce=None, scope=None):
        session = self.sessions.get(device_id)
        if session is None:
            raise ControlError('device_unavailable', 503) if nonce is None else ControlError('session_invalid', 409)
        if nonce is not None and (not isinstance(nonce, str) or not hmac.compare_digest(session.nonce, nonce)):
            raise ControlError('session_invalid', 409)
        if self.clock() >= session.expires_at:
            self.disconnect(device_id, session.nonce)
            raise ControlError('session_invalid', 409)
        try:
            self.registry.check(session.record_id, 'device', device_id, scope, self.clock())
        except ControlError:
            self.disconnect(device_id, session.nonce)
            raise
        return session

    def connect(self, token, device_id, send):
        """Called only after the device initiates hello. send is an injected coroutine."""
        record = self.registry.authorize(token, 'device', device_id, None, self.clock())
        if not callable(send):
            raise ValueError('send must be callable')
        if device_id not in self.sessions and len(self.sessions) >= self.max_sessions:
            raise ControlError('capacity_exceeded', 503)
        if device_id in self.sessions:
            self.disconnect(device_id, self.sessions[device_id].nonce)
        nonce = secrets.token_urlsafe(32)
        session = _Session(device_id, nonce, record.record_id, min(record.expires_at, self.clock()+3600), send)
        self.sessions[device_id] = session
        return {'protocol': PROTOCOL, 'type': 'welcome', 'device_id': device_id, 'session_nonce': nonce, 'expires_at': session.expires_at}

    def disconnect(self, device_id, nonce):
        session = self.sessions.get(device_id)
        if session is None or session.nonce != nonce:
            return False  # An old socket cannot disconnect its replacement.
        del self.sessions[device_id]
        for entry in self._entries.values():
            if entry.request['device_id'] == device_id and entry.request['session_nonce'] == nonce:
                self._finish(entry, make_result(entry.request, 'unknown', code='execution_unknown'))
        return True

    @staticmethod
    def _finish(entry, result):
        if not entry.future.done():
            entry.future.set_result(copy.deepcopy(result))

    def _prune(self):
        now = self.clock()
        for key, entry in list(self._entries.items()):
            if not entry.future.done() and entry.request['deadline'] <= now:
                self._finish(entry, make_result(entry.request, 'unknown', code='deadline_exceeded'))
            if entry.future.done() and entry.retain_until <= now:
                del self._entries[key]

    async def request(self, token, raw):
        command = validate_command(raw, self.clock(), allow_expired=True)
        device, action = command['device_id'], command['action']
        record = self.registry.authorize(token, 'service', device, SCOPES[action], self.clock())
        self._prune()
        key = (device, command['request_id'])
        fingerprint = request_fingerprint(command)
        existing = self._entries.get(key)
        if existing:
            if existing.fingerprint != fingerprint:
                raise ControlError('duplicate_conflict', 409)
            if not existing.future.done():
                return await self._wait(existing, record.record_id)
            # Known past results are returned only after authenticating this caller again.
            return copy.deepcopy(existing.future.result())
        if command['deadline'] <= self.clock():
            raise ControlError('deadline_exceeded', 504)
        session = self._session(device, scope=SCOPES[action])
        if len(self._entries) >= self.max_results or sum(not e.future.done() for e in self._entries.values()) >= self.max_inflight:
            raise ControlError('capacity_exceeded', 503)
        request = {**command, 'type': 'request', 'session_nonce': session.nonce}
        validate_request(request, self.clock(), device, session.nonce)
        entry = _Entry(fingerprint, request, record.record_id, asyncio.get_running_loop().create_future(), command['deadline']+MAX_DEADLINE)
        self._entries[key] = entry
        try:
            # Recheck immediately before dispatch. No registry/config IO occurs in between.
            self.registry.check(record.record_id, 'service', device, SCOPES[action], self.clock())
            self._session(device, session.nonce, SCOPES[action])
            await asyncio.wait_for(session.send(copy.deepcopy(request)), timeout=max(0, command['deadline']-self.clock()))
        except asyncio.CancelledError:
            self._finish(entry, make_result(request, 'unknown', code='execution_unknown'))
            raise
        except Exception:
            self._finish(entry, make_result(request, 'unknown', code='execution_unknown'))
        return await self._wait(entry, record.record_id)

    async def _wait(self, entry, caller_record):
        try:
            result = await asyncio.wait_for(asyncio.shield(entry.future), max(0, entry.request['deadline']-self.clock())) if not entry.future.done() else entry.future.result()
        except asyncio.TimeoutError:
            self._finish(entry, make_result(entry.request, 'unknown', code='deadline_exceeded'))
            result = entry.future.result()
        except asyncio.CancelledError:
            # Cancelled HTTP callers must not cause automatic re-dispatch on reconnect.
            self._finish(entry, make_result(entry.request, 'unknown', code='execution_unknown'))
            raise
        try:
            self.registry.check(caller_record, 'service', entry.request['device_id'], SCOPES[entry.request['action']], self.clock())
        except ControlError:
            return make_result(entry.request, 'unknown', code='credential_revoked')
        return copy.deepcopy(result)

    def receive(self, device_id, nonce, raw):
        session = self._session(device_id, nonce)
        try:
            result = validate_result(raw)
            if result['device_id'] != device_id or result['session_nonce'] != nonce:
                raise ControlError('session_invalid', 409)
            entry = self._entries.get((device_id, result['request_id']))
            if entry is None or entry.request['session_nonce'] != nonce:
                raise ControlError('session_invalid', 409)
            if entry.future.done():
                return False  # Late/duplicate results never overwrite a terminal outcome.
            action = entry.request['action']
            validate_result(result, expected_action=action)
            try:
                self.registry.check(session.record_id, 'device', device_id, SCOPES[action], self.clock())
                self.registry.check(entry.service_record, 'service', device_id, SCOPES[action], self.clock())
            except ControlError:
                self._finish(entry, make_result(entry.request, 'unknown', code='credential_revoked'))
                return False
            if self.clock() >= entry.request['deadline']:
                self._finish(entry, make_result(entry.request, 'unknown', code='deadline_exceeded'))
                return False
            self._finish(entry, result)
            return True
        except (ProtocolError, ControlError):
            self.disconnect(device_id, nonce)
            raise
