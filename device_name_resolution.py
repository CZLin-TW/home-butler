"""Resolve IR names without guessing rooms, targets, or power actions."""

import unicodedata


def _name(value):
    return unicodedata.normalize("NFC", str(value or "")).strip()


def _fan_alias(value):
    name = _name(value)
    # Only this known noun variant is interchangeable; retain the full room prefix.
    if name.endswith("電風扇"):
        return name[:-3] + "電扇"
    return name


def resolve_ir_device(device_name, rows):
    """Return (configured row, error). Exact names win; ambiguous names never send."""
    devices = [r for r in rows if r.get("狀態") == "啟用" and r.get("類型") == "IR"]
    target = _name(device_name)
    if target:
        matches = [r for r in devices if _name(r.get("名稱")) == target]
        if not matches:
            matches = [r for r in devices if _fan_alias(r.get("名稱")) == _fan_alias(target)]
    else:
        # Omitted names retain the one-device convenience. An explicit wrong room
        # must never fall back to the only device in a different room.
        matches = devices

    if len(matches) > 1:
        return None, "❌ 設備名稱不明確，請指定唯一的完整設備名稱"
    if not matches:
        return None, f"❌ 找不到「{target}」，請確認設備名稱" if target else "❌ 沒有可控制的 IR 設備"
    row = matches[0]
    if not _name(row.get("Device ID")):
        return None, f"❌「{_name(row.get('名稱'))}」缺少 Device ID，請檢查設備設定"
    return row, None
