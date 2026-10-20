"""The tables. Structure changes go through an Alembic version (store/migrations), never create_all.

Conventions: integer ids; English column names; times are timezone-aware UTC; something archived or
removed gets a timestamp or a status instead of moving to another table; every household-owned row
carries household_id.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, TypeDecorator, UniqueConstraint
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
