#!/usr/bin/env python3
"""Explicit two-checkout offline verification; no listeners or real credentials."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--floor-checkout', required=True)
args = parser.parse_args()
ROOT = Path(__file__).resolve().parents[1]
FLOOR = Path(args.floor_checkout).resolve()
sys.path[:0] = [str(ROOT), str(FLOOR)]
from vision_hub import VisionHub, CredentialRegistry, TokenRecord, hash_token, ControlError
from vision.device_session import DeviceExecutor, InMemorySyntheticAdapter, SessionGrant

DEVICE = 'synthetic-mini'
DEVICE_TOKEN = 'synthetic-device-fixture-' + 'x' * 32
SERVICE_TOKEN = 'synthetic-service-fixture-' + 'y' * 32


class PairTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000.0
        records = [TokenRecord(kind, hash_token(token), kind, frozenset({DEVICE}),
                               frozenset({'status', 'edit'}), 1100.)
                   for kind, token in [('device', DEVICE_TOKEN), ('service', SERVICE_TOKEN)]]
        self.registry = CredentialRegistry(records)
        self.hub = VisionHub(self.registry, clock=lambda: self.now)
        self.adapter = InMemorySyntheticAdapter()
        self.dispatches = 0
        async def send(request):
            self.dispatches += 1
            result = await self.executor.handle(json.dumps(request))
            self.hub.receive(DEVICE, self.grant.session_nonce, json.dumps(result))
        welcome = self.hub.connect(DEVICE_TOKEN, DEVICE, send)
        self.grant = SessionGrant(DEVICE, welcome['session_nonce'], welcome['expires_at'])
        def valid(grant):
            try:
                self.hub._session(grant.device_id, grant.session_nonce)
                return True
            except ControlError:
                return False
        self.executor = DeviceExecutor(self.grant, self.adapter, clock=lambda: self.now, valid_session=valid)

    def command(self, action='status.get', payload=None, request_id='request-1'):
        return dict(protocol='vision.v1', type='command', device_id=DEVICE, request_id=request_id,
                    action=action, deadline=1020., payload={} if payload is None else payload)

    async def test_shared_schema_bytes_and_no_network_status(self):
        self.assertEqual((ROOT/'vision_protocol.py').read_bytes(), (FLOOR/'vision/control_protocol.py').read_bytes())
        with patch('socket.socket', side_effect=AssertionError('Network forbidden')):
            result = await self.hub.request(SERVICE_TOKEN, self.command())
        self.assertEqual(result['payload']['adapter'], 'synthetic')
        self.assertEqual(result['status'], 'ok')

    async def test_model_revision_deduplication_and_conflict(self):
        command = self.command('detector.configure', dict(expected_revision=0, model='yolo11s', precision='fp16'))
        result = await self.hub.request(SERVICE_TOKEN, command)
        self.assertEqual(result['payload']['revision'], 1)
        self.assertEqual(await self.hub.request(SERVICE_TOKEN, command), result)
        self.assertEqual(self.dispatches, 1)
        command['request_id'] = 'request-2'
        conflict = await self.hub.request(SERVICE_TOKEN, command)
        self.assertEqual(conflict['code'], 'revision_conflict')
        self.assertEqual(self.adapter.detector_revision, 1)

    async def test_config_domain_validation_and_readback(self):
        command = self.command('config.get')
        original = (await self.hub.request(SERVICE_TOKEN, command))['payload']['config']
        original['zones'][0]['polygons'] = [[[0, 0], [1, 1], [0, 1], [1, 0]]]
        result = await self.hub.request(SERVICE_TOKEN, self.command('config.replace',
                    dict(expected_revision=0, config=original), 'request-2'))
        self.assertEqual(result['code'], 'invalid_payload')
        readback = await self.hub.request(SERVICE_TOKEN, self.command('config.get', request_id='request-3'))
        self.assertEqual(readback['payload']['revision'], 0)

    async def test_revoke_and_reconnect_reject_old_session(self):
        self.registry.revoke('service')
        with self.assertRaises(ControlError):
            await self.hub.request(SERVICE_TOKEN, self.command())
        self.assertEqual(self.dispatches, 0)
        old_nonce = self.grant.session_nonce
        async def unused(message):
            raise AssertionError('No dispatch expected')
        welcome = self.hub.connect(DEVICE_TOKEN, DEVICE, unused)
        self.assertNotEqual(welcome['session_nonce'], old_nonce)
        self.assertFalse(self.hub.disconnect(DEVICE, old_nonce))
        self.assertFalse(self.executor.authorized())

    async def test_disconnect_during_mutation_returns_unknown_without_retry(self):
        started = asyncio.Event()
        class Slow:
            calls = 0
            async def execute(inner, action, payload):
                inner.calls += 1
                started.set()
                await asyncio.Future()
        slow = Slow()
        self.executor.adapter = slow
        command = self.command('detector.configure', dict(expected_revision=0, model='yolo11s', precision='fp16'))
        pending = asyncio.create_task(self.hub.request(SERVICE_TOKEN, command))
        await asyncio.wait_for(started.wait(), 1)
        self.hub.disconnect(DEVICE, self.grant.session_nonce)
        result = await asyncio.wait_for(pending, 1)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(await self.hub.request(SERVICE_TOKEN, command), result)
        self.assertEqual(slow.calls, 1)


unittest.main(argv=[sys.argv[0]], verbosity=2)
