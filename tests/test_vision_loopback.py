"""Actual ephemeral loopback HTTP/WS only; no production main or household clients."""
import asyncio
import json
import socket
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from scripts.vision_loopback_fixture import loopback_fixture
from vision_hub import CredentialRegistry
from vision_integration import install_vision_routes
from vision_protocol import PROTOCOL, encode_message, make_result


def command(fixture, rid='loopback1', action='status.get', payload=None):
    return dict(protocol=PROTOCOL,type='command',request_id=rid,device_id=fixture.device_id,
                action=action,deadline=time.time()+3,payload={} if payload is None else payload)


def status_payload():
    return dict(adapter='synthetic',available=True,config_revision=0,detector_revision=0,
                detector={'model':'yolo11n','precision':'fp16'},zone_count=0)


class IntegrationTests(unittest.TestCase):
    def test_disabled_by_default_and_only_boolean_enable(self):
        app=FastAPI()
        self.assertIsNone(install_vision_routes(app))
        self.assertEqual(TestClient(app).post('/api/vision/v1/command').status_code,404)
        for flag in ['1','true',1,None]:
            with self.assertRaises(ValueError):install_vision_routes(app,enabled=flag)

    def test_enabled_empty_registry_denies_without_household_imports(self):
        app=FastAPI()
        hub=install_vision_routes(app,enabled=True)
        self.assertIsInstance(hub.registry,CredentialRegistry)
        response=TestClient(app).post('/api/vision/v1/command',headers={'Authorization':'Bearer '+'synthetic-test-token-'*3},json={})
        self.assertEqual(response.status_code,401)
        with self.assertRaises(ValueError):install_vision_routes(app,enabled=True)


class LoopbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_http_disabled_and_empty_registry(self):
        for enabled,empty_registry,status in [(False,False,404),(True,True,401)]:
            with loopback_fixture(enabled=enabled,empty_registry=empty_registry) as fixture:
                async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=3) as client:
                    response=await client.post(fixture.http_url,headers={'Authorization':'Bearer '+fixture.service_token},json=command(fixture))
                    self.assertEqual(response.status_code,status)

    async def test_real_ws_correlation_and_http_cached_response(self):
        with loopback_fixture() as fixture:
            port=int(fixture.http_url.split(':')[2].split('/')[0])
            original_connect=socket.socket.connect
            def only_fixture(sock,address):
                if not isinstance(address,tuple) or address[:2] != ('127.0.0.1',port):
                    raise AssertionError('non-fixture network forbidden')
                return original_connect(sock,address)
            with patch('socket.socket.connect',new=only_fixture):
                async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=3) as client:
                    headers={'Authorization':'Bearer '+fixture.service_token}
                    async with connect(fixture.ws_url,additional_headers={'Authorization':'Bearer '+fixture.device_token},proxy=None,max_size=32768,open_timeout=3) as websocket:
                        await websocket.send(encode_message({'protocol':PROTOCOL,'type':'hello','device_id':fixture.device_id}))
                        welcome=json.loads(await asyncio.wait_for(websocket.recv(),3))
                        cmd=command(fixture)
                        pending=asyncio.create_task(client.post(fixture.http_url,headers=headers,json=cmd))
                        request=json.loads(await asyncio.wait_for(websocket.recv(),3))
                        self.assertEqual(request['session_nonce'],welcome['session_nonce'])
                        self.assertEqual(request['request_id'],cmd['request_id'])
                        await websocket.send(encode_message(make_result(request,'ok',status_payload())))
                        response=await pending
                        self.assertEqual(response.status_code,200)
                        self.assertEqual(response.json()['status'],'ok')
                        self.assertEqual(response.headers['cache-control'],'no-store')
                        duplicate=await client.post(fixture.http_url,headers=headers,json=cmd)
                        self.assertEqual(duplicate.json(),response.json())
                        with self.assertRaises(asyncio.TimeoutError):await asyncio.wait_for(websocket.recv(),.05)
                        conflict=await client.post(fixture.http_url,headers=headers,json={**cmd,'action':'config.get'})
                        self.assertEqual(conflict.status_code,409)

    async def test_real_disconnect_is_unknown_and_not_resent(self):
        with loopback_fixture() as fixture:
            async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=3) as client:
                headers={'Authorization':'Bearer '+fixture.service_token}
                cmd=command(fixture)
                async with connect(fixture.ws_url,additional_headers={'Authorization':'Bearer '+fixture.device_token},proxy=None,max_size=32768,open_timeout=3) as websocket:
                    await websocket.send(encode_message({'protocol':PROTOCOL,'type':'hello','device_id':fixture.device_id}))
                    await websocket.recv()
                    pending=asyncio.create_task(client.post(fixture.http_url,headers=headers,json=cmd))
                    await asyncio.wait_for(websocket.recv(),3)
                response=await pending
                self.assertEqual(response.json()['status'],'unknown')
                again=await client.post(fixture.http_url,headers=headers,json=cmd)
                self.assertEqual(again.json(),response.json())

    async def test_real_dedicated_auth_and_no_query_token(self):
        with loopback_fixture() as fixture:
            async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=3) as client:
                denied=await client.post(fixture.http_url,headers={'Authorization':'Bearer '+fixture.device_token},json=command(fixture))
                self.assertEqual(denied.status_code,403)
            for url,token in [(fixture.ws_url,fixture.service_token),(fixture.ws_url+'?token=synthetic',fixture.device_token)]:
                with self.assertRaises(InvalidStatus) as raised:
                    async with connect(url,additional_headers={'Authorization':'Bearer '+token},proxy=None,open_timeout=3):pass
                self.assertEqual(raised.exception.response.status_code,403)

    async def test_real_malformed_response_cannot_forward_private_payload(self):
        with loopback_fixture() as fixture:
            async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=3) as client:
                async with connect(fixture.ws_url,additional_headers={'Authorization':'Bearer '+fixture.device_token},proxy=None,max_size=32768,open_timeout=3) as websocket:
                    await websocket.send(encode_message({'protocol':PROTOCOL,'type':'hello','device_id':fixture.device_id}))
                    await websocket.recv()
                    pending=asyncio.create_task(client.post(fixture.http_url,headers={'Authorization':'Bearer '+fixture.service_token},json=command(fixture)))
                    request=json.loads(await asyncio.wait_for(websocket.recv(),3))
                    result=make_result(request,'ok',status_payload());result['payload']['image']='private-fixture-image'
                    await websocket.send(json.dumps(result))
                    response=await pending
                    self.assertEqual(response.json()['status'],'unknown')
                    self.assertNotIn('private-fixture-image',response.text)


if __name__=='__main__':unittest.main()
