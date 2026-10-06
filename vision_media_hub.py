"""Fixture-only, single-process media authority. No control/HA imports or networking."""
import asyncio
import hashlib
import hmac
import json
import math
import re
import secrets
import time

PROTOCOL = 'vision-media.v1'
DEVICE_ID = 'synthetic-mini'
SERVICE_TOKEN = 'fixture-media-hub-service-public-only'
DEVICE_TOKEN = 'fixture-media-hub-device-public-only'
MAX_BYTES = 40960
QUARANTINE = 67

class MediaError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)

def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise MediaError('invalid_payload')

def number(value):
    return type(value) in (int, float) and math.isfinite(value)

def identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) is not None

def sdp(value):
    if not isinstance(value, str) or not value.startswith('v=0') or len(value.encode()) > 30720:
        raise MediaError('invalid_payload')
    return value

def decode(raw):
    if len(raw.encode() if isinstance(raw, str) else raw) > MAX_BYTES:
        raise MediaError('payload_too_large', 413)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise MediaError('invalid_payload')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, UnicodeError):
        raise MediaError('invalid_payload') from None

class MockProvider:
    """Only literal synthetic credentials; empty by default. Never accepts deployment secrets."""
    def __init__(self, *, enabled=False, clock=time.time):
        self.enabled, self.clock = enabled, clock
        self.expires_at = clock() + 600
        self.revoked = set()
        self.actors = {'synthetic-alice': True, 'synthetic-bob': True, 'synthetic-denied': False} if enabled else {}
    def credential(self, token, kind):
        expected = SERVICE_TOKEN if kind == 'service' else DEVICE_TOKEN
        if not self.enabled or kind in self.revoked or self.clock() >= self.expires_at or not isinstance(token, str) or not hmac.compare_digest(hashlib.sha256(token.encode()).digest(), hashlib.sha256(expected.encode()).digest()):
            raise MediaError('unauthorized', 401)
    def actor(self, actor):
        exact(actor, {'id', 'expires_at'})
        if not identifier(actor['id']) or not number(actor['expires_at']):
            raise MediaError('invalid_payload')
        if not self.enabled or not self.actors.get(actor['id']) or actor['id'] in self.revoked or self.clock() >= min(actor['expires_at'], self.expires_at):
            raise MediaError('preview_forbidden', 403)

class MediaHub:
    def __init__(self, provider=None, *, clock=time.time, timeout=4, startup_quarantine=QUARANTINE):
        self.clock = clock
        self.provider = provider or MockProvider(clock=clock)
        self.timeout = min(4, max(.01, timeout))
        self.device = None
        self.pending = {}
        self.lease = None
        self.blocked_until = clock() + startup_quarantine
        self.terminal = {}
    def connect(self, token, hello, send):
        self.provider.credential(token, 'device')
        exact(hello, {'protocol', 'type', 'device_id'})
        if hello != {'protocol': PROTOCOL, 'type': 'hello', 'device_id': DEVICE_ID}:
            raise MediaError('invalid_payload')
        if self.device:
            raise MediaError('device_busy', 409)
        epoch = secrets.token_urlsafe(32)
        self.device = {'epoch': epoch, 'send': send}
        return {'protocol': PROTOCOL, 'type': 'welcome', 'epoch': epoch, 'expires_at': self.provider.expires_at}
    def disconnect(self, epoch):
        if not self.device or self.device['epoch'] != epoch:
            return
        self.device = None
        if self.lease or self.pending:
            self.blocked_until = max(self.blocked_until, self.clock() + QUARANTINE)
        self.lease = None
        for _, future in self.pending.values():
            if not future.done():
                future.set_exception(MediaError('execution_unknown', 503))
    def receive(self, epoch, value):
        if not self.device or epoch != self.device['epoch']:
            raise MediaError('session_invalid', 409)
        self.provider.credential(DEVICE_TOKEN, 'device')
        exact(value, {'protocol', 'type', 'id', 'epoch', 'status', 'body'})
        if value['protocol'] != PROTOCOL or value['type'] != 'result' or value['epoch'] != epoch or not identifier(value['id']) or type(value['status']) is not int or not 200 <= value['status'] <= 599 or len(json.dumps(value).encode()) > MAX_BYTES:
            raise MediaError('invalid_payload')
        pending = self.pending.get(value['id'])
        if not pending:
            raise MediaError('session_invalid', 409)
        action, future = pending
        body = value['body']
        if value['status'] != 200:
            exact(body, {'code'})
            if not identifier(body['code']):
                raise MediaError('invalid_payload')
        elif action == 'offer':
            exact(body, {'session_id', 'type', 'sdp', 'expires_at'})
            if not identifier(body['session_id']) or body['type'] != 'answer' or not number(body['expires_at']) or not self.clock() < body['expires_at'] <= self.clock()+61:
                raise MediaError('invalid_payload')
            sdp(body['sdp'])
        elif action == 'heartbeat':
            exact(body, {'active', 'expires_at'})
            if body['active'] is not True or not number(body['expires_at']):
                raise MediaError('invalid_payload')
        else:
            exact(body, {'active', 'reason'})
            if body['active'] is not False or not identifier(body['reason']):
                raise MediaError('invalid_payload')
        if not future.done():
            future.set_result((value['status'], body))
    async def dispatch(self, action, payload):
        self.provider.credential(DEVICE_TOKEN, 'device')
        if not self.device or len(self.pending) >= 4:
            raise MediaError('media_unavailable', 503)
        device = self.device
        request_id = secrets.token_urlsafe(24)
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = (action, future)
        try:
            async with asyncio.timeout(self.timeout):
                await device['send']({'protocol': PROTOCOL, 'type': 'request', 'id': request_id, 'epoch': device['epoch'], 'action': action, 'deadline': self.clock()+self.timeout, 'payload': payload})
                return await future
        except (Exception, asyncio.CancelledError):
            self.blocked_until = max(self.blocked_until, self.clock()+QUARANTINE)
            raise MediaError('execution_unknown', 503) from None
        finally:
            self.pending.pop(request_id, None)
    async def cleanup(self, lease):
        if self.lease is not lease:
            return
        lease['phase'] = 'closing'
        try:
            status, _ = await self.dispatch('stop', {'session_id': lease['native']}) if lease.get('native') else (503, {})
            if status not in (200, 404):
                raise MediaError('execution_unknown', 503)
            self.terminal[lease['id']] = (lease['actor']['id'], self.clock()+67)
        except MediaError:
            self.blocked_until = max(self.blocked_until, self.clock()+QUARANTINE)
        finally:
            if self.lease is lease:
                self.lease = None
    async def sweep(self):
        self.terminal = {k:v for k,v in self.terminal.items() if v[1] > self.clock()}
        lease = self.lease
        if not lease or lease['phase'] != 'active':
            return
        try:
            self.provider.credential(SERVICE_TOKEN, 'service')
            self.provider.actor(lease['actor'])
            if self.clock() >= lease['expires_at']:
                raise MediaError('expired')
        except MediaError:
            await self.cleanup(lease)
    async def operation(self, token, action, body):
        self.provider.credential(token, 'service')
        fields = {'actor'} | ({'type','sdp'} if action == 'offer' else {'session_id','visible'} if action == 'heartbeat' else {'session_id'} if action == 'stop' else set())
        if action not in {'offer','heartbeat','stop','state'}:
            raise MediaError('invalid_payload')
        exact(body, fields)
        actor = body['actor']
        self.provider.actor(actor)
        if action == 'offer':
            if body['type'] != 'offer':
                raise MediaError('invalid_payload')
            sdp(body['sdp'])
        elif action != 'state':
            if not identifier(body['session_id']) or (action == 'heartbeat' and type(body['visible']) is not bool):
                raise MediaError('invalid_payload')
        await self.sweep()
        if action == 'state':
            own = self.lease and self.lease['actor']['id'] == actor['id']
            return {'active': bool(own and self.lease['phase'] == 'active'), 'reason': 'lease_active' if own and self.lease['phase'] == 'active' else 'unknown' if own else 'not_started', 'media': None}
        if action == 'offer':
            if self.clock() < self.blocked_until:
                raise MediaError('media_result_unknown', 503)
            if self.lease:
                raise MediaError('media_viewer_busy', 409)
            if len(self.terminal) >= 32:
                raise MediaError('capacity_exceeded', 503)
            lease = {'id': secrets.token_urlsafe(24), 'actor': dict(actor), 'phase': 'pending'}
            self.lease = lease
            try:
                status, answer = await self.dispatch('offer', {'device_id': DEVICE_ID, 'type':'offer', 'sdp':body['sdp']})
                if status != 200:
                    if status >= 500:
                        self.blocked_until = max(self.blocked_until, self.clock()+QUARANTINE)
                    self.lease = None
                    raise MediaError('media_offer_failed', status)
                lease['native'] = answer['session_id']
                lease['expires_at'] = min(actor['expires_at'], answer['expires_at'])
                self.provider.credential(token, 'service')
                self.provider.actor(actor)
                if self.lease is not lease:
                    raise MediaError('execution_unknown', 503)
                lease['phase'] = 'active'
                return {**answer, 'session_id': lease['id'], 'expires_at': lease['expires_at']}
            except MediaError:
                await self.cleanup(lease)
                raise
        lease = self.lease
        if action == 'stop' and self.terminal.get(body['session_id'], (None,))[0] == actor['id']:
            return {'active':False, 'reason':'user_stopped'}
        if not lease or lease['id'] != body['session_id'] or lease['actor']['id'] != actor['id']:
            raise MediaError('lease_not_found', 404)
        if action == 'stop' or not body['visible']:
            await self.cleanup(lease)
            if self.clock() < self.blocked_until:
                raise MediaError('execution_unknown', 503)
            return {'active':False, 'reason':'user_stopped'}
        try:
            status, answer = await self.dispatch('heartbeat', {'session_id':lease['native'], 'visible':True})
            self.provider.actor(actor)
            self.provider.credential(token, 'service')
        except MediaError:
            await self.cleanup(lease)
            raise
        if status != 200 or self.lease is not lease or lease['phase'] != 'active' or self.clock() >= lease['expires_at']:
            await self.cleanup(lease)
            raise MediaError('execution_unknown', 503)
        return {'active':True, 'expires_at':lease['expires_at']}
