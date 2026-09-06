"""Dedicated appliance capability. Never mount owner routes on this router."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from auth import verify_device_voice_key
from config import claude
from device_voice import run_device_voice, device_list_reply
from handlers.device import (
    handle_control_ac, handle_control_ir, handle_control_dehumidifier,
    handle_query_sensor, handle_query_dehumidifier,
)
from sheets import RequestContext

router = APIRouter(prefix="/api/assistant", dependencies=[Depends(verify_device_voice_key)])


class DeviceVoiceRequest(BaseModel):
    # Reject user_id, role, mode and direct actions rather than implying they grant rights.
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=500, strict=True)


DEVICE_HANDLERS = {
    "control_ac": handle_control_ac,
    "control_ir": handle_control_ir,
    "control_dehumidifier": handle_control_dehumidifier,
    "query_sensor": handle_query_sensor,
    "query_dehumidifier": handle_query_dehumidifier,
    "query_devices": device_list_reply,
}


@router.post("/devices")
def api_device_voice(req: DeviceVoiceRequest):
    from request_timing import request_timing

    with request_timing("siri_devices"):
        text = req.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="text 不可為空")
        try:
            reply = run_device_voice(text, RequestContext(), claude, DEVICE_HANDLERS)
        except Exception:
            # Do not return provider exceptions, keys or private request context.
            raise HTTPException(status_code=503, detail="家電語音服務暫時無法使用，請稍後再試") from None
        return {"reply": reply}
