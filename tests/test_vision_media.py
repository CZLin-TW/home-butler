import asyncio
import json
import unittest
from fastapi.testclient import TestClient
from vision_media_hub import MediaHub, MockProvider, MediaError, PROTOCOL, DEVICE_ID, SERVICE_TOKEN, DEVICE_TOKEN, decode
from vision_media_api import create_fixture_app

class HubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000
        self.provider = MockProvider(enabled=True, clock=lambda:self.now)
        self.hub = MediaHub(self.provider, clock=lambda:self.now, startup_quarantine=0, timeout=.05)
        self.sent = []
        self.block = None
        async def send(command):
            self.sent.append(command)
            if self.block == command['action']:
                return
            self.reply(command)
        self.welcome = self.hub.connect(DEVICE_TOKEN, {'protocol':PROTOCOL,'type':'hello','device_id':DEVICE_ID}, send)
    def reply(self, command):
        action = command['action']
        body = {'session_id':'native-secret-id','sdp':'v=0\r\n','type':'answer','expires_at':self.now+30} if action=='offer' else {'active':True,'expires_at':self.now+30} if action=='heartbeat' else {'active':False,'reason':'user_stopped'}
        self.hub.receive(command['epoch'],{'protocol':PROTOCOL,'type':'result','id':command['id'],'epoch':command['epoch'],'status':200,'body':body})
    def actor(self, name='synthetic-alice'):
        return {'id':name,'expires_at':self.now+120}
    async def offer(self, name='synthetic-alice'):
        return await self.hub.operation(SERVICE_TOKEN,'offer',{'actor':self.actor(name),'type':'offer','sdp':'v=0\r\n'})
    async def test_offer_ownership_projection_and_idempotent_stop(self):
        result = await self.offer()
        self.assertNotEqual(result['session_id'],'native-secret-id')
        with self.assertRaises(MediaError) as failure:
            await self.hub.operation(SERVICE_TOKEN,'stop',{'actor':self.actor('synthetic-bob'),'session_id':result['session_id']})
        self.assertEqual(failure.exception.status,404)
        state = await self.hub.operation(SERVICE_TOKEN,'state',{'actor':self.actor('synthetic-bob')})
        self.assertEqual(state,{'active':False,'reason':'not_started','media':None})
        for _ in range(2):
            await self.hub.operation(SERVICE_TOKEN,'stop',{'actor':self.actor(),'session_id':result['session_id']})
        self.assertEqual([c['action'] for c in self.sent],['offer','stop'])
    async def test_concurrent_offer_single_device(self):
        self.block='offer'
        pending=asyncio.create_task(self.offer())
        await asyncio.sleep(0)
        with self.assertRaises(MediaError) as failure:
            await self.offer('synthetic-bob')
        self.assertEqual(failure.exception.status,409)
        self.reply(self.sent[0])
        await pending
    async def test_revoke_active_stops(self):
        await self.offer()
        self.provider.revoked.add('synthetic-alice')
        await self.hub.sweep()
        self.assertIsNone(self.hub.lease)
        self.assertEqual(self.sent[-1]['action'],'stop')
        with self.assertRaises(MediaError):
            await self.offer()
    async def test_revoke_during_offer_closes_late_answer(self):
        self.block='offer'
        pending=asyncio.create_task(self.offer())
        await asyncio.sleep(0)
        self.provider.revoked.add('synthetic-alice')
        self.reply(self.sent[0])
        with self.assertRaises(MediaError):
            await pending
        self.assertEqual(self.sent[-1]['action'],'stop')
        self.assertIsNone(self.hub.lease)
    async def test_timeout_unknown_no_retry(self):
        self.block='offer'
        with self.assertRaises(MediaError):
            await self.offer()
        self.assertEqual(len(self.sent),1)
        self.assertGreater(self.hub.blocked_until,self.now+60)
        with self.assertRaises(MediaError):
            await self.offer()
        self.assertEqual(len(self.sent),1)
    async def test_disconnect_reconnect_old_epoch_rejected(self):
        await self.offer()
        old=self.welcome['epoch']
        self.hub.disconnect(old)
        welcome=self.hub.connect(DEVICE_TOKEN,{'protocol':PROTOCOL,'type':'hello','device_id':DEVICE_ID},lambda _:None)
        self.assertNotEqual(welcome['epoch'],old)
        with self.assertRaises(MediaError):
            self.hub.receive(old,{})
        with self.assertRaises(MediaError):
            await self.offer()
    async def test_restart_quarantine(self):
        restarted=MediaHub(self.provider,clock=lambda:self.now)
        with self.assertRaises(MediaError) as failure:
            await restarted.operation(SERVICE_TOKEN,'offer',{'actor':self.actor(),'type':'offer','sdp':'v=0'})
        self.assertEqual(failure.exception.status,503)
        self.assertEqual(failure.exception.code,'media_result_unknown')
        self.assertEqual(restarted.blocked_until,self.now+67)
    async def test_ttl_actor_expiry_and_device_revoke(self):
        await self.offer()
        self.now+=31
        await self.hub.sweep()
        self.assertIsNone(self.hub.lease)
        self.provider.revoked.add('device')
        with self.assertRaises(MediaError):
            await self.offer()
    async def test_late_heartbeat_cannot_resurrect_stopped_lease(self):
        answer=await self.offer()
        self.block='heartbeat'
        task=asyncio.create_task(self.hub.operation(SERVICE_TOKEN,'heartbeat',{'actor':self.actor(),'session_id':answer['session_id'],'visible':True}))
        await asyncio.sleep(0)
        command=self.sent[-1]
        await self.hub.operation(SERVICE_TOKEN,'stop',{'actor':self.actor(),'session_id':answer['session_id']})
        self.reply(command)
        with self.assertRaises(MediaError):
            await task
        self.assertIsNone(self.hub.lease)
    async def test_abandoned_http_offer_is_closed(self):
        from vision_media_api import create_media_router
        self.block = 'offer'
        hub = self.hub
        owner = self
        class Request:
            headers = {'authorization':'Bearer '+SERVICE_TOKEN,'content-type':'application/json'}
            query_params = {}
            async def stream(self):
                yield json.dumps({'actor':owner.actor(),'type':'offer','sdp':'v=0'}).encode()
            async def is_disconnected(self):
                if owner.sent:
                    owner.reply(owner.sent[0])
                return True
        self.hub.timeout = .2
        endpoint = create_media_router(hub).routes[0].endpoint
        response = await endpoint('offer',Request())
        self.assertEqual(response.status_code,503)
        self.assertEqual([c['action'] for c in self.sent],['offer','stop'])
        self.assertIsNone(hub.lease)

    async def test_provider_empty_wrong_kind_denied_expired(self):
        for token in ('wrong',DEVICE_TOKEN):
            with self.assertRaises(MediaError):
                await self.hub.operation(token,'state',{'actor':self.actor()})
        for actor in (self.actor('synthetic-denied'),{'id':'synthetic-alice','expires_at':999}):
            with self.assertRaises(MediaError):
                await self.hub.operation(SERVICE_TOKEN,'state',{'actor':actor})
        with self.assertRaises(MediaError):
            MockProvider().credential(SERVICE_TOKEN,'service')

class RouterTests(unittest.TestCase):
    def test_http_bounds_auth_and_default_unregistered_main(self):
        hub=MediaHub(MockProvider(enabled=True),startup_quarantine=0)
        with TestClient(create_fixture_app(hub)) as client:
            path='/api/vision-media/v1/state'
            self.assertEqual(client.post(path,json={}).status_code,401)
            headers={'Authorization':'Bearer '+SERVICE_TOKEN,'Content-Type':'application/json'}
            response=client.post(path,headers=headers,content='x'*40961)
            self.assertEqual(response.status_code,413)
            self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertEqual(client.post(path+'?token=x',headers=headers,json={}).status_code,401)
            self.assertEqual(client.post(path,headers=headers,content='{"actor":{},"actor":{}}').status_code,400)
        from pathlib import Path
        self.assertNotIn('vision_media',Path('main.py').read_text())
    def test_strict_decode(self):
        for raw in ('{"x":NaN}','{"x":1,"x":2}'):
            with self.assertRaises(MediaError):
                decode(raw)

if __name__=='__main__':
    unittest.main()
