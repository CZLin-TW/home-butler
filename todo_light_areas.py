"""Multiple reminder targets in the existing 燈光區域ID cell; no schema migration."""
import json


def normalize_area_ids(value):
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("提醒區域必須是最多 32 個區域的清單")
    if any(not isinstance(item, str) or not item.strip() or item.strip().startswith("[") for item in value):
        raise ValueError("提醒區域 ID 不可為空或格式錯誤")
    return list(dict.fromkeys(item.strip() for item in value))


def decode_area_ids(cell):
    """Read legacy scalar IDs and new JSON arrays. Corrupt lists must not fall back."""
    text = str(cell or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            return normalize_area_ids(json.loads(text))
        except (ValueError, TypeError) as exc:
            raise ValueError("儲存的提醒區域格式錯誤，請重新選擇區域") from exc
    return [text]


def encode_area_ids(ids):
    ids = normalize_area_ids(ids)
    if len(ids) <= 1:
        return ids[0] if ids else ""
    return json.dumps(ids, ensure_ascii=False, separators=(",", ":"))
