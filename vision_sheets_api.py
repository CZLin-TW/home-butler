"""Status-only shared HB authentication, default-off; no new service token."""
import asyncio
from contextlib import asynccontextmanager
import fcntl
import os
import time
from fastapi import APIRouter,FastAPI,HTTPException,Request
from fastapi.responses import JSONResponse
from vision_hub import ControlError
from vision_pilot import PilotConfigurationError,StatusOnlyHub,private_path
from vision_protocol import MAX_BYTES,ProtocolError,validate_command,make_result
from vision_sheets_registry import SharedSnapshot,SnapshotRegistry

def response(body,status=200):
    return JSONResponse(body,status_code=status,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

class SnapshotHub(StatusOnlyHub):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.bindings={}
    def connect(self,token,device_id,send):
        record=self.registry.authorize(token,'device',device_id,None,self.clock())
        welcome=super().connect(token,device_id,send)
        self.bindings[welcome['session_nonce']]=record
        return welcome
    def _session(self,device_id,nonce=None,scope=None):
        session=super()._session(device_id,nonce,scope)
        current=self.registry.check(session.record_id,'device',device_id,scope,self.clock())
        if self.bindings.get(session.nonce)!=current:
            self.disconnect(device_id,session.nonce)
            raise ControlError('credential_revoked',403)
        return session
    def disconnect(self,device_id,nonce):
        self.bindings.pop(nonce,None)
        return super().disconnect(device_id,nonce)

def create_components(reader,api_key_verifier,*,clock=time.time,monotonic=time.monotonic):
    snapshot=SharedSnapshot(reader,clock=clock,monotonic=monotonic)
    hub=SnapshotHub(SnapshotRegistry(snapshot,api_key_verifier),clock=clock)
    def revalidate():
        for device,session in list(hub.sessions.items()):
            try:
                hub._session(device,session.nonce)
            except ControlError:
                hub.disconnect(device,session.nonce)
        for entry in list(hub._entries.values()):
            if not entry.future.done():
                try:
                    hub.registry.check(entry.service_record,'service',entry.request['device_id'],'status',clock())
                except ControlError:
                    hub._finish(entry,make_result(entry.request,'unknown',code='credential_revoked'))
    snapshot.on_change=revalidate
    router=APIRouter(prefix='/api/vision/v1')
    def actor(request):
        if request.query_params:
            raise ControlError('invalid_payload',400)
        api_key_verifier(request.headers.get('x-api-key',''))
        expiry=request.headers.get('x-dashboard-session-expires','')
        if not expiry.isascii() or not expiry.isdecimal() or len(expiry)>12 or int(expiry)<=clock():
            raise ControlError('session_expired',403)
        user=request.headers.get('x-dashboard-user','')
        caps=snapshot.capabilities(user,request.headers.get('x-dashboard-role',''))
        return user,caps,int(expiry)
    @router.get('/access')
    async def access(request:Request):
        try:
            _,caps,_=actor(request)
            return response({'capabilities':caps})
        except HTTPException as error:
            return response({'code':'unauthorized'},error.status_code)
        except ControlError as error:
            return response({'code':error.code},error.status)
    @router.post('/command')
    async def command(request:Request):
        try:
            user,caps,expiry=actor(request)
            if not caps['status']:
                raise ControlError('vision_forbidden',403)
            if request.headers.get('content-type','').split(';')[0].strip()!='application/json':
                raise ControlError('json_required',415)
            raw=bytearray()
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw)>MAX_BYTES:
                        raise ControlError('payload_too_large',413)
            value=validate_command(bytes(raw),clock())
            if value['deadline']>expiry:
                raise ControlError('session_expired',403)
            result=await hub.request(user,value)
            _,current,_=actor(request)
            if not current['status']:
                raise ControlError('vision_forbidden',403)
            return response(result)
        except HTTPException as error:
            return response({'code':'unauthorized'},error.status_code)
        except (ControlError,ProtocolError) as error:
            return response({'code':error.code},getattr(error,'status',400))
        except TimeoutError:
            return response({'code':'deadline_exceeded'},408)
    # Device Bearer path is unchanged; service command handler above is the only HTTP path.
    from vision_api import create_vision_router
    device_router=create_vision_router(hub)
    device_router.routes=[r for r in device_router.routes if r.path.endswith('/device')]
    return snapshot,hub,router,device_router

def attach(app,reader,api_key_verifier,*,clock=time.time,monotonic=time.monotonic,close=lambda:None):
    snapshot,hub,router,device_router=create_components(reader,api_key_verifier,clock=clock,monotonic=monotonic)
    app.include_router(router)
    app.include_router(device_router)
    previous=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        task=None
        try:
            await snapshot.refresh(force=True)  # No serving with an uninitialized authorization snapshot.
            async def maintenance():
                while True:
                    await asyncio.sleep(.25)
                    try:
                        snapshot.current()
                    except ControlError:
                        snapshot.invalidate()
                    snapshot.on_change()
                    if monotonic()>=snapshot.next_refresh:
                        try:
                            await snapshot.refresh()
                        except ControlError:
                            pass
            task=asyncio.create_task(maintenance())
            async with previous(application) as state:
                yield state
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
            snapshot.close()
            close()
    app.router.lifespan_context=lifespan
    app.state.vision_snapshot=snapshot
    app.state.vision_control_hub=hub
    app.state._vision_control_installed=True
    return snapshot

def create_sheets_app(reader,api_key_verifier,*,clock=time.time,monotonic=time.monotonic):
    app=FastAPI()
    attach(app,reader,api_key_verifier,clock=clock,monotonic=monotonic)
    return app

def install_sheets_status_pilot(app,*,environ=None,reader=None,api_key_verifier=None):
    env=os.environ if environ is None else environ
    if env.get('VISION_STATUS_PILOT_ENABLED')!='1':
        return None
    if env.get('VISION_STATUS_SINGLE_AUTHORITY_ACK')!='1' or env.get('VISION_STATUS_TLS_PROXY_ACK')!='1' or env.get('WEB_CONCURRENCY')!='1' or env.get('UVICORN_WORKERS','1')!='1' or getattr(app.state,'_vision_control_installed',False):
        raise PilotConfigurationError('pilot_deployment_guards_required')
    fd=None
    try:
        # Existing private ephemeral lock file: no persistent DB or paid volume dependency.
        path=private_path(env.get('VISION_STATUS_AUTHORITY_LOCK',''))
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if api_key_verifier is None:
            from auth import verify_api_key
            api_key_verifier=verify_api_key
        if reader is None:
            from vision_sheets_source import ProductionSheetsReader
            reader=ProductionSheetsReader()
        info=os.fstat(fd)
        identity=(info.st_dev,info.st_ino)
        owner_pid=os.getpid()
        def guard():
            current=private_path(path).stat()
            if os.getpid()!=owner_pid or (current.st_dev,current.st_ino)!=identity:
                raise ValueError('authority_changed')
        snapshot=attach(app,reader,api_key_verifier,close=lambda:os.close(fd))
        snapshot.authority_guard=guard
        guard()
        return snapshot
    except Exception:
        if fd is not None:
            os.close(fd)
        raise PilotConfigurationError('sheets_authority_unavailable') from None
