"""Apply the hand-edited tabs to the database.

Each tab is applied only when its content changed (fingerprint), in one transaction, and only if every
row it needs is sound enough: a tab with a problem row still applies its good rows and records the
problems (shown to the operator), but a tab that cannot be read at all changes nothing. A row removed
from the spreadsheet is marked removed, never deleted, so what refers to it stays valid.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from homebutler.domain import households
from homebutler.domain.passwords import hash_password, verify_password
from homebutler.store.db import Store
from homebutler.store.models import ConfigSource, DeviceSetting, Member, Remote, utcnow

log = logging.getLogger("homebutler.config")

MEMBERS = "家庭成員"
REMOTES = "遙控器"
DEVICES = "智能居家"
TABS = [MEMBERS, REMOTES, DEVICES]
KINDS = {"空調": "ac", "IR": "ir", "感應器": "sensor", "除濕機": "dehumidifier"}
TRUE = {"TRUE", "1", "YES", "Y", "ON", "是", "要", "啟用", "V", "✓"}


def truthy(value) -> bool:
    return str(value or "").strip().upper() in TRUE


def fingerprint(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@dataclass
class Result:
    applied: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    problems: dict[str, list[str]] = field(default_factory=dict)


def _household(session: Session, row: dict, default_key: str):
    return households.ensure(session, (row.get("戶別") or "").strip() or default_key)


def apply_members(session: Session, rows: list[dict], default_household: str) -> list[str]:
    problems, seen = [], set()
    existing = list(session.scalars(select(Member)))
    by_line = {m.line_user_id: m for m in existing if m.line_user_id}
    by_name = {m.name: m for m in existing if not m.line_user_id}
    for number, row in enumerate(rows, start=2):
        name, line_id = row.get("名稱", ""), row.get("Line User ID", "") or None
        if not name:
            problems.append(f"第 {number} 列沒有名稱，略過")
            continue
        key = line_id or "name:" + name
        if key in seen:
            problems.append(f"第 {number} 列與前面重複（{name}），略過")
            continue
        seen.add(key)
        member = by_line.get(line_id) if line_id else by_name.get(name)
        if member is None:
            member = Member(name=name, household_id=_household(session, row, default_household).id)
            session.add(member)
        member.name = name
        member.line_user_id = line_id
        member.household_id = _household(session, row, default_household).id
        member.title = row.get("稱謂", "")
        member.active = row.get("狀態", "") == "啟用"
        member.receives_alerts = truthy(row.get("系統告警"))
        member.notion_database_id = row.get("Notion Database ID", "")
        member.notion_filter = row.get("Notion 篩選", "")
        member.notion_writable = (row.get("Notion 權限") or "唯讀") == "讀寫"
        member.removed_at = None
    for member in existing:
        key = member.line_user_id or "name:" + member.name
        if key not in seen and member.removed_at is None:
            member.removed_at, member.active = utcnow(), False
    return problems


def apply_remotes(session: Session, rows: list[dict], default_household: str) -> list[str]:
    problems, seen = [], set()
    existing = {r.name: r for r in session.scalars(select(Remote))}
    for number, row in enumerate(rows, start=2):
        name, password = row.get("名稱", ""), row.get("密碼", "")
        if not name or not password:
            problems.append(f"第 {number} 列缺名稱或密碼，略過")
            continue
        if name in seen:
            problems.append(f"第 {number} 列名稱重複（{name}），略過")
            continue
        seen.add(name)
        remote = existing.get(name)
        if remote is None:
            remote = Remote(name=name, password_hash=hash_password(password),
                            household_id=_household(session, row, default_household).id)
            session.add(remote)
        elif not verify_password(password, remote.password_hash):
            remote.password_hash = hash_password(password)
        remote.enabled = truthy(row.get("啟用"))
        remote.household_id = _household(session, row, default_household).id
        remote.removed_at = None
    for name, remote in existing.items():
        if name not in seen and remote.removed_at is None:
            remote.removed_at, remote.enabled = utcnow(), False
    return problems


def _offset(value, name, number, problems):
    if value in (None, ""):
        return 0.0
    try:
        found = float(value)
    except (TypeError, ValueError):
        problems.append(f"第 {number} 列（{name}）的補償值不是數字，當作 0")
        return 0.0
    return found if abs(found) <= 20 else 0.0


def apply_device_settings(session: Session, rows: list[dict], default_household: str) -> list[str]:
    """Settings only. The Auth column (a device credential) and the 最後… columns (old state) are never read."""
    problems, seen = [], set()
    existing = {s.name: s for s in session.scalars(select(DeviceSetting))}
    for number, row in enumerate(rows, start=2):
        name = row.get("名稱", "")
        if not name:
            continue
        if name in seen:
            problems.append(f"第 {number} 列名稱重複（{name}），略過")
            continue
        seen.add(name)
        setting = existing.get(name) or DeviceSetting(name=name)
        if name not in existing:
            session.add(setting)
        setting.kind = KINDS.get(row.get("類型", ""), "other")
        setting.location = row.get("位置", "")
        setting.enabled = row.get("狀態", "啟用") != "停用"
        setting.external_id = row.get("Device ID", "")
        setting.brand = row.get("品牌", "")
        setting.temperature_offset = _offset(row.get("溫度補償"), name, number, problems)
        setting.humidity_offset = _offset(row.get("濕度補償"), name, number, problems)
        setting.auto_off_hours_text = row.get("自動關機小時數", "")[:40]
        setting.auto_off_hold_text = row.get("自動關機暫緩時段", "")[:40]
        setting.humidity_rule_text = row.get("濕度控制規則", "")
        setting.removed_at = None
    for name, setting in existing.items():
        if name not in seen and setting.removed_at is None:
            setting.removed_at = utcnow()
    return problems


APPLY = {MEMBERS: apply_members, REMOTES: apply_remotes, DEVICES: apply_device_settings}


class ConfigSync:
    def __init__(self, store: Store, source, default_household: str, clock=time.monotonic):
        self.store, self.source, self.default_household, self.clock = store, source, default_household, clock
        self.last_modified = None

    def run(self, force: bool = False) -> Result:
        """One check: skip when Google says nothing changed, else apply each changed tab."""
        result = Result()
        modified = self.source.modified_time()
        if not force and modified is not None and modified == self.last_modified:
            result.unchanged = list(TABS)
            return result
        tabs = self.source.read(TABS)       # raises: nothing is changed and the next run retries
        for tab in TABS:
            rows = tabs[tab]
            mark = fingerprint(rows)
            with self.store.transaction() as session:
                source = session.get(ConfigSource, tab)
                if source is not None and source.fingerprint == mark and not force:
                    result.unchanged.append(tab)
                    continue
                problems = APPLY[tab](session, rows, self.default_household)
                if source is None:
                    source = ConfigSource(name=tab, fingerprint=mark)
                    session.add(source)
                source.fingerprint, source.synced_at, source.rows, source.problems = mark, utcnow(), len(rows), problems
            result.applied.append(tab)
            if problems:
                result.problems[tab] = problems
                log.warning("config %s: %d row(s) skipped", tab, len(problems))
        self.last_modified = modified
        return result
