#!/usr/bin/env python3
"""Launch only synthetic loopback HB + outbound fixture, then real Dashboard HTTP test."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

parser = argparse.ArgumentParser()
parser.add_argument('--floor-checkout', required=True)
parser.add_argument('--dashboard-checkout', required=True)
parser.add_argument('--node', default='node')
args = parser.parse_args()
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(args.floor_checkout).resolve())]
from scripts.vision_loopback_fixture import loopback_fixture
from vision.outbound_fixture import FixtureCredential, connect_fixture
from vision.fixture_store import create_fixture_directory, FixtureFileAdapter

async def verify(fixture, directory):
    credential = FixtureCredential(fixture.device_token, time.time()+120)
    adapter = FixtureFileAdapter(directory)
    device = asyncio.create_task(connect_fixture(fixture.ws_url, credential, fixture.device_id, adapter))
    try:
        for _ in range(100):
            if fixture.hub.sessions: break
            if device.done(): raise RuntimeError('synthetic connector failed')
            await asyncio.sleep(.02)
        else: raise RuntimeError('synthetic connector timeout')
        env = {'PATH': os.environ.get('PATH', ''), 'NODE_ENV': 'test',
               'SESSION_JWT_SECRET': 'fixture-SYNTHETIC-session-signing-key-not-production',
               'DASHBOARD_VISION_FIXTURE_MODE': '1',
               'DASHBOARD_VISION_FIXTURE_PORT': str(urlsplit(fixture.http_url).port),
               'DASHBOARD_VISION_FIXTURE_TOKEN': fixture.service_token,
               'DASHBOARD_VISION_FIXTURE_DEVICE_ID': fixture.device_id,
               'DASHBOARD_VISION_GRANTS': json.dumps({'synthetic-member': ['status', 'edit']})}
        child = await asyncio.create_subprocess_exec(args.node, '--import', 'tsx',
            'scripts/test-vision-live-fixture.ts', cwd=Path(args.dashboard_checkout).resolve(), env=env)
        try:
            code = await asyncio.wait_for(child.wait(), 30)
        except BaseException:
            if child.returncode is None: child.kill(); await child.wait()
            raise
        if code: raise RuntimeError('Dashboard full-chain test failed')
        # A new adapter proves the model metadata actually reached fixture disk.
        reloaded = FixtureFileAdapter(directory)
        saved = await reloaded.execute('status.get', {})
        if saved['detector_revision'] != 1 or saved['detector']['model'] != 'yolo11s':
            raise RuntimeError('fixture reload failed')
        print('Fixture disk reload verified; no production files touched.')
    finally:
        credential.revoked = True
        try: await asyncio.wait_for(device, 3)
        except asyncio.TimeoutError: device.cancel(); await asyncio.gather(device, return_exceptions=True)

with create_fixture_directory() as directory, loopback_fixture() as fixture:
    asyncio.run(verify(fixture, directory))
