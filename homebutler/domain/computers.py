"""Machines reporting their health every minute.

Two ways in. The Mac mini is read by Home Assistant on the home network and arrives with HA's snapshots,
known by the source name HA gives it (2026-10: the one way between home and the cloud is HA's link).
The Windows PC still sends with a key of its own until it retires; there a machine is the key it reports
with, so no sender can report as another. The first report through HA for an address that a keyed card
already shows takes that card over, so its history goes on.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from homebutler.store.models import ApiToken, Computer, ComputerMetric

ONLINE_SECONDS = 300          # the card greys out after this; alerts wait longer (see the alert job)


class FAHStatus(BaseModel):
    paused: Optional[bool] = None
    finish: Optional[bool] = None
    units_count: Optional[int] = None
    progress_pct: Optional[float] = None


class SMCTemperature(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tcmb_c: Optional[float] = Field(default=None, strict=True, gt=0, le=150, allow_inf_nan=False)
    tcmz_c: Optional[float] = Field(default=None, strict=True, gt=0, le=150, allow_inf_nan=False)


class MemoryPressure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: Optional[Literal["normal", "warning", "critical"]] = None
    pct: Optional[int] = Field(default=None, strict=True, ge=0, le=100)


class Heartbeat(BaseModel):
    """The report, unchanged from the old service so the senders keep their format."""
    ip: str = Field(max_length=45)
    hostname: Optional[str] = Field(default="", max_length=80)
    cpu_model: Optional[str] = Field(default="", max_length=128)
    gpu_model: Optional[str] = Field(default="", max_length=128)
    cpu_pct: float = Field(ge=0, le=100, allow_inf_nan=False)
    ram_pct: float = Field(ge=0, le=100, allow_inf_nan=False)
    gpu_pct: Optional[float] = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    gpu_temp_c: Optional[float] = Field(default=None, allow_inf_nan=False)
    cpu_temp_c: Optional[float] = Field(default=None, allow_inf_nan=False)
    fah: Optional[FAHStatus] = None
    smc_temperature: Optional[SMCTemperature] = None
    system_power_w: Optional[float] = Field(default=None, strict=True, gt=0, le=500, allow_inf_nan=False)
    memory_pressure: Optional[MemoryPressure] = None


class WatchdogStatus(BaseModel):
    """One of the small unattended services on the reporting machine (~/Services/watchdogs), as its
    status command describes it. Shown on the card so they are not forgotten."""
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=40)
    healthy: bool
    last_line: Optional[str] = Field(default=None, max_length=200)
    last_action: Optional[str] = Field(default=None, max_length=200)
    paused: bool = False


class Report(BaseModel):
    """What HA passes on for one machine: the same heartbeat as before, plus the watchdogs."""
    model_config = ConfigDict(extra="forbid")
    source: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")
    sampled_at: int = Field(ge=0)
    heartbeat: Heartbeat
    watchdogs: Optional[list[WatchdogStatus]] = Field(default=None, max_length=20)


def record(session: Session, token: ApiToken, beat: Heartbeat, now: datetime) -> Computer:
    computer = session.scalar(select(Computer).where(Computer.token_id == token.id))
    if computer is None:
        computer = Computer(token_id=token.id)
        session.add(computer)
    return _store(session, computer, beat, now, beat.hostname or token.label)


def record_report(session: Session, report: Report, now: datetime) -> Computer | None:
    """A report HA passed on. Recorded once per sample (HA's snapshots come more often than samples)."""
    computer = session.scalar(select(Computer).where(Computer.source == report.source))
    if computer is None:
        computer = session.scalar(select(Computer).where(Computer.source.is_(None), Computer.ip == report.heartbeat.ip))
        if computer is None:
            computer = Computer()
            session.add(computer)
        computer.source = report.source
    elif (computer.current or {}).get("sampled_at") == report.sampled_at:
        return None
    computer = _store(session, computer, report.heartbeat, now, report.heartbeat.hostname or report.source)
    computer.current = {**computer.current, "sampled_at": report.sampled_at,
                        "watchdogs": [w.model_dump() for w in report.watchdogs] if report.watchdogs is not None else None}
    return computer


def _store(session: Session, computer: Computer, beat: Heartbeat, now: datetime, name: str) -> Computer:
    computer.hostname, computer.ip = name, beat.ip
    computer.cpu_model, computer.gpu_model = beat.cpu_model or "", beat.gpu_model or ""
    computer.last_heartbeat_at, computer.current = now, beat.model_dump()
    session.flush()
    pressure = beat.memory_pressure
    session.add(ComputerMetric(computer_id=computer.id, t=now, cpu_pct=beat.cpu_pct, ram_pct=beat.ram_pct, gpu_pct=beat.gpu_pct,
                               cpu_temp=beat.cpu_temp_c, gpu_temp=beat.gpu_temp_c,
                               soc_temp=beat.smc_temperature.tcmb_c if beat.smc_temperature else None,
                               mem_pressure_pct=pressure.pct if pressure else None,
                               mem_pressure_level=pressure.level if pressure else None, power_w=beat.system_power_w))
    return computer


def online(computer: Computer, now: datetime) -> bool:
    return computer.last_heartbeat_at is not None and now - computer.last_heartbeat_at <= timedelta(seconds=ONLINE_SECONDS)


def history(session: Session, computer_id: int, since: datetime) -> list[ComputerMetric]:
    return list(session.scalars(select(ComputerMetric).where(ComputerMetric.computer_id == computer_id, ComputerMetric.t >= since)
                                .order_by(ComputerMetric.t)))
