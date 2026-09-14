"""Dashboard-only configuration using the existing integer hours column."""
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
    from sheets import RequestContext, update_row_fields
    from ac_feedback import _unique
    from config import now_taipei
    import ha_climate
    try:
        with ac_auto_off.LOCK:
            ctx = RequestContext()
            ctx.load(["智能居家", "排程指令"])
            target = _unique(ctx.get("智能居家"), req.device_name)
            sheet = ctx.get_worksheet("智能居家")
            from schedule_execution import _rows
            live = _rows(sheet)
            fresh = _unique([r for _, r in live], req.device_name)
            matches = [(n, r) for n, r in live if r.get("Device ID") == target["Device ID"]]
            if len(matches) != 1 or fresh["Device ID"] != target["Device ID"]:
                raise ValueError("空調資料已變更，請重新讀取")
            if ac_auto_off.HOURS_COLUMN not in sheet.row_values(1):
                raise ValueError("智能居家缺少自動關機小時數欄位")
            previous = ac_auto_off.hours_for(matches[0][1])
            update_row_fields(sheet, matches[0][0], {ac_auto_off.HOURS_COLUMN: req.hours})
            target[ac_auto_off.HOURS_COLUMN] = req.hours
            if ha_climate.managed(req.device_name):
                ac_auto_off.reconcile(ctx, now_taipei())
            else:
                from handlers.device import maintain_ac_auto_schedule
                maintain_ac_auto_schedule(req.device_name, ctx, transitioned_to_on=previous != req.hours)
            return ac_auto_off.describe(target, ctx.get("排程指令"))
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except Exception:
        raise HTTPException(503, "保存結果未確認，請重新讀取；不要自動重送") from None
