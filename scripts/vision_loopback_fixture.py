"""Actual loopback HTTP/WS fixture, containing SYNTHETIC credentials only.

Never imports main.py, existing household APIs, environment credentials or cameras.
Binds only 127.0.0.1 on a new ephemeral port. Used through loopback_fixture() or
`python scripts/vision_loopback_fixture.py --ready-file /tmp/vision-fixture.json`.
The CLI stops on Ctrl-C or --stop-file creation; it never installs a service.
"""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import socket
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SYNTHETIC_SERVICE_TOKEN = 'fixture-SYNTHETIC-vision-service-fixture-only-not-a-real-secret'
SYNTHETIC_DEVICE_TOKEN = 'fixture-SYNTHETIC-vision-device-fixture-only-not-a-real-secret'
SYNTHETIC_DEVICE_ID = 'synthetic-mini'


@dataclass(frozen=True)
class LoopbackFixture:
    http_url: str
    ws_url: str
    hub: object
    device_id: str = SYNTHETIC_DEVICE_ID
    service_token: str = SYNTHETIC_SERVICE_TOKEN
    device_token: str = SYNTHETIC_DEVICE_TOKEN


@contextmanager
def loopback_fixture(*, enabled=True, empty_registry=False):
    """Start only an isolated fixture; stop and close its own listener on exit."""
    from fastapi import FastAPI
    import uvicorn
    from vision_hub import CredentialRegistry, TokenRecord, hash_token
    from vision_integration import install_vision_routes

    records = [] if empty_registry else [
        TokenRecord('synthetic-service', hash_token(SYNTHETIC_SERVICE_TOKEN), 'service', frozenset({SYNTHETIC_DEVICE_ID}), frozenset({'status', 'edit'}), time.time()+600),
        TokenRecord('synthetic-device', hash_token(SYNTHETIC_DEVICE_TOKEN), 'device', frozenset({SYNTHETIC_DEVICE_ID}), frozenset({'status', 'edit'}), time.time()+600),
    ]
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    hub = install_vision_routes(app, enabled=enabled, registry=CredentialRegistry(records))
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    thread = server = None
    try:
        listener.bind(('127.0.0.1', 0))
        listener.listen(32)
        port = listener.getsockname()[1]
        config = uvicorn.Config(app, host='127.0.0.1', port=port, access_log=False, log_config=None,
                                log_level='critical', lifespan='off', ws='websockets-sansio',
                                ws_max_size=32768, timeout_graceful_shutdown=2)
        server = uvicorn.Server(config)
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), name='synthetic-vision-fixture', daemon=True)
        thread.start()
        deadline = time.monotonic()+5
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError('synthetic fixture failed to start')
            time.sleep(.01)
        yield LoopbackFixture(f'http://127.0.0.1:{port}/api/vision/v1/command',
                              f'ws://127.0.0.1:{port}/api/vision/v1/device', hub)
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=4)
            if thread.is_alive():
                server.force_exit = True
                listener.close()
                thread.join(timeout=2)
                if thread.is_alive():
                    raise RuntimeError('synthetic fixture did not stop')
        listener.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ready-file', type=Path)
    parser.add_argument('--stop-file', type=Path)
    args = parser.parse_args()
    with loopback_fixture() as fixture:
        public = {'source': 'synthetic-fixture', 'http_url': fixture.http_url, 'ws_url': fixture.ws_url, 'device_id': fixture.device_id}
        if args.ready_file:
            args.ready_file.write_text(json.dumps(public), encoding='utf-8')
        else:
            print(json.dumps(public), flush=True)
        try:
            while args.stop_file is None or not args.stop_file.exists():
                time.sleep(.1)
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
