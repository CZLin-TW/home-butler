import asyncio
import copy
import json
import pathlib
import time
import unittest
from unittest.mock import patch

from vision_protocol import (PROTOCOL, MAX_BYTES, ProtocolError, validate_command,
    validate_request, validate_result, make_result, decode_message, validate_hello)
from vision_hub import TokenRecord, CredentialRegistry, VisionHub, ControlError, hash_token

SERVICE = 'test-service-token-' + 's'*32
DEVICE = 'test-device-token-' + 'd'*32
NOW = 1000.0


def registry(expiry=2000):
    return CredentialRegistry([
        TokenRecord('service',hash_token(SERVICE),'service',frozenset({'mini'}),frozenset({'status','edit'}),expiry),
        TokenRecord('device',hash_token(DEVICE),'device',frozenset({'mini'}),frozenset({'status','edit'}),expiry)])


def command(rid='r1',action='status.get',payload=None,deadline=1020):
    return dict(protocol=PROTOCOL,type='command',request_id=rid,device_id='mini',action=action,deadline=deadline,payload={} if payload is None else payload)


def status_payload():
    return dict(adapter='synthetic',available=True,config_revision=0,detector_revision=0,detector={'model':'yolo11n','precision':'fp16'},zone_count=0)


class ProtocolTests(unittest.TestCase):
    def test_strict_size_duplicate_json_and_finite(self):
        for raw in ['{"protocol":1,"protocol":2}', '{"x":NaN}', {'x':float('nan')}, {'x':'x'*MAX_BYTES}, '[]', b'\xff']:
            with self.subTest(raw_type=type(raw)),self.assertRaises(ProtocolError):decode_message(raw)

    def test_fixed_actions_and_no_proxy_secrets_media(self):
        for action in ['preview.get','http.get','ha.service',None,[],{}]:
            with self.subTest(action=action),self.assertRaises(ProtocolError):validate_command(command(action=action),NOW)
        for key in ['url','token','secret','frame','image','headers','path']:
            with self.assertRaises(ProtocolError):validate_command(command(payload={key:'private-value'}),NOW)
        valid=command(action='detector.configure',payload={'expected_revision':0,'model':'yolo11s','precision':'fp32'})
        self.assertEqual(validate_command(valid,NOW),valid)
        for key,value in [('expected_revision',True),('expected_revision',-1),('model','file:///private'),('precision','int8')]:
            invalid=copy.deepcopy(valid);invalid['payload'][key]=value
            with self.assertRaises(ProtocolError):validate_command(invalid,NOW)

    def test_deadline_version_and_identity(self):
        for deadline in [NOW,NOW-1,NOW+31,True,float('inf'),10**1000]:
            with self.assertRaises(ProtocolError):validate_command(command(deadline=deadline),NOW)
        request={**command(),'type':'request','session_nonce':'n'*32}
        self.assertEqual(validate_request(request,NOW,'mini','n'*32),request)
        for device,nonce in [('other','n'*32),('mini','x'*32)]:
            with self.assertRaises(ProtocolError):validate_request(request,NOW,device,nonce)
        request['protocol']='ha.v1'
        with self.assertRaises(ProtocolError):validate_request(request,NOW)
        with self.assertRaises(ProtocolError):validate_hello({'protocol':PROTOCOL,'type':'hello','device_id':'mini','token':'private'})

    def test_strict_results_cannot_exfiltrate(self):
        request={**command(),'type':'request','session_nonce':'n'*32}
        result=make_result(request,'ok',status_payload())
        self.assertEqual(validate_result(result,'status.get'),result)
        for key in ['image','secret','url','credentials']:
            bad=copy.deepcopy(result);bad['payload'][key]='private'
            with self.assertRaises(ProtocolError):validate_result(bad,'status.get')
        bad=make_result(request,'error',code='execution_unknown');bad['code']=[]
        with self.assertRaises(ProtocolError):validate_result(bad)
        bad=make_result(request,'error',code='private raw exception')
        with self.assertRaises(ProtocolError) as raised:validate_result(bad)
        self.assertNotIn('private',str(raised.exception))


class RegistryTests(unittest.TestCase):
    def test_default_deny_separate_tokens_scopes_devices_expiry_revoke(self):
        with self.assertRaises(ControlError):CredentialRegistry().authorize(SERVICE,'service','mini','status',NOW)
        r=registry()
        for token,kind,device,scope,now in [(SERVICE,'device','mini','status',NOW),(DEVICE,'service','mini','status',NOW),(SERVICE,'service','other','status',NOW),(SERVICE,'service','mini','preview',NOW),(SERVICE,'service','mini','status',2000)]:
            with self.assertRaises(ControlError):r.authorize(token,kind,device,scope,now)
        self.assertEqual(r.authorize(SERVICE,'service','mini','edit',NOW).record_id,'service')
        r.revoke('service')
        with self.assertRaises(ControlError):r.authorize(SERVICE,'service','mini','status',NOW)
        self.assertNotIn(SERVICE,repr(r.__dict__))
        self.assertNotIn(DEVICE,repr(r.__dict__))

    def test_invalid_registry_rejected_and_no_duplicate_key_roles(self):
        records=[TokenRecord('x',hash_token(SERVICE),'service',frozenset({'mini'}),frozenset({'status'}),2000)]
        with self.assertRaises(ValueError):CredentialRegistry(records+records)
        with self.assertRaises(ValueError):CredentialRegistry([TokenRecord('x','bad')])


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now=NOW
        self.registry=registry()
        self.hub=VisionHub(self.registry,clock=lambda:self.now)
        self.sent=[]
        async def send(message):self.sent.append(message)
        self.welcome=self.hub.connect(DEVICE,'mini',send)
        self.nonce=self.welcome['session_nonce']

    async def start_request(self, cmd=None):
        task=asyncio.create_task(self.hub.request(SERVICE,cmd or command()))
        for _ in range(3):await asyncio.sleep(0)
        return task

    def receive_ok(self,index=0):
        return self.hub.receive('mini',self.nonce,make_result(self.sent[index],'ok',status_payload()))

    async def test_success_duplicate_cache_and_payload_conflict(self):
        task=await self.start_request()
        self.assertTrue(self.receive_ok())
        result=await task
        self.assertEqual(result['status'],'ok')
        self.assertEqual(await self.hub.request(SERVICE,command()),result)
        self.assertEqual(len(self.sent),1)
        with self.assertRaises(ControlError) as raised:await self.hub.request(SERVICE,command(action='config.get'))
        self.assertEqual(raised.exception.code,'duplicate_conflict')

    async def test_parallel_duplicate_only_one_send(self):
        first=await self.start_request();second=await self.start_request()
        self.assertEqual(len(self.sent),1)
        self.receive_ok()
        self.assertEqual(await first,await second)

    async def test_wrong_device_and_session_fail_no_late_result(self):
        task=await self.start_request()
        with self.assertRaises(ControlError):self.hub.receive('mini','z'*32,make_result(self.sent[0],'ok',status_payload()))
        bad=make_result(self.sent[0],'ok',status_payload());bad['device_id']='other'
        with self.assertRaises(ControlError):self.hub.receive('mini',self.nonce,bad)
        self.assertEqual((await task)['status'],'unknown')

    async def test_disconnect_and_reconnect_do_not_retry(self):
        task=await self.start_request()
        self.hub.disconnect('mini',self.nonce)
        unknown=await task
        self.assertEqual(unknown['status'],'unknown')
        async def send(msg):self.sent.append(msg)
        new=self.hub.connect(DEVICE,'mini',send)
        self.assertNotEqual(new['session_nonce'],self.nonce)
        self.assertFalse(self.hub.disconnect('mini',self.nonce))
        self.assertEqual(await self.hub.request(SERVICE,command()),unknown)
        self.assertEqual(len(self.sent),1)
        with self.assertRaises(ControlError):self.hub.receive('mini',self.nonce,make_result(self.sent[0],'ok',status_payload()))

    async def test_expired_results_unknown_and_never_overwrite(self):
        task=await self.start_request();self.now=1021
        self.assertFalse(self.receive_ok())
        result=await task
        self.assertEqual(result['status'],'unknown');self.assertEqual(result['code'],'deadline_exceeded')
        self.assertEqual(await self.hub.request(SERVICE,command()),result)
        self.now=1100
        with self.assertRaises(ControlError):await self.hub.request(SERVICE,command())
        self.assertEqual(len(self.sent),1)

    async def test_timeout_unknown_and_same_id_not_resent(self):
        self.hub=VisionHub(registry(expiry=time.time()+60),clock=time.time)
        async def send(msg):self.sent.append(msg)
        self.hub.connect(DEVICE,'mini',send)
        cmd=command(deadline=time.time()+.03)
        result=await self.hub.request(SERVICE,cmd)
        self.assertEqual(result['status'],'unknown')
        self.assertEqual(await self.hub.request(SERVICE,cmd),result)
        self.assertEqual(len(self.sent),1)

    async def test_capacity_does_not_evict_replayable_result(self):
        self.hub.max_results=self.hub.max_inflight=1
        task=await self.start_request()
        with self.assertRaises(ControlError) as raised:await self.hub.request(SERVICE,command('r2'))
        self.assertEqual(raised.exception.code,'capacity_exceeded')
        self.receive_ok();await task
        with self.assertRaises(ControlError):await self.hub.request(SERVICE,command('r2'))
        self.assertEqual((await self.hub.request(SERVICE,command()))['status'],'ok')
        self.assertEqual(len(self.sent),1)

    async def test_revoke_before_dispatch_and_after_dispatch(self):
        self.registry.revoke('service')
        with self.assertRaises(ControlError):await self.hub.request(SERVICE,command())
        self.assertEqual(len(self.sent),0)
        self.hub.registry=self.registry=registry()
        task=await self.start_request();self.registry.revoke('service')
        self.assertFalse(self.receive_ok())
        self.assertEqual((await task)['status'],'unknown')

    async def test_device_revocation_or_expiry_ends_session(self):
        task=await self.start_request();self.registry.revoke('device')
        with self.assertRaises(ControlError):self.receive_ok()
        self.assertEqual((await task)['status'],'unknown')
        self.assertNotIn('mini',self.hub.sessions)

    async def test_bad_response_cannot_reach_dashboard(self):
        task=await self.start_request()
        bad=make_result(self.sent[0],'ok',status_payload());bad['payload']['image']='private'
        with self.assertRaises(ProtocolError):self.hub.receive('mini',self.nonce,bad)
        result=await task
        self.assertEqual(result['status'],'unknown');self.assertNotIn('private',str(result))

    async def test_device_scope_restriction_checked_before_send(self):
        self.hub.registry=CredentialRegistry([
            TokenRecord('service',hash_token(SERVICE),'service',frozenset({'mini'}),frozenset({'status','edit'}),2000),
            TokenRecord('device',hash_token(DEVICE),'device',frozenset({'mini'}),frozenset({'status'}),2000)])
        with self.assertRaises(ControlError):
            await self.hub.request(SERVICE,command(action='detector.configure',payload={'expected_revision':0,'model':'yolo11n','precision':'fp16'}))
        self.assertEqual(self.sent,[])

    async def test_idle_session_expiry_and_reconnect_nonce(self):
        self.now=2001
        with self.assertRaises(ControlError):self.hub._session('mini',self.nonce)
        self.assertNotIn('mini',self.hub.sessions)
        self.assertEqual(self.sent,[])

    async def test_send_failure_and_cancel_unknown(self):
        async def fails(_):raise RuntimeError('private transport details')
        self.hub.connect(DEVICE,'mini',fails)
        result=await self.hub.request(SERVICE,command())
        self.assertEqual(result['status'],'unknown');self.assertNotIn('private',str(result))
        self.hub=VisionHub(self.registry,clock=lambda:self.now)
        async def wait(_):await asyncio.Future()
        self.hub.connect(DEVICE,'mini',wait)
        task=asyncio.create_task(self.hub.request(SERVICE,command()))
        await asyncio.sleep(0);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual((await self.hub.request(SERVICE,command()))['status'],'unknown')


class RouterTests(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from vision_api import create_vision_router
        self.hub=VisionHub(registry(),clock=lambda:NOW)
        app=FastAPI();app.include_router(create_vision_router(self.hub))
        self.client=TestClient(app)

    def test_production_integration_is_explicit_and_default_empty(self):
        import ast
        tree=ast.parse(pathlib.Path('main.py').read_text())
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='install_vision_routes']
        self.assertEqual(len(calls),1)
        self.assertEqual([k.arg for k in calls[0].keywords],['enabled'])
        self.assertEqual(ast.unparse(calls[0].keywords[0].value), "os.environ.get('VISION_CONTROL_ENABLED') == '1'")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from vision_api import create_vision_router
        app=FastAPI();app.include_router(create_vision_router())
        result=TestClient(app).post('/api/vision/v1/command',headers={'Authorization':'Bearer '+SERVICE},json=command())
        self.assertEqual(result.status_code,401)

    def test_http_dedicated_auth_json_limit_query_and_failclosed(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            for headers,status in [({},401),({'Authorization':'Bearer '+DEVICE},403),({'Authorization':'Bearer '+SERVICE},503),({'X-API-Key':SERVICE},401)]:
                response=self.client.post('/api/vision/v1/command',headers=headers,json=command())
                self.assertEqual(response.status_code,status)
                self.assertEqual(response.headers['cache-control'],'no-store')
            headers={'Authorization':'Bearer '+SERVICE,'Content-Type':'application/json'}
            self.assertEqual(self.client.post('/api/vision/v1/command?token=x',headers=headers,json=command()).status_code,400)
            self.assertEqual(self.client.post('/api/vision/v1/command',headers=headers,content='x'*(MAX_BYTES+1)).status_code,413)
            self.assertEqual(self.client.post('/api/vision/v1/command',headers={'Authorization':'Bearer '+SERVICE},content='{}').status_code,415)

    def test_websocket_device_hello_and_no_query_tokens(self):
        from starlette.websockets import WebSocketDisconnect
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('/api/vision/v1/device?token=x',headers={'Authorization':'Bearer '+DEVICE}):pass
        for token in [SERVICE,'x'*40]:
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect('/api/vision/v1/device',headers={'Authorization':'Bearer '+token}):pass
        with self.client.websocket_connect('/api/vision/v1/device',headers={'Authorization':'Bearer '+DEVICE}) as ws:
            ws.send_json({'protocol':PROTOCOL,'type':'hello','device_id':'mini'})
            welcome=ws.receive_json()
            self.assertEqual(welcome['type'],'welcome')
            self.assertNotIn(DEVICE,json.dumps(welcome))
            self.assertEqual(len(self.hub.sessions),1)
        self.assertEqual(len(self.hub.sessions),0)


if __name__=='__main__':unittest.main()
