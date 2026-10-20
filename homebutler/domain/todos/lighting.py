"""Which lights a to-do flashes when it is due.

A to-do with a time whose title is a household chore (laundry, rubbish, the gas…) gets a light reminder
by default, unless asked otherwise. The areas are fixed when the to-do is made: chosen ones, or the
household's default area. An area that is later removed is never swapped for another one.
"""
from __future__ import annotations

import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from homebutler.store.models import LightArea

CHORES = (
    "收衣服", "收衣", "晾衣服", "晾衣", "曬衣服", "曬衣", "洗衣服", "洗衣", "烘衣服", "烘衣",
    "洗衣機", "烘衣機", "倒垃圾", "垃圾", "回收", "廚餘", "拿包裹", "收包裹", "包裹", "取貨",
    "餵食", "餵貓", "餵狗", "貓砂", "澆花", "澆水", "關瓦斯", "瓦斯", "爐火", "關火",
    "洗碗", "掃地", "拖地", "吸地", "打掃",
)
NOT_CHORES = ("買", "購物", "採買", "預約", "牙醫", "看診", "醫生", "會議", "開會")


class AreaError(ValueError):
    pass


def is_chore(title: str) -> bool:
    text = str(title or "").strip()
    return bool(text) and not any(w in text for w in NOT_CHORES) and any(w in text for w in CHORES)


def default_reminder(title: str, has_time: bool) -> bool:
    return has_time and is_chore(title)


def _norm(text) -> str:
    return unicodedata.normalize("NFC", str(text or "")).strip().lower()


def areas_for(session: Session, household_id: int, wanted: bool, has_time: bool, *, area_ids=None,
              area_name: str | None = None, existing: list[LightArea] | None = None) -> list[LightArea]:
    """The areas to store for a to-do. Raises AreaError with words for the family."""
    if not wanted or not has_time:
        return []
    if area_ids is not None:
        ids = list(dict.fromkeys(area_ids))
        if not ids:
            raise AreaError("開啟燈光提醒時，請至少選擇一個提醒區域")
        found = {a.id: a for a in session.scalars(select(LightArea).where(LightArea.id.in_(ids)))}
        if any(i not in found or not found[i].usable or found[i].household_id != household_id for i in ids):
            raise AreaError("選取的提醒區域已不可用，請重新選擇")
        return [found[i] for i in ids]
    if area_name:
        target = _norm(area_name)
        for area in session.scalars(select(LightArea).where(LightArea.household_id == household_id)):
            if area.usable and target in (_norm(area.display_name), _norm(area.source_name), _norm(area.ha_target)):
                return [area]
        raise AreaError(f"找不到提醒區域「{area_name}」")
    if existing:
        return list(existing)      # unrelated edits keep every target, even unavailable ones
    default = session.scalar(select(LightArea).where(LightArea.household_id == household_id,
                                                     LightArea.is_default.is_(True), LightArea.enabled.is_(True),
                                                     LightArea.removed_at.is_(None)))
    return [default] if default is not None else []


def names(areas: list[LightArea]) -> str:
    return "、".join(a.name for a in areas)
