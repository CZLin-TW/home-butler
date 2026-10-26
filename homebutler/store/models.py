"""The tables. Structure changes go through an Alembic version (store/migrations), never create_all.

Conventions: integer ids; English column names; times are timezone-aware UTC; something archived or
removed gets a timestamp or a status instead of moving to another table; every household-owned row
carries household_id.
"""
from __future__ import annotations

from datetime import date, datetime, time as time_of_day, timezone

from sqlalchemy import (JSON, Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, String, Table, Text, Time, text,
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
    butler_style: Mapped[str] = mapped_column(Text, default="")     # set by the member through the butler, not the spreadsheet
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
    __table_args__ = (UniqueConstraint("device_token_hash", name="uq_pairing_codes_device_token_hash"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column()
    approved_at: Mapped[datetime | None] = mapped_column()
    used_at: Mapped[datetime | None] = mapped_column()
    # the browser that asked for the code keeps this secret; only it can collect the sign-in
    device_token_hash: Mapped[str | None] = mapped_column(String(64))
    picture_url: Mapped[str] = mapped_column(String(500), default="")


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


# ── Food ──────────────────────────────────────────────────────────────────────

class FoodItem(Base):
    """Something in the fridge or the cupboard. Used up = status "consumed" (kept, hidden)."""
    __tablename__ = "food_items"
    __table_args__ = (Index("ix_food_active", "household_id", "status"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"))
    name: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit: Mapped[str] = mapped_column(String(16), default="個")
    expires_on: Mapped[date | None] = mapped_column(Date)
    added_on: Mapped[date] = mapped_column(Date)
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"))
    status: Mapped[str] = mapped_column(String(10), default="active")     # active | consumed
    consumed_at: Mapped[datetime | None] = mapped_column()
    added_by: Mapped["Member | None"] = relationship()


# ── Devices (Home Assistant is the source; these mirror it) ───────────────────

class Device(Base):
    """Something the family sees as one device: an AC, an IR fan, a sensor, a presence zone. Made from
    what HA shares; `kind` + `name` identify it, so a device keeps its history across HA renames of ids."""
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("kind", "name", name="uq_devices_kind_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"))
    kind: Mapped[str] = mapped_column(String(16))          # ac | ir | sensor | presence
    name: Mapped[str] = mapped_column(String(160))
    area: Mapped[str] = mapped_column(String(100), default="")
    ha_floor_id: Mapped[str] = mapped_column(String(80), default="")
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    removed_at: Mapped[datetime | None] = mapped_column()
    entities: Mapped[list["DeviceEntity"]] = relationship(back_populates="device")


class DeviceEntity(Base):
    """One HA entity behind a device, by HA's stable registry id."""
    __tablename__ = "device_entities"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))
    ha_id: Mapped[str] = mapped_column(String(32), unique=True)
    entity_id: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(24))          # climate | button | temperature | humidity | co2 | light_level | occupancy | illuminance
    label: Mapped[str] = mapped_column(String(60), default="")   # an IR button's name
    removed_at: Mapped[datetime | None] = mapped_column()
    device: Mapped[Device] = relationship(back_populates="entities")


class Room(Base):
    """Where the Dashboard lists a room. A room is the location text the devices carry (the 智能居家
    setting, else the HA area); only the order lives here. Rooms without a row come after, by name."""
    __tablename__ = "rooms"
    __table_args__ = (UniqueConstraint("household_id", "name", name="uq_rooms_household_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"))
    name: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer)


class DeviceSetting(Base):
    """A row of the hand-edited 智能居家 tab, matched to a device by name. Credentials are never copied."""
    __tablename__ = "device_settings"
    name: Mapped[str] = mapped_column(String(160), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))          # ac | ir | sensor | dehumidifier | other
    location: Mapped[str] = mapped_column(String(100), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    external_id: Mapped[str] = mapped_column(String(80), default="")
    brand: Mapped[str] = mapped_column(String(32), default="")
    temperature_offset: Mapped[float] = mapped_column(default=0.0)
    humidity_offset: Mapped[float] = mapped_column(default=0.0)
    auto_off_hours_text: Mapped[str] = mapped_column(String(40), default="")
    auto_off_hold_text: Mapped[str] = mapped_column(String(40), default="")
    humidity_rule_text: Mapped[str] = mapped_column(Text, default="")
    removed_at: Mapped[datetime | None] = mapped_column()


class SensorReading(Base):
    __tablename__ = "sensor_readings"
    __table_args__ = (Index("ix_sensor_readings", "device_id", "t"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))
    t: Mapped[datetime] = mapped_column()
    temperature: Mapped[float | None] = mapped_column()
    humidity: Mapped[float | None] = mapped_column()
    co2: Mapped[float | None] = mapped_column()
    light_level: Mapped[int | None] = mapped_column(Integer)


class AcReading(Base):
    __tablename__ = "ac_history"
    __table_args__ = (Index("ix_ac_history", "device_id", "t"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))
    t: Mapped[datetime] = mapped_column()
    power: Mapped[str] = mapped_column(String(4))          # on | off
    mode: Mapped[str] = mapped_column(String(16), default="")
    temperature: Mapped[float | None] = mapped_column()
    fan: Mapped[str] = mapped_column(String(16), default="")


# ── Computers (telemetry senders) ─────────────────────────────────────────────

class Computer(Base):
    """A machine that reports its health every minute: through Home Assistant, known by the source name HA
    gives it (2026-10 on), or directly with a key of its own (the Windows PC until it retires). Never by
    the address it reports."""
    __tablename__ = "computers"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[int | None] = mapped_column(ForeignKey("api_tokens.id"), unique=True)
    source: Mapped[str | None] = mapped_column(String(80), unique=True)
    hostname: Mapped[str] = mapped_column(String(80), default="")
    ip: Mapped[str] = mapped_column(String(45), default="")
    cpu_model: Mapped[str] = mapped_column(String(128), default="")
    gpu_model: Mapped[str] = mapped_column(String(128), default="")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column()
    current: Mapped[dict] = mapped_column(JSON, default=dict)     # the latest report, for the card


class ComputerMetric(Base):
    __tablename__ = "computer_metrics"
    __table_args__ = (Index("ix_computer_metrics", "computer_id", "t"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    computer_id: Mapped[int] = mapped_column(ForeignKey("computers.id"))
    t: Mapped[datetime] = mapped_column()
    cpu_pct: Mapped[float | None] = mapped_column()
    ram_pct: Mapped[float | None] = mapped_column()
    gpu_pct: Mapped[float | None] = mapped_column()
    cpu_temp: Mapped[float | None] = mapped_column()
    gpu_temp: Mapped[float | None] = mapped_column()
    soc_temp: Mapped[float | None] = mapped_column()
    mem_pressure_pct: Mapped[int | None] = mapped_column(Integer)
    mem_pressure_level: Mapped[str | None] = mapped_column(String(10))
    power_w: Mapped[float | None] = mapped_column()


# ── Schedules ─────────────────────────────────────────────────────────────────

class AutoOffCycle(Base):
    """One run of an AC, from the moment it was seen on until it was seen off, for the automatic
    shutdown. At most one open cycle per AC (the database enforces it), so a restart or a repeated "on"
    can never arm a second shutdown for the same run, and a shutdown the family deleted stays deleted
    until the AC has been off."""
    __tablename__ = "auto_off_cycles"
    __table_args__ = (Index("uq_auto_off_open", "device_id", unique=True, sqlite_where=text("closed_at IS NULL"),
                            postgresql_where=text("closed_at IS NULL")),)
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))
    started_at: Mapped[datetime] = mapped_column()
    hours: Mapped[int] = mapped_column(Integer)                  # as set when the run started
    closed_at: Mapped[datetime | None] = mapped_column()


class Schedule(Base):
    """A device action at a time. `source` is "user" or "auto_off" (made for an AutoOffCycle).

    status: pending → claimed (marked before sending, so a restart never sends twice) → done | failed |
    unknown; or expired (more than 2 hours late) or cancelled. Unknown and failed stay visible until a
    person dismisses them; they are never sent again."""
    __tablename__ = "schedules"
    __table_args__ = (Index("ix_schedules_due", "status", "due_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"))
    target_kind: Mapped[str] = mapped_column(String(16))         # ac | ir | dehumidifier (a module)
    target_name: Mapped[str] = mapped_column(String(160))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    due_at: Mapped[datetime] = mapped_column()
    source: Mapped[str] = mapped_column(String(10), default="user")
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("auto_off_cycles.id"))
    status: Mapped[str] = mapped_column(String(10), default="pending")
    attempt_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    result: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"))
    created_by: Mapped[str] = mapped_column(String(80), default="")     # who, as shown (a member, a remote, 系統)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column()
    dismissed_at: Mapped[datetime | None] = mapped_column()
    cycle: Mapped[AutoOffCycle | None] = relationship()


# ── The butler ────────────────────────────────────────────────────────────────

class ConversationMessage(Base):
    """What a member and the butler said on LINE; the last few turns are the butler's context."""
    __tablename__ = "conversation_messages"
    __table_args__ = (Index("ix_conversation_member", "member_id", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    role: Mapped[str] = mapped_column(String(10))           # user | assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
