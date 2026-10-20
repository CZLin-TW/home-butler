"""Where machines report their health: their own key only, nothing else reachable with it."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from homebutler.api.auth import Caller, caller
from homebutler.domain import computers
from homebutler.store.models import utcnow

router = APIRouter()


@router.post("/v2/telemetry/heartbeat")
def heartbeat(beat: computers.Heartbeat, request: Request, who: Caller = Depends(caller("telemetry"))):
    with request.app.state.store.transaction() as session:
        computers.record(session, who.token, beat, utcnow())
    return {"ok": True}
