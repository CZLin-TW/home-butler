"""Dashboard configuration; restricted voice/bridge keys are excluded."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from auth import verify_api_key
import ac_feedback

router = APIRouter(prefix="/api/ac/feedback", dependencies=[Depends(verify_api_key)])


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_name: str
    config: dict


@router.get("")
def get_feedback():
    from sheets import get_sheet_records
    import sensor_state
    with ac_feedback.CONTROL_LOCK:
        rows = get_sheet_records("智能居家")
        sensors = sensor_state.snapshot(include_history=False)
        devices = {}
        for row in rows:
            if row.get("類型") == "空調" and row.get("狀態") == "啟用":
                try:
                    ac_feedback._unique(rows, row["名稱"])
                except ValueError:
                    continue
                devices[row["名稱"]] = ac_feedback.describe(row, sensors)
        return {"devices": devices, "sensors": [
            {"name": r["名稱"], "location": r.get("位置", "")} for r in rows
            if r.get("類型") == "感應器" and r.get("狀態") == "啟用"]}


@router.post("")
def set_feedback(req: FeedbackRequest):
    try:
        cfg = ac_feedback.save_config(req.device_name, req.config)
        return {"config": cfg}
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except Exception:
        raise HTTPException(503, "設定保存未確認，請重新讀取後再操作") from None
