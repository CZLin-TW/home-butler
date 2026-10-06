import asyncio
import contextlib
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from vision_pilot import (initialize_registry,enroll,revoke,PersistentRegistry,PilotAuthority,
                         PilotConfigurationError,install_status_pilot,StatusOnlyHub)
from vision_hub import ControlError
from vision_protocol import make_result

SERVICE='fixture-persistent-service-'+'s'*32
DEVICE='fixture-persistent-device-'+'d'*32

class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name).resolve()/'registry.sqlite'
        initialize_registry(self.path)
        for rid,token,kind in [('service',SERVICE,'service'),('device',DEVICE,'device')]:
            enroll(self.path,record_id=rid,token=token,kind=kind,device_id='mini',expires_at=time.time()+600)
    def tearDown(self):
        self.temp.cleanup()
    def env(self):
        return {'VISION_STATUS_PILOT_ENABLED':'1','VISION_STATUS_REGISTRY_DB':str(self.path),
                'VISION_STATUS_SINGLE_AUTHORITY_ACK':'1','VISION_STATUS_TLS_PROXY_ACK':'1','WEB_CONCURRENCY':'1'}
    def command(self,action='status.get',payload=None):
        return {'protocol':'vision.v1','type':'command','request_id':'r1','device_id':'mini',
                'action':action,'deadline':time.time()+10,'payload':{} if payload is None else payload}
    def test_digest_only_and_persistent_revoke_across_restart(self):
        raw=self.path.read_bytes()
        self.assertNotIn(SERVICE.encode(),raw)
        self.assertNotIn(DEVICE.encode(),raw)
        authority=PilotAuthority(self.path)
        self.assertEqual(authority.registry.identify(SERVICE,'service',time.time()).scopes,frozenset({'status'}))
        revoke(self.path,'service')
        with self.assertRaises(ControlError):
            authority.registry.identify(SERVICE,'service',time.time())
        authority.close()
        reopened=PilotAuthority(self.path)
        try:
            with self.assertRaises(ControlError):
                reopened.registry.identify(SERVICE,'service',time.time())
        finally:
            reopened.close()
    def test_second_authority_denied_and_shutdown_releases(self):
        one=PilotAuthority(self.path)
        try:
            with self.assertRaises(PilotConfigurationError):
                PilotAuthority(self.path)
        finally:
            one.close()
        two=PilotAuthority(self.path)
        two.close()
    def test_missing_corrupt_and_replaced_database_deny(self):
        with self.assertRaises(PilotConfigurationError):
            PilotAuthority(self.path.parent/'missing')
        registry=PersistentRegistry(self.path)
        self.path.write_bytes(b'corrupt')
        with self.assertRaises(ControlError):
            registry.identify(SERVICE,'service',time.time())
        with self.assertRaises(PilotConfigurationError):
            PilotAuthority(self.path)
        self.path.unlink()
        Path(str(self.path)+'.authority.lock').unlink()
        initialize_registry(self.path)
        with self.assertRaises(ControlError):
            registry.identify(SERVICE,'service',time.time())
    def test_permission_symlink_and_wal_deny(self):
        self.path.chmod(0o644)
        with self.assertRaises(PilotConfigurationError):
            PilotAuthority(self.path)
        self.path.chmod(0o600)
        linked=self.path.parent/'linked'
        linked.symlink_to(self.path)
        with self.assertRaises(PilotConfigurationError):
            PilotAuthority(linked)
        with sqlite3.connect(self.path) as db:
            db.execute('PRAGMA journal_mode=WAL')
        with self.assertRaises(PilotConfigurationError):
            PilotAuthority(self.path)
    def test_deployment_guards_and_disabled_no_routes(self):
        app=FastAPI()
        self.assertIsNone(install_status_pilot(app,environ={}))
        self.assertFalse(any('/api/vision/' in route.path for route in app.routes))
        for key in ['VISION_STATUS_REGISTRY_DB','VISION_STATUS_SINGLE_AUTHORITY_ACK','VISION_STATUS_TLS_PROXY_ACK','WEB_CONCURRENCY']:
            env=self.env();env.pop(key)
            with self.subTest(key=key),self.assertRaises(PilotConfigurationError):
                install_status_pilot(FastAPI(),environ=env)
        env=self.env();env['UVICORN_WORKERS']='2'
        with self.assertRaises(PilotConfigurationError):
            install_status_pilot(FastAPI(),environ=env)
    def test_routes_deny_config_edit_media_wrong_token(self):
        app=FastAPI(); authority=install_status_pilot(app,environ=self.env())
        with TestClient(app) as client:
            headers={'Authorization':'Bearer '+SERVICE}
            for action,payload in [('config.get',{}),('detector.configure',{'expected_revision':0,'model':'yolo11n','precision':'fp32'})]:
                result=client.post('/api/vision/v1/command',headers=headers,json=self.command(action,payload))
                self.assertEqual(result.status_code,403)
                self.assertEqual(result.json()['code'],'status_only_pilot')
                self.assertEqual(result.headers['cache-control'],'no-store')
            self.assertEqual(client.post('/api/vision-media/v1/offer',headers=headers,json={}).status_code,404)
            self.assertEqual(client.post('/api/vision/v1/command',headers={'Authorization':'Bearer '+DEVICE},json=self.command()).status_code,403)
        self.assertIsNone(authority.fd)
    def test_expiry_scope_and_live_database_failure(self):
        registry=PersistentRegistry(self.path)
        with self.assertRaises(ControlError):
            registry.authorize(SERVICE,'service','mini','edit',time.time())
        with self.assertRaises(ControlError):
            registry.identify(SERVICE,'service',time.time()+1000)
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TABLE registry_meta')
        with self.assertRaises(ControlError) as error:
            registry.check('service','service','mini','status',time.time())
        self.assertEqual(error.exception.code,'registry_unavailable')
    def test_cli_reads_private_secret_and_never_prints_it(self):
        from scripts.vision_status_registry import main
        secret=self.path.parent/'secret'
        secret.write_text('fixture-other-service-'+'a'*40)
        secret.chmod(0o600)
        output=io.StringIO()
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):
            code=main(['enroll','--db',str(self.path),'--record-id','other','--kind','service','--device-id','mini','--expires-at',str(time.time()+60),'--secret-file',str(secret)])
        self.assertEqual(code,0)
        self.assertNotIn(secret.read_text(),output.getvalue())
        self.assertNotIn(secret.read_text().encode(),self.path.read_bytes())

class StatusDispatchTests(unittest.IsolatedAsyncioTestCase):
    setUp = PilotTests.setUp
    tearDown = PilotTests.tearDown
    command = PilotTests.command
    async def test_status_success_then_revoked_session_denies(self):
        authority=PilotAuthority(self.path)
        hub=authority.hub
        sent=[]
        async def send(request):
            sent.append(request)
            hub.receive('mini',request['session_nonce'],make_result(request,'ok',{'adapter':'synthetic','available':True,'config_revision':0,'detector_revision':0,'detector':{'model':'yolo11n','precision':'fp16'},'zone_count':0}))
        try:
            welcome=hub.connect(DEVICE,'mini',send)
            result=await hub.request(SERVICE,self.command())
            self.assertEqual(result['status'],'ok')
            revoke(self.path,'device')
            with self.assertRaises(ControlError):
                hub._session('mini',welcome['session_nonce'])
            self.assertEqual(len(sent),1)
        finally:
            authority.close()
if __name__=='__main__':
    unittest.main()
