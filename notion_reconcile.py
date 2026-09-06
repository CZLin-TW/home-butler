"""Pure Notion-to-Sheets diff. No credentials, network calls or row mutations."""
from dataclasses import dataclass, field

EXTERNAL_ID_COLUMN = "外部ID"
OWNED_FIELDS = ("事項", "日期", "時間", "負責人", "狀態", "類型", "來源", "屬性", EXTERNAL_ID_COLUMN)


def legacy_key(name, date, time):
    return tuple(str(v or "").strip() for v in (name, date, time))


def _legacy(row):
    return legacy_key(row.get("事項"), row.get("日期"), row.get("時間"))


def _id(row):
    return str(row.get(EXTERNAL_ID_COLUMN) or "").strip()


def _key(row):
    return (str(row.get("負責人") or "").strip(), ("id", _id(row)) if _id(row) else ("legacy", _legacy(row)))


@dataclass
class Changes:
    updates: list = field(default_factory=list)
    additions: list = field(default_factory=list)
    removals: list = field(default_factory=list)


def plan_changes(values, fetched):
    """Only successful members in fetched are eligible for changes.

    Completion markers apply across members, as before. Local rows and unrelated
    sources are never touched; per-row settings outside OWNED_FIELDS are preserved.
    """
    if not values:
        return Changes()
    headers = values[0]
    missing = set(OWNED_FIELDS) - set(headers)
    if missing:
        raise ValueError(f"Missing todo columns: {sorted(missing)}")
    existing = [(n, dict(zip(headers, row))) for n, row in enumerate(values[1:], start=2)]
    external = [(n, r) for n, r in existing if str(r.get("來源", "")).strip() == "Notion"]
    completed_ids = {_id(r) for _, r in external if r.get("狀態") == "已完成" and _id(r)}
    completed_legacy = {_legacy(r) for _, r in external if r.get("狀態") == "已完成" and not _id(r)}
    desired = {}
    by_legacy = {}
    for person, (permission, events) in fetched.items():
        for page_id, name, date, time in events:
            row = dict(zip(OWNED_FIELDS, (name, date, time, person, "待辦", "私人", "Notion", permission, page_id)))
            desired[_key(row)] = row
            by_legacy[(person, _legacy(row))] = row
    live_ids = {_id(r) for r in desired.values() if _id(r)}
    live_legacy = {_legacy(r) for r in desired.values()}
    changes = Changes()
    seen = set()
    for number, row in external:
        person = str(row.get("負責人") or "").strip()
        status = row.get("狀態")
        if person not in fetched or status not in {"待辦", "已完成"}:
            continue
        target = desired.get(_key(row)) if _id(row) else by_legacy.get((person, _legacy(row)))
        if status == "已完成":
            still_live = _id(row) in live_ids if _id(row) else _legacy(row) in live_legacy
            if not still_live:
                changes.removals.append(number)
                continue
            if target:
                # Migrate a matching legacy marker so later renames keep it completed.
                target = {**target, "狀態": "已完成"}
        elif (not target or _key(target) in seen or _id(target) in completed_ids
              or _legacy(target) in completed_legacy):
            changes.removals.append(number)
            continue
        if target:
            seen.add(_key(target))
            updates = {h: v for h, v in target.items() if str(row.get(h) or "") != str(v or "")}
            if updates:
                changes.updates.append((number, updates))
    changes.additions = [r for key, r in desired.items()
                         if key not in seen and _id(r) not in completed_ids and _legacy(r) not in completed_legacy]
    changes.removals.sort(reverse=True)
    return changes
