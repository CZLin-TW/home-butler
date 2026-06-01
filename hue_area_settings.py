"""Persistent display names for Hue lighting areas."""

from config import now_taipei
from sheets import append_record, get_or_create_sheet, update_row_fields


SHEET_NAME = "Hue 照明區域"
HEADERS = ["Hue ID", "資源類型", "Hue 名稱", "顯示名稱", "狀態", "最後更新時間"]


def _now_text() -> str:
    return now_taipei().strftime("%Y-%m-%d %H:%M")


def _worksheet():
    return get_or_create_sheet(SHEET_NAME, HEADERS)


def load_area_settings() -> dict[str, dict]:
    sheet = _worksheet()
    settings = {}
    for row in sheet.get_all_records():
        hue_id = str(row.get("Hue ID", "") or "").strip()
        if not hue_id:
            continue
        settings[hue_id] = row
    return settings


def apply_area_settings(areas: list[dict]) -> list[dict]:
    settings = load_area_settings()
    merged = []
    for area in areas:
        hue_id = str(area.get("id", "") or "").strip()
        setting = settings.get(hue_id, {})
        display_name = str(setting.get("顯示名稱", "") or "").strip()
        status = str(setting.get("狀態", "") or "").strip() or "啟用"
        merged.append({
            **area,
            "display_name": display_name or area.get("hue_name") or area.get("id", ""),
            "custom_name": display_name,
            "enabled": status != "停用",
        })
    return merged


def upsert_area_setting(
    hue_id: str,
    display_name: str,
    resource_type: str = "grouped_light",
    hue_name: str = "",
) -> dict:
    hue_id = str(hue_id or "").strip()
    if not hue_id:
        raise ValueError("Hue ID is required")

    sheet = _worksheet()
    records = sheet.get_all_records()
    updates = {
        "資源類型": resource_type,
        "Hue 名稱": hue_name,
        "顯示名稱": str(display_name or "").strip(),
        "狀態": "啟用",
        "最後更新時間": _now_text(),
    }

    for idx, row in enumerate(records, start=2):
        if str(row.get("Hue ID", "") or "").strip() == hue_id:
            update_row_fields(sheet, idx, updates)
            return {"Hue ID": hue_id, **updates}

    new_row = {"Hue ID": hue_id, **updates}
    append_record(sheet, new_row)
    return new_row
