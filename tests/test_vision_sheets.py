import asyncio
import copy
import time
import unittest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from vision_hub import ControlError,hash_token
from vision_protocol import make_result
from vision_sheets_registry import SharedSnapshot,SnapshotRegistry,validate_snapshot
from vision_sheets_api import create_sheets_app,create_components

FAMILY='fixture-existing-household-key-'+'f'*32
DEVICE='fixture-independent-device-key-'+'d'*32

def verify(token):
    if token!=FAMILY: raise HTTPException(401)
def data(now=1000):
    return {'members':[{'Line User ID':'alice','狀態':'啟用'},{'Line User ID':'bob','狀態':'啟用'}],
            'grants':[{'user_id':'alice','status':True,'preview':False,'edit':False}],
            'devices':[{'record_id':'mini-device','digest':hash_token(DEVICE),'device_id':'mini','scopes':['status'],'expires_at':now+600,'revoked':False}]}

def headers(now=1000,**overrides):
    return {'X-API-Key':FAMILY,'X-Dashboard-User':'alice','X-Dashboard-Role':'member','X-Dashboard-Session-Expires':str(now+100),**overrides}
def command(now=1000,action='status.get'):
    return {'protocol':'vision.v1','type':'command','request_id':'r1','device_id':'mini','action':action,'deadline':now+10,'payload':{}}
def status():
    return {'adapter':'synthetic','available':True,'config_revision':0,'detector_revision':0,'detector':{'model':'yolo11n','precision':'fp16'},'zone_count':0}

class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mono=10;self.now=1000;self.value=data();self.calls=0;self.fail=False
        async def reader():
            self.calls+=1
            if self.fail: raise OSError('private-text-not-returned')
            return copy.deepcopy(self.value)
        self.snapshot=SharedSnapshot(reader,owner_user_id='alice',clock=lambda:self.now,monotonic=lambda:self.mono)
        self.registry=SnapshotRegistry(self.snapshot,verify)
    async def test_startup_deny_no_per_heartbeat_reads_and_ttl(self):
        with self.assertRaises(ControlError):self.snapshot.current()
        await self.snapshot.refresh()
        for _ in range(100):self.registry.identify(DEVICE,'device',self.now)
        self.assertEqual(self.calls,1)
        self.now+=10000
        self.snapshot.current()
        self.mono+=60
        with self.assertRaises(ControlError):self.snapshot.current()
    async def test_failure_immediately_denies_without_extending(self):
        await self.snapshot.refresh();started=self.snapshot.started
        self.fail=True;self.mono+=30
        with self.assertRaises(ControlError):await self.snapshot.refresh()
        with self.assertRaises(ControlError):self.snapshot.capabilities('alice')
        self.assertEqual(started,self.snapshot.started)
        self.fail=False;self.mono+=30
        await self.snapshot.refresh()
        self.assertTrue(self.snapshot.capabilities('alice')['status'])
    async def test_singleflight_timeout_discards_late_read(self):
        release=asyncio.Event();calls=[]
        async def reader():
            calls.append(1);await release.wait();return data()
        snapshot=SharedSnapshot(reader,monotonic=lambda:self.mono,timeout=.01)
        outcomes=await asyncio.gather(snapshot.refresh(),snapshot.refresh(),return_exceptions=True)
        self.assertTrue(all(isinstance(x,ControlError) for x in outcomes));self.assertEqual(len(calls),1)
        self.mono+=30
        with self.assertRaises(ControlError):await snapshot.refresh()
        self.assertEqual(len(calls),1)
        release.set();await asyncio.sleep(0)
        with self.assertRaises(ControlError):snapshot.current()
    async def test_age_starts_before_read_and_shutdown_no_resurrection(self):
        async def slow():self.mono+=59;return data()
        snapshot=SharedSnapshot(slow,monotonic=lambda:self.mono)
        await snapshot.refresh();self.mono+=1
        with self.assertRaises(ControlError):snapshot.current()
        snapshot.close()
        with self.assertRaises(ControlError):await snapshot.refresh(force=True)
    async def test_device_family_key_cannot_be_enrolled_to_bypass(self):
        self.value['devices'][0]['digest']=hash_token(FAMILY)
        await self.snapshot.refresh()
        with self.assertRaises(ControlError):self.registry.identify(FAMILY,'device',self.now)
    async def test_duplicate_malformed_expired_and_missing_grants_deny(self):
        bad=data();bad['grants'].append(bad['grants'][0])
        with self.assertRaises(ValueError):validate_snapshot(bad)
        bad=data();bad['devices'][0]['token']='never-allowed'
        with self.assertRaises(ValueError):validate_snapshot(bad)
        await self.snapshot.refresh()
        with self.assertRaises(ControlError):self.snapshot.capabilities('bob')
        with self.assertRaises(ControlError):self.registry.identify(DEVICE,'device',1700)
    async def test_refresh_revoke_disconnects_and_late_result_denies(self):
        snapshot,hub,_,_=create_components(self.snapshot.reader,verify,owner_user_id='alice',clock=lambda:self.now,monotonic=lambda:self.mono)
        await snapshot.refresh()
        sent=[]
        async def send(value):sent.append(value)
        hello=hub.connect(DEVICE,'mini',send)
        task=asyncio.create_task(hub.request('alice',command()))
        await asyncio.sleep(.01)
        self.value['devices'][0]['revoked']=True
        await snapshot.refresh(force=True)
        self.assertNotIn('mini',hub.sessions)
        self.assertEqual((await task)['status'],'unknown')
        with self.assertRaises(ControlError):hub.receive('mini',hello['session_nonce'],make_result(sent[0],'ok',status()))
    async def test_same_record_id_digest_rotation_disconnects_old_session(self):
        snapshot,hub,_,_=create_components(self.snapshot.reader,verify,owner_user_id='alice',clock=lambda:self.now,monotonic=lambda:self.mono)
        await snapshot.refresh()
        async def send(value):pass
        hub.connect(DEVICE,'mini',send)
        self.value['devices'][0]['digest']=hash_token('replacement-device-token-'+'r'*40)
        await snapshot.refresh(force=True)
        self.assertNotIn('mini',hub.sessions)
        with self.assertRaises(ControlError):hub.connect(DEVICE,'mini',send)

    async def test_shutdown_during_read_cannot_publish_late_success(self):
        release=asyncio.Event()
        async def reader():await release.wait();return data()
        snapshot=SharedSnapshot(reader)
        task=asyncio.create_task(snapshot.refresh());await asyncio.sleep(0);await asyncio.sleep(0)
        snapshot.close();release.set()
        await asyncio.gather(task,return_exceptions=True)
        await asyncio.sleep(0)
        with self.assertRaises(ControlError):snapshot.current()
        self.assertIsNone(snapshot.data)

    async def test_grant_revoke_denies_pending_response(self):
        snapshot,hub,_,_=create_components(self.snapshot.reader,verify,owner_user_id='alice',clock=lambda:self.now,monotonic=lambda:self.mono)
        await snapshot.refresh();sent=[]
        async def send(value):sent.append(value)
        hello=hub.connect(DEVICE,'mini',send)
        task=asyncio.create_task(hub.request('alice',command()));await asyncio.sleep(.01)
        self.value['grants'][0]['status']=False
        await snapshot.refresh(force=True)
        hub.receive('mini',hello['session_nonce'],make_result(sent[0],'ok',status()))
        self.assertEqual((await task)['status'],'unknown')

class RouteTests(unittest.TestCase):
    def setUp(self):
        async def reader():return data()
        self.app=create_sheets_app(reader,verify,owner_user_id='alice',clock=lambda:1000)
    def test_existing_key_user_role_expiry_grant_and_defaultdeny(self):
        with TestClient(self.app) as client:
            access='/api/vision/v1/access'
            self.assertEqual(client.get(access,headers=headers()).json(),{'capabilities':{'status':True,'preview':False,'edit':False}})
            for overrides,expected in [({'X-API-Key':DEVICE},401),({'X-Dashboard-User':'bob'},403),({'X-Dashboard-Role':'kid'},403),({'X-Dashboard-Role':''},403),({'X-Dashboard-Session-Expires':'999'},403),({'X-Dashboard-Session-Expires':'NaN'},403)]:
                response=client.get(access,headers=headers(**overrides));self.assertEqual(response.status_code,expected);self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertEqual(client.post('/api/vision/v1/command',headers=headers(),json=command(action='config.get')).status_code,403)
            self.assertEqual(client.get(access+'?token=x',headers=headers()).status_code,400)
            for key in headers():
                duplicated=list(headers().items())+[(key,headers()[key])]
                self.assertEqual(client.get(access,headers=duplicated).status_code,403)
    def test_device_ws_wrong_key_denied_and_wire_unchanged(self):
        from starlette.websockets import WebSocketDisconnect
        with TestClient(self.app) as client:
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect('/api/vision/v1/device',headers={'Authorization':'Bearer '+FAMILY}):pass
            from starlette.datastructures import Headers
            from vision_api import _bearer
            with self.assertRaises(ControlError):
                _bearer(Headers(raw=[(b'authorization',('Bearer '+DEVICE).encode())]*2))
            with client.websocket_connect('/api/vision/v1/device',headers={'Authorization':'Bearer '+DEVICE}) as ws:
                ws.send_json({'protocol':'vision.v1','type':'hello','device_id':'mini'})
                self.assertEqual(ws.receive_json()['type'],'welcome')
    def test_startup_failure_not_serving(self):
        async def failed():raise OSError()
        with self.assertRaises(ControlError):
            with TestClient(create_sheets_app(failed,verify)):pass
class StorageBoundaryTests(unittest.TestCase):
    def test_installer_allows_two_status_instances_without_disk_or_authority_ack(self):
        from fastapi import FastAPI
        from vision_sheets_api import install_sheets_status_pilot
        async def reader():return data()
        env={'VISION_STATUS_PILOT_ENABLED':'1','VISION_STATUS_TLS_PROXY_ACK':'1','WEB_CONCURRENCY':'1','VISION_STATUS_OWNER_USER_ID':'alice'}
        first,second=FastAPI(),FastAPI()
        one=install_sheets_status_pilot(first,environ=env,reader=reader,api_key_verifier=verify)
        two=install_sheets_status_pilot(second,environ=env,reader=reader,api_key_verifier=verify)
        with TestClient(first) as a,TestClient(second) as b:
            self.assertIsNot(one,two)
            self.assertEqual(a.get('/api/vision/v1/access',headers=headers(int(time.time()))).status_code,200)
            self.assertEqual(b.get('/api/vision/v1/access',headers=headers(int(time.time()))).status_code,200)
    def test_production_reader_only_fixed_batch_no_secret_columns(self):
        from vision_sheets_source import ProductionSheetsReader
        source=ProductionSheetsReader.__new__(ProductionSheetsReader)
        source.url='https://sheets.googleapis.com/fake-fixture-only'
        body={'valueRanges':[{'values':[['Line User ID','狀態'],['alice','啟用']]},{'values':[['user_id','status','preview','edit'],['alice','TRUE','FALSE','FALSE']]},{'values':[['record_id','digest','device_id','scopes','expires_at','revoked'],['mini-device',hash_token(DEVICE),'mini','status','1600','FALSE']]}]}
        calls=[]
        class Response:
            status_code=200;content=b'fixture'
            def json(self):return body
        class Session:
            def get(self,*args,**kwargs):calls.append(kwargs);return Response()
        source.session=Session()
        self.assertEqual(source.read(),{'members':data()['members'][:1],'grants':data()['grants'],'devices':data()['devices']})
        self.assertEqual(calls[0]['timeout'],(2,3));self.assertFalse(calls[0]['allow_redirects'])
        body['valueRanges'][2]['values'][0].append('token')
        with self.assertRaises(ValueError):source.read()

if __name__=='__main__':unittest.main()
