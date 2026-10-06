"""Two independent status-only hubs: no shared routing, locks, or synthetic fallback."""
import asyncio
import copy
import unittest
from fastapi import HTTPException
from vision_hub import ControlError,hash_token
from vision_protocol import make_result
from vision_sheets_api import create_components

KEY='fixture-household-overlap-'+'k'*32
DEVICE='fixture-device-overlap-'+'d'*32

def verifier(token):
    if token!=KEY:raise HTTPException(401)
def payload():
    return {'adapter':'local-health','available':True,'service':{'reachable':True,'app_version':'1.2.3','mode':'localhost-dev','config_schema':2},'reason':'http_service_responding'}

class OverlapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now=1000;self.mono=10;self.fail=False
        self.data={'members':[{'Line User ID':'owner','狀態':'啟用'},{'Line User ID':'other','狀態':'啟用'}],
                   'grants':[{'user_id':user,'status':True,'preview':False,'edit':False} for user in ('owner','other')],
                   'devices':[{'record_id':'device','digest':hash_token(DEVICE),'device_id':'mini','scopes':['status'],'expires_at':1600,'revoked':False}]}
        async def reader():
            if self.fail:raise OSError('fake failure')
            return copy.deepcopy(self.data)
        self.parts=[]
        for _ in range(2):
            snapshot,hub,_,_=create_components(reader,verifier,owner_user_id='owner',clock=lambda:self.now,monotonic=lambda:self.mono)
            await snapshot.refresh();self.parts.append((snapshot,hub))
        self.a,self.b=self.parts[0][1],self.parts[1][1]
    def command(self,rid='same',action='status.get'):
        return {'protocol':'vision.v1','type':'command','request_id':rid,'device_id':'mini','action':action,'deadline':self.now+20,'payload':{}}
    def connect(self,hub,auto=False):
        sent=[]
        async def send(request):
            sent.append(request)
            if auto:hub.receive('mini',request['session_nonce'],make_result(request,'ok',payload()))
        welcome=hub.connect(DEVICE,'mini',send)
        return welcome,sent
    async def test_load_balancer_miss_and_disconnected_cache_cannot_report_success(self):
        welcome,sent=self.connect(self.a,True)
        result=await self.a.request('owner',self.command())
        self.assertEqual(result['payload']['adapter'],'local-health')
        with self.assertRaises(ControlError) as missing:await self.b.request('owner',self.command())
        self.assertEqual((missing.exception.status,missing.exception.code),(503,'device_unavailable'))
        self.assertFalse(self.b._entries)
        self.a.disconnect('mini',welcome['session_nonce'])
        with self.assertRaises(ControlError) as missing:await self.a.request('owner',self.command())
        self.assertEqual(missing.exception.code,'device_unavailable')
        self.assertEqual(len(sent),1)
    async def test_rollout_old_reply_same_request_id_cannot_finish_new_instance(self):
        old,asent=self.connect(self.a)
        pending_a=asyncio.create_task(self.a.request('owner',self.command()));await asyncio.sleep(.01)
        new,bsent=self.connect(self.b)
        pending_b=asyncio.create_task(self.b.request('owner',self.command()));await asyncio.sleep(.01)
        self.assertNotEqual(old['session_nonce'],new['session_nonce'])
        self.parts[0][0].close()
        self.assertEqual((await pending_a)['status'],'unknown')
        with self.assertRaises(ControlError):self.b.receive('mini',old['session_nonce'],make_result(asent[0],'ok',payload()))
        self.assertFalse(pending_b.done())
        self.assertEqual(self.b.sessions['mini'].nonce,new['session_nonce'])
        self.b.receive('mini',new['session_nonce'],make_result(bsent[0],'ok',payload()))
        self.assertEqual((await pending_b)['status'],'ok')
        self.assertEqual((len(asent),len(bsent)),(1,1))
    async def test_revoke_is_applied_per_snapshot_and_denies_pending_results(self):
        tasks=[]
        for hub in (self.a,self.b):
            self.connect(hub);tasks.append(asyncio.create_task(hub.request('owner',self.command())))
        await asyncio.sleep(.01)
        self.data['devices'][0]['revoked']=True
        await self.parts[0][0].refresh(force=True)
        self.assertEqual((await tasks[0])['status'],'unknown')
        self.assertFalse(tasks[1].done()) # Independent bounded snapshot, not global instant revocation.
        await self.parts[1][0].refresh(force=True)
        self.assertEqual((await tasks[1])['status'],'unknown')
        for hub in (self.a,self.b):
            self.assertFalse(hub.sessions)
            with self.assertRaises(ControlError):hub.connect(DEVICE,'mini',lambda _:None)
    async def test_freshness_failure_owner_and_status_only_remain_enforced(self):
        for hub in (self.a,self.b):
            self.connect(hub)
            with self.assertRaises(ControlError) as denied:await hub.request('other',self.command())
            self.assertEqual(denied.exception.status,403)
            with self.assertRaises(ControlError) as denied:await hub.request('owner',self.command(action='config.get'))
            self.assertEqual(denied.exception.code,'status_only_pilot')
        self.fail=True
        for snapshot,hub in self.parts:
            with self.assertRaises(ControlError):await snapshot.refresh(force=True)
            with self.assertRaises(ControlError):await hub.request('owner',self.command())
            self.assertFalse(hub.sessions)
    async def test_rotation_expiry_and_old_disconnect_do_not_weaken_nonce(self):
        old,_=self.connect(self.a);new,sent=self.connect(self.a,True)
        self.assertFalse(self.a.disconnect('mini',old['session_nonce']))
        self.assertEqual(self.a.sessions['mini'].nonce,new['session_nonce'])
        await self.a.request('owner',self.command())
        self.connect(self.a,True)
        with self.assertRaises(ControlError) as stale:await self.a.request('owner',self.command())
        self.assertEqual(stale.exception.code,'session_invalid')
        self.data['devices'][0]['digest']=hash_token('replacement-device-token-'+'x'*32)
        for snapshot,hub in self.parts:
            await snapshot.refresh(force=True)
            self.assertFalse(hub.sessions)
            with self.assertRaises(ControlError):hub.connect(DEVICE,'mini',lambda _:None)
        self.now=1601
        for _,hub in self.parts:
            with self.assertRaises(ControlError):hub.connect('replacement-device-token-'+'x'*32,'mini',lambda _:None)
if __name__=='__main__':unittest.main()
