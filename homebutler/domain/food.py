"""The food stock. An item is chosen by id, or by name; when several share a name the one expiring
first is used (the one to eat first), and the reply says which."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from homebutler.domain.todos.service import Outcome, parse_date
from homebutler.store.models import FoodItem, Member, utcnow

EXPIRING_DAYS = 2     # the daily summary lists what is past or due within this many days


def active(session: Session, household_id: int | None = None) -> list[FoodItem]:
    query = select(FoodItem).where(FoodItem.status == "active")
    if household_id is not None:
        query = query.where(FoodItem.household_id == household_id)
    return list(session.scalars(query.order_by(FoodItem.expires_on.is_(None), FoodItem.expires_on, FoodItem.id)))


def _choose(session, household_id, item_id=None, name=None):
    query = select(FoodItem).where(FoodItem.status == "active")
    if household_id is not None:
        query = query.where(FoodItem.household_id == household_id)
    if item_id not in (None, ""):
        try:
            return session.scalar(query.where(FoodItem.id == int(item_id)))
        except (TypeError, ValueError):
            return None
    return session.scalar(query.where(FoodItem.name == str(name or "").strip())
                          .order_by(FoodItem.expires_on.is_(None), FoodItem.expires_on, FoodItem.id))


def _label(item: FoodItem, several: bool) -> str:
    return f"{item.name}（{item.expires_on.isoformat()}）" if several and item.expires_on else item.name


def _several(session, item: FoodItem) -> bool:
    return len(list(session.scalars(select(FoodItem.id).where(FoodItem.status == "active", FoodItem.name == item.name,
                                                              FoodItem.household_id == item.household_id)))) > 1


def add(session: Session, actor: Member | None, today: date, *, name: str, quantity=1, unit: str = "個", expiry=None,
        household_id: int | None = None) -> Outcome:
    name = str(name or "").strip()
    if not name:
        return Outcome(False, "❌ 請告訴我食品的名稱")
    try:
        expires, count = parse_date(expiry), int(quantity if quantity not in (None, "") else 1)
    except (ValueError, TypeError) as error:
        return Outcome(False, f"❌ {error}")
    if count < 1:
        return Outcome(False, "❌ 數量要大於 0")
    home = household_id or (actor.household_id if actor else None)
    if home is None:
        return Outcome(False, "❌ 不知道這項食品屬於哪一戶")
    item = FoodItem(household_id=home, name=name, quantity=count, unit=str(unit or "個").strip() or "個", expires_on=expires,
                    added_on=today, added_by=actor)
    session.add(item)
    session.flush()
    return Outcome(True, f"✅ 已新增 {name}，過期日 {expires.isoformat() if expires else '未設定'}")


def consume(session: Session, household_id: int | None, *, item_id=None, name=None) -> Outcome:
    item = _choose(session, household_id, item_id, name)
    if item is None:
        return Outcome(False, f"❌ 找不到 {name or '這項食品'}")
    label = _label(item, _several(session, item))
    item.status, item.consumed_at = "consumed", utcnow()
    return Outcome(True, f"✅ 已標記 {label} 為已消耗")


def modify(session: Session, household_id: int | None, changes: dict, *, item_id=None, name=None) -> Outcome:
    """changes: name, quantity (0 or less means used up), unit, expiry."""
    item = _choose(session, household_id, item_id, name)
    if item is None:
        return Outcome(False, f"❌ 找不到 {name or '這項食品'}")
    label = _label(item, _several(session, item))
    try:
        quantity = int(changes["quantity"]) if changes.get("quantity") not in (None, "") else None
        expires = parse_date(changes["expiry"]) if changes.get("expiry") else None
    except (ValueError, TypeError) as error:
        return Outcome(False, f"❌ {error}")
    if quantity is not None and quantity <= 0:
        item.status, item.consumed_at = "consumed", utcnow()
        return Outcome(True, f"✅ {label} 已全部消耗")
    if not any(changes.get(k) not in (None, "") for k in ("name", "quantity", "unit", "expiry")):
        return Outcome(False, f"❌ 找到 {label} 但沒收到任何要更新的欄位")
    if changes.get("name"):
        item.name = str(changes["name"]).strip()
    if quantity is not None:
        item.quantity = quantity
    if changes.get("unit"):
        item.unit = str(changes["unit"]).strip()
    if expires is not None:
        item.expires_on = expires
    return Outcome(True, f"✅ {label} 已更新")


def listing(items: list[FoodItem]) -> str:
    if not items:
        return "目前庫存是空的"
    return "目前庫存：\n" + "\n".join(
        f"• {i.name} {i.quantity}{i.unit}（{i.expires_on.isoformat() if i.expires_on else '未設定'}）" for i in items)


def expiring(session: Session, today: date, household_id: int | None = None) -> list[FoodItem]:
    """Past their date or due within EXPIRING_DAYS, for the daily summary."""
    return [i for i in active(session, household_id) if i.expires_on and (i.expires_on - today).days <= EXPIRING_DAYS]
