"""Unregistered router factory: dedicated credentials, JSON control only.

Calling create_vision_router() does not start a listener. Production main.py does not
import/include this router. TLS, enrollment, credential storage and one-worker routing
must be provided and reviewed separately before any deployment.
"""
import asyncio

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from vision_hub import VisionHub, ControlError
from vision_protocol import MAX_BYTES, ProtocolError, validate_hello, encode_message


def _bearer(headers):
    auth = headers.get('authorization', '')
    if not auth.startswith('Bearer ') or auth.count(' ') != 1:
        raise ControlError('unauthorized', 401)
    return auth[7:]


def _response(body, status=200):
    return JSONResponse(body, status_code=status, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


def create_vision_router(hub=None):
    hub = hub if hub is not None else VisionHub()
    router = APIRouter(prefix='/api/vision/v1', tags=['vision-control'])

    @router.post('/command')
    async def command(request: Request):
        try:
            if request.query_params:
                raise ControlError('invalid_payload', 400)
            token = _bearer(request.headers)
            hub.registry.identify(token, 'service', hub.clock())
            if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
                raise ControlError('json_required', 415)
            async def read_body():
                data = bytearray()
                async for chunk in request.stream():
                    if len(data)+len(chunk) > MAX_BYTES:
                        raise ControlError('payload_too_large', 413)
                    data.extend(chunk)
                return bytes(data)
            data = await asyncio.wait_for(read_body(), timeout=5)
            return _response(await hub.request(token, data))
        except asyncio.TimeoutError:
            return _response({'code': 'request_timeout'}, 408)
        except ControlError as exc:
            return _response({'code': exc.code}, exc.status)
        except ProtocolError as exc:
            return _response({'code': exc.code}, 400)
        except Exception:
            return _response({'code': 'execution_unknown'}, 503)

    @router.websocket('/device')
    async def device(websocket: WebSocket):
        device_id = nonce = None
        try:
            if websocket.query_params:
                raise ControlError('unauthorized', 401)
            token = _bearer(websocket.headers)
            hub.registry.identify(token, 'device', hub.clock())
            # No application data is accepted before a header-authenticated hello.
            # Device ID is supplied by hello, so full record scoping occurs next.
            await websocket.accept()
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=5)
            hello = validate_hello(raw)
            device_id = hello['device_id']

            async def send(message):
                await websocket.send_text(encode_message(message))

            welcome = hub.connect(token, device_id, send)
            nonce = welcome['session_nonce']
            await asyncio.wait_for(send(welcome), timeout=5)
            token = None  # Only hashed registry identity remains in the hub session.
            while True:
                hub._session(device_id, nonce)  # Expiry/revocation checked during idle too.
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=1)
                except asyncio.TimeoutError:
                    continue
                hub.receive(device_id, nonce, raw)
        except (ControlError, ProtocolError, asyncio.TimeoutError, WebSocketDisconnect, RuntimeError, KeyError, TypeError):
            try:
                await websocket.close(code=1008)
            except (RuntimeError, WebSocketDisconnect):
                pass
        finally:
            if device_id is not None and nonce is not None:
                hub.disconnect(device_id, nonce)
    return router
