"""The Dashboard's sign-in by LINE code (see domain/pairing.py). Called by the Dashboard's server only."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from homebutler.api.auth import Caller, caller
from homebutler.domain import pairing
from homebutler.store.models import utcnow

router = APIRouter(prefix="/v2/pairing")


@router.post("")
def create(request: Request, _: Caller = Depends(caller("dashboard"))):
    with request.app.state.store.transaction() as session:
        return pairing.create(session, utcnow())


class Collect(BaseModel):
    device_token: str = Field(max_length=100)


@router.post("/collect")
def collect(body: Collect, request: Request, _: Caller = Depends(caller("dashboard"))):
    """POST, so the browser's secret never appears in a URL or an access log."""
    with request.app.state.store.transaction() as session:
        return pairing.collect(session, body.device_token, utcnow())
