"""Opt-in fixture router factory. Deliberately absent from main and control integration."""
import asyncio
from contextlib import asynccontextmanager
from fastapi import APIRouter, FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from vision_media_hub import MediaError, MediaHub, MockProvider, MAX_BYTES, decode

def bearer(headers, query):
    value = headers.get('authorization', '')
    if query or not value.startswith('Bearer ') or len(value) > 256:
        raise MediaError('unauthorized', 401)
    return value[7:]

def create_media_router(hub):
    router = APIRouter(prefix='/api/vision-media/v1')
    @router.post('/{action}')
    async def operation(action: str, request: Request):
        try:
            token = bearer(request.headers, request.query_params)
            hub.provider.credential(token, 'service')
            if request.headers.get('content-type','').split(';')[0] != 'application/json':
                raise MediaError('invalid_payload', 415)
            raw = bytearray()
            async with asyncio.timeout(4):
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES:
                        raise MediaError('payload_too_large', 413)
            task = asyncio.create_task(hub.operation(token, action, decode(raw)))
            disconnected = False
            try:
                while not task.done():
                    await asyncio.wait({task}, timeout=.05)
                    disconnected = disconnected or await request.is_disconnected()
                body = await task
            except asyncio.CancelledError:
                # Preserve the bounded operation long enough to identify and stop a late offer.
                try:
                    body = await asyncio.shield(task)
                    if action == 'offer' and hub.lease and hub.lease['id'] == body.get('session_id'):
                        await hub.cleanup(hub.lease)
                except (MediaError, asyncio.CancelledError):
                    pass
                raise
            if disconnected and action == 'offer' and hub.lease and hub.lease['id'] == body.get('session_id'):
                await hub.cleanup(hub.lease)
                raise MediaError('execution_unknown', 503)
            return JSONResponse(body, headers={'Cache-Control':'no-store'})
        except TimeoutError:
            return JSONResponse({'code':'deadline_exceeded'}, status_code=408, headers={'Cache-Control':'no-store'})
        except MediaError as error:
            return JSONResponse({'code':error.code}, status_code=error.status, headers={'Cache-Control':'no-store'})
    @router.websocket('/device')
    async def device(socket: WebSocket):
        epoch = None
        try:
            token = bearer(socket.headers, socket.query_params)
            hub.provider.credential(token, 'device')
            await socket.accept()
            hello = decode(await asyncio.wait_for(socket.receive_text(), 4))
            welcome = hub.connect(token, hello, socket.send_json)
            epoch = welcome['epoch']
            await asyncio.wait_for(socket.send_json(welcome), 4)
            while True:
                hub.provider.credential(token, 'device')
                try:
                    raw = await asyncio.wait_for(socket.receive_text(), 1)
                except TimeoutError:
                    continue
                hub.receive(epoch, decode(raw))
        except Exception:
            pass  # Do not log SDP, network addresses, tokens, or untrusted exception text.
        finally:
            if epoch:
                hub.disconnect(epoch)
            try:
                await socket.close(code=1008)
            except Exception:
                pass
    return router

def create_fixture_app(hub=None):
    hub = hub or MediaHub(MockProvider())
    @asynccontextmanager
    async def lifespan(app):
        async def sweep():
            while True:
                await asyncio.sleep(.2)
                await hub.sweep()
        task = asyncio.create_task(sweep())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if hub.lease:
                await hub.cleanup(hub.lease)
            if hub.device:
                hub.disconnect(hub.device['epoch'])
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(create_media_router(hub))
    return app
