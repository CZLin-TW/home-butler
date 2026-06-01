"""Persistent display names for Hue lighting areas."""

import unicodedata

from config import now_taipei
from sheets import append_record, get_or_create_sheet, update_row_fields


SHEET_NAME = "Hue 照明區域"
HEADERS = ["Hue ID", "資源類型", "Hue 名稱", "顯示名稱", "狀態", "最後更新時間"]
DEFAULT_LIGHT_AREA_NAME = "客廳"


def _now_text() -> str:
    return now_taipei().strftime("%Y-%m-%d %H:%M")


def _worksheet():
    return get_or_create_sheet(SHEET_NAME, HEADERS)


def _norm(value: str) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip().lower()


def load_area_settings() -> dict[str, dict]:
    sheet = _worksheet()
    settings = {}
    for row in sheet.get_all_records():
        hue_id = str(row.get("Hue ID", "") or "").strip()
        if not hue_id:
            continue
        settings[hue_id] = row
    return settings


def sync_discovered_areas(areas: list[dict]) -> dict[str, dict]:
    """Ensure discovered Hue area IDs exist in the settings sheet.

    Display names are preserved once the user customizes them; Hue names are
    refreshed when the Hue App room/zone name changes.
    """
    sheet = _worksheet()
    records = sheet.get_all_records()
    by_id = {
        str(row.get("Hue ID", "") or "").strip(): (idx, row)
        for idx, row in enumerate(records, start=2)
        if str(row.get("Hue ID", "") or "").strip()
    }
    changed = False

    for area in areas:
        hue_id = str(area.get("id", "") or "").strip()
        if not hue_id:
            continue
        resource_type = str(area.get("resource_type", "") or "grouped_light").strip()
        hue_name = str(area.get("hue_name", "") or "").strip()
        if hue_id not in by_id:
            append_record(sheet, {
                "Hue ID": hue_id,
                "資源類型": resource_type,
                "Hue 名稱": hue_name,
                "顯示名稱": "",
                "狀態": "啟用",
                "最後更新時間": _now_text(),
            })
            changed = True
            continue

        row_number, row = by_id[hue_id]
        updates = {}
        if str(row.get("資源類型", "") or "").strip() != resource_type:
            updates["資源類型"] = resource_type
        if str(row.get("Hue 名稱", "") or "").strip() != hue_name:
            updates["Hue 名稱"] = hue_name
        if not str(row.get("狀態", "") or "").strip():
            updates["狀態"] = "啟用"
        if updates:
            updates["最後更新時間"] = _now_text()
            update_row_fields(sheet, row_number, updates)
            changed = True

    return load_area_settings() if changed else {
        str(row.get("Hue ID", "") or "").strip(): row
        for _, row in by_id.values()
    }


def apply_area_settings(areas: list[dict]) -> list[dict]:
    settings = sync_discovered_areas(areas)
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


def resolve_area(area_name: str = "", area_id: str = "", default_name: str = DEFAULT_LIGHT_AREA_NAME) -> dict:
    """Resolve a user-facing Hue area name to the stable grouped_light ID."""
    settings = load_area_settings()
    target_id = str(area_id or "").strip()
    if target_id and target_id in settings:
        row = settings[target_id]
        display_name = str(row.get("顯示名稱", "") or "").strip()
        hue_name = str(row.get("Hue 名稱", "") or "").strip()
        return {
            "id": target_id,
            "name": display_name or hue_name or target_id,
            "resource_type": str(row.get("資源類型", "") or "grouped_light").strip() or "grouped_light",
        }

    target = _norm(area_name or default_name)
    rows = []
    for hue_id, row in settings.items():
        if str(row.get("狀態", "") or "啟用").strip() == "停用":
            continue
        display_name = str(row.get("顯示名稱", "") or "").strip()
        hue_name = str(row.get("Hue 名稱", "") or "").strip()
        aliases = [display_name, hue_name, hue_id]
        rows.append((hue_id, row, aliases))
        if target and any(_norm(alias) == target for alias in aliases):
            return {
                "id": hue_id,
                "name": display_name or hue_name or hue_id,
                "resource_type": str(row.get("資源類型", "") or "grouped_light").strip() or "grouped_light",
            }

    if target:
        for hue_id, row, aliases in rows:
            if any(target in _norm(alias) or _norm(alias) in target for alias in aliases if alias):
                display_name = str(row.get("顯示名稱", "") or "").strip()
                hue_name = str(row.get("Hue 名稱", "") or "").strip()
                return {
                    "id": hue_id,
                    "name": display_name or hue_name or hue_id,
                    "resource_type": str(row.get("資源類型", "") or "grouped_light").strip() or "grouped_light",
                }

    return {"id": target_id, "name": area_name or default_name, "resource_type": "grouped_light"}


def display_name_for_area_id(area_id: str) -> str:
    resolved = resolve_area(area_id=area_id)
    return str(resolved.get("name") or "")


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
