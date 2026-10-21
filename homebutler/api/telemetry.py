"""Where machines report their health: their own key only, nothing else reachable with it."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from homebutler.api.auth import Caller, caller
from homebutler.domain import computers, tokens
from homebutler.store.models import utcnow

router = APIRouter()


@router.post("/v2/telemetry/heartbeat")
def heartbeat(beat: computers.Heartbeat, request: Request, who: Caller = Depends(caller("telemetry"))):
    with request.app.state.store.transaction() as session:
        computers.record(session, who.token, beat, utcnow())
    return {"ok": True}


@router.post("/api/computers/heartbeat")
def heartbeat_from_an_old_sender(beat: computers.Heartbeat, request: Request, x_api_key: str = Header(default="")):
    """The address and X-API-Key header senders used before /v2. A sender that has not updated yet keeps
    reporting through the switch-over; the key still has to be a telemetry key."""
    with request.app.state.store.transaction() as session:
        token = tokens.verify(session, x_api_key, "telemetry")
        if token is None:
            raise HTTPException(401, "需要有效的金鑰")
        computers.record(session, token, beat, utcnow())
    return {"ok": True}
