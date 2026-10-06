"""Shared, bounded authorization snapshot. No Sheets IO at import or on heartbeat."""
import asyncio
import hashlib
import math
import time
from vision_hub import CredentialRegistry, TokenRecord, ControlError
from vision_protocol import IDENTIFIER

CAPS = {'status','preview','edit'}

def validate_snapshot(value):
    if not isinstance(value,dict) or set(value) != {'members','grants','devices'}:
        raise ValueError('snapshot_schema')
    if any(not isinstance(value[key],list) or len(value[key])>256 for key in value):
        raise ValueError('snapshot_capacity')
    members, grants, records = {}, {}, []
    for row in value['members']:
        if not isinstance(row,dict) or set(row) != {'Line User ID','狀態'}:
            raise ValueError('member_schema')
        uid=row['Line User ID']
        if not isinstance(uid,str) or not IDENTIFIER.fullmatch(uid) or uid in members or row['狀態'] not in ('啟用','停用'):
            raise ValueError('member_schema')
        members[uid]=row['狀態']=='啟用'
    for row in value['grants']:
        if not isinstance(row,dict) or set(row) != CAPS|{'user_id'} or not isinstance(row['user_id'],str) or not IDENTIFIER.fullmatch(row['user_id']) or row['user_id'] in grants or any(type(row[c]) is not bool for c in CAPS):
            raise ValueError('grant_schema')
        grants[row['user_id']]={c:row[c] for c in CAPS}
    for row in value['devices']:
        if not isinstance(row,dict) or set(row) != {'record_id','digest','device_id','scopes','expires_at','revoked'} or row['scopes'] != ['status']:
            raise ValueError('device_schema')
        records.append(TokenRecord(row['record_id'],row['digest'],'device',frozenset({row['device_id']}),frozenset({'status'}),row['expires_at'],row['revoked']))
    return members,grants,CredentialRegistry(records)

class SharedSnapshot:
    def __init__(self,reader,*,clock=time.time,monotonic=time.monotonic,timeout=5,owner_user_id=None):
        self.reader,self.clock,self.monotonic=reader,clock,monotonic
        self.timeout=timeout
        self.owner_user_id=owner_user_id if isinstance(owner_user_id,str) and IDENTIFIER.fullmatch(owner_user_id) else None
        self.closed=False
        self.data=None
        self.started=None
        self.next_refresh=0
        self._refresh=None
        self._read=None
        self.on_change=lambda:None
    def current(self):
        if self.closed or self.data is None or self.started is None or not 0 <= self.monotonic()-self.started < 60:
            raise ControlError('registry_unavailable',503)
        return self.data
    def close(self):
        self.closed=True
        self.invalidate()
        if self._refresh and not self._refresh.done():
            self._refresh.cancel()
    def invalidate(self):
        self.data=None
        self.on_change()
    async def refresh(self,*,force=False):
        if self.closed:
            raise ControlError('registry_unavailable',503)
        if self._refresh and not self._refresh.done():
            return await asyncio.shield(self._refresh)
        if not force and self.monotonic()<self.next_refresh:
            return self.current()
        async def run():
            start=self.monotonic()
            self.next_refresh=start+30
            try:
                # A timed-out underlying read remains singleflight; never pile up worker calls.
                if self._read and not self._read.done():
                    raise ValueError('reader_busy')
                self._read=asyncio.create_task(self.reader())
                self._read.add_done_callback(lambda task:task.exception() if not task.cancelled() else None)
                raw=await asyncio.wait_for(asyncio.shield(self._read),self.timeout)
                data=validate_snapshot(raw)
                if self.closed or not 0 <= self.monotonic()-start < 60:
                    raise ValueError('snapshot_expired')
                self.data,self.started=data,start
                self.on_change()
                return self.current()
            except Exception:
                self.invalidate()
                raise ControlError('registry_unavailable',503) from None
        self._refresh=asyncio.create_task(run())
        return await asyncio.shield(self._refresh)
    def capabilities(self,user,role='member'):
        members,grants,_=self.current()
        if self.owner_user_id is None or user != self.owner_user_id or role != 'member' or not isinstance(user,str) or not members.get(user) or user not in grants or not grants[user]['status']:
            raise ControlError('vision_forbidden',403)
        return {'status':True,'preview':False,'edit':False}

class SnapshotRegistry:
    """Service identity here is an internal verified user, never a Bearer credential."""
    def __init__(self,snapshot,api_key_verifier):
        self.snapshot=snapshot
        self.api_key_verifier=api_key_verifier
    def _actor(self,user,now):
        caps=self.snapshot.capabilities(user)
        if not caps['status']:
            raise ControlError('vision_forbidden',403)
        return TokenRecord(user,hashlib.sha256(user.encode()).hexdigest(),'service',frozenset({'*'}),frozenset({'status'}),now+60)
    def identify(self,token,kind,now):
        if kind=='service':
            return self._actor(token,now)
        # Even an accidentally enrolled household key must never authenticate a device.
        try:
            self.api_key_verifier(token)
        except Exception as error:
            if getattr(error,'status_code',None) not in (401,403):
                raise ControlError('registry_unavailable',503) from None
        else:
            raise ControlError('forbidden',403)
        return self.snapshot.current()[2].identify(token,kind,now)
    def authorize(self,token,kind,device_id,scope,now):
        record=self.identify(token,kind,now)
        return self.check(record.record_id,kind,device_id,scope,now)
    def check(self,record_id,kind,device_id,scope,now):
        if kind=='service':
            if scope != 'status':
                raise ControlError('forbidden',403)
            return self._actor(record_id,now)
        return self.snapshot.current()[2].check(record_id,kind,device_id,scope,now)
