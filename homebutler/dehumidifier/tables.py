"""This module's own tables. Removing the module: one migration drops both, nothing else refers to them."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from homebutler.store.models import Base, utcnow


class Dehumidifier(Base):
    """One row per dehumidifier (named as in the 智能居家 tab): its auto rule, where the rule stands, and
    the last status read from the cloud. Kept across restarts, so a restart neither forgets a wait under
    way nor the state the machine was left in."""
    __tablename__ = "dehumidifiers"
    name: Mapped[str] = mapped_column(String(160), primary_key=True)
    # the rule (set by people)
    auto_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    sensor_name: Mapped[str] = mapped_column(String(160), default="")
    duration_min: Mapped[int] = mapped_column(Integer, default=30)
    threshold: Mapped[int] = mapped_column(Integer, default=50)
    threshold_source: Mapped[str] = mapped_column(String(10), default="fixed")      # fixed | custom
    # where the rule stands (set by the checks)
    phase: Mapped[str] = mapped_column(String(24), default="disabled")
    countdown_min: Mapped[int | None] = mapped_column(Integer)
    above_since: Mapped[datetime | None] = mapped_column()
    below_since: Mapped[datetime | None] = mapped_column()
    sensor_missing_ticks: Mapped[int] = mapped_column(Integer, default=0)
    expected: Mapped[dict | None] = mapped_column(JSON)          # what the machine should report after our last command
    applied_threshold: Mapped[int | None] = mapped_column(Integer)
    last_event: Mapped[str] = mapped_column(String(40), default="")
    last_event_at: Mapped[datetime | None] = mapped_column()
    # the last status read
    power: Mapped[bool | None] = mapped_column(Boolean)
    mode: Mapped[str] = mapped_column(String(24), default="")
    target: Mapped[str] = mapped_column(String(8), default="")
    status_at: Mapped[datetime | None] = mapped_column()
    status_error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class DehumidifierReading(Base):
    __tablename__ = "dehumidifier_history"
    __table_args__ = (Index("ix_dehumidifier_history", "name", "t"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    t: Mapped[datetime] = mapped_column()
    power: Mapped[str] = mapped_column(String(4))          # on | off
    mode: Mapped[str] = mapped_column(String(24), default="")
