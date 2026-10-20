"""Bring a member's Notion tasks in as to-dos (Notion is read-only here).

Called only with the events of a member whose Notion query succeeded; a failed query must change
nothing for that member, or one timeout would wipe their "done" markers and bring every task back.

Matching is by Notion page id, which survives renames and date changes:
  - a task still in Notion: title, date and time follow Notion; its done/open status stays ours
  - a task no longer in Notion (or no longer matching the member's filter): archived; if it comes back it
    opens again
  - a new task: added as an open, private, read-only (unless the member may write) to-do
Local settings on a Notion to-do (light reminder, areas) are kept across syncs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from homebutler.store.models import Member, Todo, utcnow


@dataclass(frozen=True)
class Event:
    page_id: str
    title: str
    due_date: date
    due_time: time | None


@dataclass
class Changes:
    added: int = 0
    updated: int = 0
    archived: int = 0


def apply(session: Session, member: Member, events: list[Event]) -> Changes:
    changes = Changes()
    wanted = {e.page_id: e for e in events if e.page_id and e.title}
    existing = {t.external_id: t for t in session.scalars(
        select(Todo).where(Todo.source == "notion", Todo.assignee_id == member.id, Todo.archived_at.is_(None)))}
    for page_id, todo in existing.items():
        event = wanted.get(page_id)
        if event is None:
            todo.archived_at = utcnow()
            changes.archived += 1
            continue
        if (todo.title, todo.due_date, todo.due_time) != (event.title, event.due_date, event.due_time):
            todo.title, todo.due_date, todo.due_time = event.title, event.due_date, event.due_time
            changes.updated += 1
        todo.read_only = not member.notion_writable
    returning = {t.external_id: t for t in session.scalars(
        select(Todo).where(Todo.source == "notion", Todo.assignee_id == member.id, Todo.archived_at.is_not(None),
                           Todo.external_id.in_([p for p in wanted if p not in existing])))}
    # A page someone already marked done stays done for everyone it is shared with.
    done_anywhere = set(session.scalars(select(Todo.external_id).where(Todo.source == "notion", Todo.status == "done",
                                                                       Todo.archived_at.is_(None))))
    for page_id, event in wanted.items():
        if page_id in existing or page_id in done_anywhere:
            continue
        todo = returning.get(page_id)
        if todo is not None:
            # Left Notion's view earlier and is back: open again, as a new task would be.
            todo.archived_at, todo.status, todo.completed_at = None, "open", None
            todo.title, todo.due_date, todo.due_time = event.title, event.due_date, event.due_time
            todo.read_only = not member.notion_writable
        else:
            session.add(Todo(household_id=member.household_id, title=event.title, due_date=event.due_date, due_time=event.due_time,
                             assignee_id=member.id, visibility="private", source="notion", read_only=not member.notion_writable,
                             external_id=page_id))
        changes.added += 1
    session.flush()
    return changes
