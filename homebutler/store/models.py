"""The tables. Structure changes go through an Alembic version (store/migrations), never create_all.

Conventions: integer ids; English column names; times are timezone-aware UTC; something archived or
removed gets a timestamp or a status instead of moving to another table; every household-owned row
carries household_id.
"""
from __future__ import annotations

from datetime import date, datetime, time as time_of_day, timezone

from sqlalchemy import (JSON, Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, String, Table, Text, Time,
                        TypeDecorator, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class UTCDateTime(TypeDecorator):
    """Aware UTC datetimes in and out, also on SQLite (which keeps no zone)."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime()}


class Household(Base):
    """A home this system serves. `key` is short and stable ("A", "B"); `ha_floor_id` links it to the
    Home Assistant floor that is the source of truth for which area belongs where."""
    __tablename__ = "households"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    ha_floor_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Member(Base):
    """A family member. Edited in the spreadsheet for now (家庭成員), synced in; `line_user_id` is the
    identity LINE and the Dashboard's login use."""
    __tablename__ = "members"
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"))
    name: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(80), default="")
    line_user_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    butler_style: Mapped[str] = mapped_column(Text, default="")
    receives_alerts: Mapped[bool] = mapped_column(Boolean, default=False)
    notion_database_id: Mapped[str] = mapped_column(String(64), default="")
    notion_filter: Mapped[str] = mapped_column(Text, default="")
    notion_writable: Mapped[bool] = mapped_column(Boolean, default=False)
    # Gone from the spreadsheet: kept for the rows that refer to them, never active again by itself.
    removed_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    household: Mapped[Household] = relationship()


class ApiToken(Base):
    """One key per caller (the Dashboard, Home Assistant, each telemetry sender). Only a hash is kept;
    the plain key is shown once when it is issued."""
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(80))
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column()


class Remote(Base):
    """A wall tablet that signs in with a shared password instead of LINE (遙控器)."""
    __tablename__ = "remotes"
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"))
    name: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    removed_at: Mapped[datetime | None] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class PairingCode(Base):
    """A six-digit code a member sends to LINE to sign a browser in. Only a hash is kept."""
    __tablename__ = "pairing_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column()
    approved_at: Mapped[datetime | None] = mapped_column()
    used_at: Mapped[datetime | None] = mapped_column()


class SystemState(Base):
    """Small named values that must survive a restart (last daily push, alert markers, …)."""
    __tablename__ = "system_state"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ConfigSource(Base):
    """What was last read from each hand-edited spreadsheet tab, so an unchanged tab is skipped."""
    __tablename__ = "config_sources"
    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    synced_at: Mapped[datetime] = mapped_column(default=utcnow)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    problems: Mapped[list] = mapped_column(JSON, default=list)


# ── To-dos ────────────────────────────────────────────────────────────────────

class LightArea(Base):
    """A lighting target a to-do can flash when it is due (a Hue room or zone through Home Assistant).
    Discovered from Home Assistant; `display_name` is what the family calls it."""
    __tablename__ = "light_areas"
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"))
    ha_target: Mapped[str] = mapped_column(String(80), unique=True)
    resource_type: Mapped[str] = mapped_column(String(32), default="grouped_light")
    source_name: Mapped[str] = mapped_column(String(80), default="")
    display_name: Mapped[str] = mapped_column(String(80), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    removed_at: Mapped[datetime | None] = mapped_column()

    @property
    def name(self) -> str:
        return self.display_name or self.source_name or self.ha_target

    @property
    def usable(self) -> bool:
        return self.enabled and self.removed_at is None and self.resource_type == "grouped_light"


todo_light_areas = Table(
    "todo_light_areas", Base.metadata,
    Column("todo_id", ForeignKey("todos.id", ondelete="CASCADE"), primary_key=True),
    Column("light_area_id", ForeignKey("light_areas.id"), primary_key=True),
)

template_light_areas = Table(
    "template_light_areas", Base.metadata,
    Column("template_id", ForeignKey("todo_templates.id", ondelete="CASCADE"), primary_key=True),
    Column("light_area_id", ForeignKey("light_areas.id"), primary_key=True),
)


class TodoTemplate(Base):
    """A recurring to-do: the rule only. Exactly one open to-do per active template is kept ("the next
    one"); completing it lets the next appear."""
    __tablename__ = "todo_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"))
    title: Mapped[str] = mapped_column(String(200))
    rule: Mapped[str] = mapped_column(String(16))           # see domain/todos/recurrence.py RULES
    weekdays: Mapped[list] = mapped_column(JSON, default=list)   # ISO weekdays 1 (Mon) .. 7 (Sun)
    month_day: Mapped[int | None] = mapped_column(Integer)
    interval_days: Mapped[int | None] = mapped_column(Integer)
    due_time: Mapped[time_of_day | None] = mapped_column(Time)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"))
    visibility: Mapped[str] = mapped_column(String(8), default="private")
    light_reminder: Mapped[bool] = mapped_column(Boolean, default=False)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_generated_on: Mapped[date | None] = mapped_column(Date)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    assignee: Mapped["Member | None"] = relationship(foreign_keys=[assignee_id])
    light_areas: Mapped[list[LightArea]] = relationship(secondary=template_light_areas)


class Todo(Base):
    """A to-do. Dates and times are the household's wall clock (no zone). Done local to-dos are
    archived (hidden, kept); done Notion to-dos stay unarchived as the marker that keeps them done."""
    __tablename__ = "todos"
    __table_args__ = (
        UniqueConstraint("assignee_id", "external_id", name="uq_todos_external"),
        Index("ix_todos_open", "household_id", "status", "archived_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"))
    title: Mapped[str] = mapped_column(String(200))
    due_date: Mapped[date | None] = mapped_column(Date)
    due_time: Mapped[time_of_day | None] = mapped_column(Time)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"))
    visibility: Mapped[str] = mapped_column(String(8), default="private")   # private | public
    status: Mapped[str] = mapped_column(String(8), default="open")          # open | done
    source: Mapped[str] = mapped_column(String(8), default="local")         # local | notion
    read_only: Mapped[bool] = mapped_column(Boolean, default=False)
    external_id: Mapped[str | None] = mapped_column(String(64))
    light_reminder: Mapped[bool] = mapped_column(Boolean, default=False)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("todo_templates.id"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column()
    archived_at: Mapped[datetime | None] = mapped_column()
    assignee: Mapped["Member | None"] = relationship(foreign_keys=[assignee_id])
    light_areas: Mapped[list[LightArea]] = relationship(secondary=todo_light_areas)


class ReminderLog(Base):
    """A reminder already sent, so each stage goes out once per person."""
    __tablename__ = "reminder_log"
    __table_args__ = (UniqueConstraint("todo_id", "member_id", "stage", name="uq_reminder_once"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    todo_id: Mapped[int] = mapped_column(ForeignKey("todos.id", ondelete="CASCADE"))
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    stage: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(200))
    sent_at: Mapped[datetime] = mapped_column(default=utcnow)
