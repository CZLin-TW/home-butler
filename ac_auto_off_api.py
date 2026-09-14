"""Read-only diagnostics; hours configuration is owned by Sheets."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from auth import verify_api_key
import ac_auto_off

router = APIRouter(prefix="/api/ac/auto-off", dependencies=[Depends(verify_api_key)])


class AutoOffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_name: str
    hours: int = Field(ge=0, le=168, strict=True)


@router.get("")
def get_auto_off():
    from sheets import RequestContext
    ctx = RequestContext()
    ctx.load(["智能居家", "排程指令"])
    devices = ctx.get("智能居家")
    return {"devices": {d["名稱"]: ac_auto_off.describe(d, ctx.get("排程指令")) for d in devices
                        if d.get("類型") == "空調" and d.get("狀態") == "啟用"
                        and sum(r.get("名稱") == d.get("名稱") for r in devices) == 1}}


@router.post("")
def set_auto_off(req: AutoOffRequest):
    raise HTTPException(410, "自動關機時數請直接在 Sheet 管理；本次排程請使用一般排程編輯")
