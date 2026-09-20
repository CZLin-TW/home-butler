"""待辦 / 週期待辦共用的小工具。

從 handlers/todo.py 抽出來，讓 handlers/recurring_todo.py 能複用「燈光提醒
自動判斷」與「布林解析」邏輯，而不必跨模組 import todo.py 的私有函式
（把私有當公有 API 用、日後 refactor 會炸到別人）。

同時處理舊單區域與新多區域提醒的儲存和選取驗證。
"""

from hue_area_settings import DEFAULT_LIGHT_AREA_NAME, resolve_area, load_area_settings
from todo_light_areas import decode_area_ids, encode_area_ids, normalize_area_ids


LIGHT_NOTIFY_COLUMN = "燈光提醒"
LIGHT_AREA_ID_COLUMN = "燈光區域ID"

# 有 time 且事項屬於「家事 / 起身處理類」時，預設開燈光提醒的關鍵字。
HOUSEHOLD_LIGHT_NOTIFY_KEYWORDS = (
    "收衣服", "收衣", "晾衣服", "晾衣", "曬衣服", "曬衣", "洗衣服", "洗衣", "烘衣服", "烘衣",
    "洗衣機", "烘衣機", "倒垃圾", "垃圾", "回收", "廚餘", "拿包裹", "收包裹", "包裹", "取貨",
    "餵食", "餵貓", "餵狗", "貓砂", "澆花", "澆水", "關瓦斯", "瓦斯", "爐火", "關火",
    "洗碗", "掃地", "拖地", "吸地", "打掃",
)
HOUSEHOLD_LIGHT_NOTIFY_EXCLUSIONS = (
    "買", "購物", "採買", "預約", "牙醫", "看診", "醫生", "會議", "開會",
)


def parse_bool(value, default=False):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y", "on", "是", "要", "需要", "開", "開啟", "啟用"):
        return True
    if text in ("false", "0", "no", "n", "off", "否", "不要", "不用", "關", "關閉", "停用"):
        return False
    return default


def bool_cell(value):
    return "TRUE" if parse_bool(value) else "FALSE"


def is_household_light_notify_item(item):
    text = str(item or "").strip()
    if not text:
        return False
    if any(word in text for word in HOUSEHOLD_LIGHT_NOTIFY_EXCLUSIONS):
        return False
    return any(word in text for word in HOUSEHOLD_LIGHT_NOTIFY_KEYWORDS)


def default_light_notify(data):
    return bool(data.get("time") and is_household_light_notify_item(data.get("item")))


def resolve_light_notify(data):
    if "light_notify" in data:
        return parse_bool(data.get("light_notify"), default=False)
    return default_light_notify(data)


def resolve_light_area(data, light_notify, existing_area_id=""):
    """Return the storage cell and display label for one or more reminder targets."""
    time_value = data.get("time") if "time" in data else data.get("時間")
    if not light_notify or not time_value:
        return {"id": "", "name": ""}

    if "light_area_ids" in data:
        ids = normalize_area_ids(data["light_area_ids"])
        if not ids:
            raise ValueError("開啟燈光提醒時，請至少選擇一個提醒區域")
        settings = load_area_settings()
        for area_id in ids:
            row = settings.get(area_id)
            if (row is None or str(row.get("狀態", "啟用")).strip() == "停用"
                    or (row.get("資源類型") or "grouped_light") != "grouped_light"):
                raise ValueError("選取的提醒區域已不可用，請重新選擇")
        names = [settings[i].get("顯示名稱") or settings[i].get("Hue 名稱") or i for i in ids]
        return {"id": encode_area_ids(ids), "name": "、".join(names)}

    explicit_id = str(data.get("light_area_id") or "").strip()
    explicit_name = str(data.get("light_area") or "").strip()
    if explicit_id or explicit_name:
        return resolve_area(explicit_name, area_id=explicit_id)
    if existing_area_id:
        ids = decode_area_ids(existing_area_id)
        # Preserve every existing target for unrelated edits, including unavailable IDs.
        settings = load_area_settings()
        names = [settings.get(i, {}).get("顯示名稱") or settings.get(i, {}).get("Hue 名稱") or i for i in ids]
        return {"id": encode_area_ids(ids), "name": "、".join(names)}
    return resolve_area(DEFAULT_LIGHT_AREA_NAME)
