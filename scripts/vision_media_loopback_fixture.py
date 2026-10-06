"""New synthetic fixture only. No household app, environment credentials, or native HTTP."""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import socket
import sys
import threading
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vision_media_hub import MediaHub, MockProvider, DEVICE_TOKEN, SERVICE_TOKEN
from vision_media_api import create_fixture_app

@dataclass
class MediaFixture:
    http_url: str
    ws_url: str
    hub: object
    provider: object
    service_token: str = SERVICE_TOKEN
    device_token: str = DEVICE_TOKEN

@contextmanager
def media_loopback_fixture(*, port=0, fresh_native=False):
    import uvicorn
    provider = MockProvider(enabled=True)
    # Zero startup quarantine is ONLY for a new fixture paired with a new native process.
    hub = MediaHub(provider, startup_quarantine=0 if fresh_native else 67)
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', port))
    listener.listen(16)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_fixture_app(hub), host='127.0.0.1', port=port, access_log=False, log_config=None, log_level='critical', ws='websockets-sansio', ws_max_size=40960, timeout_graceful_shutdown=5))
    thread = threading.Thread(target=lambda:server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+5
        while not server.started:
            if not thread.is_alive() or time.monotonic()>deadline:
                raise RuntimeError('fixture start failed')
            time.sleep(.01)
        yield MediaFixture(f'http://127.0.0.1:{port}/api/vision-media/v1',f'ws://127.0.0.1:{port}/api/vision-media/v1/device',hub,provider)
    finally:
        server.should_exit = True
        thread.join(7)
        listener.close()
        if thread.is_alive():
            server.force_exit = True
            thread.join(2)
            if thread.is_alive():
                raise RuntimeError('fixture shutdown failed')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ready-file',type=Path,required=True)
    parser.add_argument('--stop-file',type=Path)
    parser.add_argument('--revoke-file',type=Path)
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--fresh-native',action='store_true')
    parser.add_argument('--lifetime',type=int,default=180)
    args = parser.parse_args()
    if not 1 <= args.lifetime <= 300 or not 0 <= args.port <= 65535 or args.ready_file.exists():
        raise SystemExit('invalid fixture arguments')
    with media_loopback_fixture(port=args.port, fresh_native=args.fresh_native) as fixture:
        args.ready_file.write_text(json.dumps({'source':'synthetic-media-hub','http_url':fixture.http_url,'ws_url':fixture.ws_url}))
        end = time.monotonic()+args.lifetime
        while time.monotonic()<end and not (args.stop_file and args.stop_file.exists()):
            if args.revoke_file and args.revoke_file.exists():
                fixture.provider.revoked.add('synthetic-alice')
            time.sleep(.05)
if __name__ == '__main__':
    main()
