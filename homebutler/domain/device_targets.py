"""The ACs and IR remotes Home Assistant shares, as schedule targets (and, later, for the Dashboard and
the assistant). Checked against the device list at the moment of use; a device HA no longer shares is
refused, never replaced by another with a similar name."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from homebutler.domain import devices
from homebutler.domain.schedules import Invalid
from homebutler.store.db import Store
from homebutler.store.models import Device, DeviceEntity


def _entity(session: Session, kind: str, name: str, role: str, label: str | None = None) -> tuple[Device, DeviceEntity]:
    device = session.scalars(select(Device).where(Device.kind == kind, Device.name == name, Device.removed_at.is_(None))).first()
    if device is None:
        raise Invalid(f"找不到「{name}」")
    query = select(DeviceEntity).where(DeviceEntity.device_id == device.id, DeviceEntity.role == role, DeviceEntity.removed_at.is_(None))
    if label is not None:
        query = query.where(DeviceEntity.label == label)
    found = list(session.scalars(query))
    if len(found) != 1:
        raise Invalid(f"「{name}」沒有「{label}」這個按鈕" if label is not None else f"「{name}」目前無法控制")
    return device, found[0]


class ACTarget:
    kind = "ac"

    def __init__(self, store: Store, commands):
        self.store, self.commands = store, commands

    def check(self, session: Session, name: str, params: dict) -> tuple[int, dict]:
        device, _ = _entity(session, "ac", name, "climate")
        try:
            return device.household_id, devices.ac_patch(params)
        except devices.Invalid as error:
            raise Invalid(str(error)) from None

    def run(self, name: str, params: dict) -> dict:
        with self.store.transaction() as session:
            _, entity = _entity(session, "ac", name, "climate")
            ha_id = entity.ha_id
        return self.commands.climate(ha_id, params)


class IRTarget:
    kind = "ir"

    def __init__(self, store: Store, commands):
        self.store, self.commands = store, commands

    def check(self, session: Session, name: str, params: dict) -> tuple[int, dict]:
        button = params.get("button") if isinstance(params, dict) else None
        if not isinstance(button, str) or not button:
            raise Invalid("請指定要按哪個按鈕")
        device, _ = _entity(session, "ir", name, "button", button)
        return device.household_id, {"button": button}

    def run(self, name: str, params: dict) -> dict:
        with self.store.transaction() as session:
            _, entity = _entity(session, "ir", name, "button", params["button"])
            ha_id = entity.ha_id
        return self.commands.ir(ha_id)
