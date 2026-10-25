"""The messages HA and this service exchange. Every field is checked; anything unexpected is refused and
the connection dropped (HA reconnects), so a malformed frame can never become device state."""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROTOCOLS = (1, 2)
MAX_BYTES = 131072
# What this service offers; HA turns on what it supports in its hello.
CAPABILITIES = ["observations", "climate_control", "ir_control", "environment", "hue_control",
                "theater_relay", "automation_log"]


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    entity_id: str = Field(pattern=r"^(binary_sensor|sensor)\.[a-z0-9_]+$", max_length=160)
    name: str = Field(min_length=1, max_length=160)
    area: str = Field(default="", max_length=100)
    floor_id: str = Field(default="", max_length=80)
    kind: Literal["occupancy", "illuminance"]
    value: bool | float | None
    available: bool = Field(strict=True)
    source_updated_at: float | None = Field(default=None, ge=0, le=253402214400, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def check_value(cls, data):
        if not isinstance(data, dict):
            return data
        value, kind = data.get("value"), data.get("kind")
        entity_id = data.get("entity_id", "")
        if not isinstance(entity_id, str):
            raise ValueError("Invalid entity ID")
        if kind == "occupancy":
            if not entity_id.startswith("binary_sensor.") or (value is not None and type(value) is not bool):
                raise ValueError("Occupancy requires a boolean binary sensor")
        elif kind == "illuminance":
            if (not entity_id.startswith("sensor.") or
                    (value is not None and (type(value) not in (int, float) or not 0 <= value <= 1_000_000))):
                raise ValueError("Illuminance requires finite nonnegative lux")
        if data.get("available") is True and value is None:
            raise ValueError("Available observations require a value")
        if data.get("available") is False and value is not None:
            raise ValueError("Unavailable observations must not carry a value")
        return data


class ClimateState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    entity_id: str = Field(pattern=r"^climate\.[a-z0-9_]+$", max_length=160)
    name: str = Field(min_length=1, max_length=160)
    area: str = Field(default="", max_length=100)
    floor_id: str = Field(default="", max_length=80)
    available: bool = Field(strict=True)
    hvac_mode: Literal["off", "cool", "heat", "dry", "fan_only", "auto", "heat_cool"] | None = None
    temperature: float | None = Field(default=None, ge=5, le=40, allow_inf_nan=False)
    fan_mode: str | None = Field(default=None, max_length=40)
    source_updated_at: float | None = Field(default=None, ge=0, le=253402214400, allow_inf_nan=False)


class ClimateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["climate_result"]
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    status: Literal["success", "failed", "unknown"]
    state: ClimateState | None = None


class IRButtonState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    entity_id: str = Field(pattern=r"^button\.[a-z0-9_]+$", max_length=160)
    name: str = Field(min_length=1, max_length=160)
    area: str = Field(default="", max_length=100)
    floor_id: str = Field(default="", max_length=80)
    button: str = Field(min_length=1, max_length=60)
    available: bool = Field(strict=True)
    # Where the button sits in the remote's own list in HA (0 first); integrations before 1.10 leave it out.
    position: Optional[int] = Field(default=None, ge=0, le=119, strict=True)


class IRResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ir_result"]
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    status: Literal["success", "failed", "unknown"]


class TheaterResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["theater_result"]
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    status: Literal["success", "failed", "unknown"]
    result: dict | None = None
    message: str | None = Field(default=None, max_length=400)


class EnvironmentReading(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    entity_id: str = Field(pattern=r"^sensor\.[a-z0-9_]+$", max_length=160)
    name: str = Field(min_length=1, max_length=160)
    area: str = Field(default="", max_length=100)
    floor_id: str = Field(default="", max_length=80)
    kind: Literal["temperature", "humidity", "co2", "light_level"]
    value: float | None = Field(allow_inf_nan=False)
    available: bool = Field(strict=True)
    source_updated_at: float | None = Field(default=None, ge=0, le=253402214400, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def check_value(cls, data):
        if not isinstance(data, dict):
            return data
        bounds = {"temperature": (-100, 150), "humidity": (0, 100),
                  "co2": (0, 100000), "light_level": (1, 20)}
        value = data.get("value")
        low, high = bounds.get(data.get("kind"), (0, 0))
        if value is not None and (type(value) not in (int, float) or not low <= value <= high):
            raise ValueError("Invalid sensor value or units")
        if data.get("kind") == "light_level" and value is not None and value != int(value):
            raise ValueError("Hub light level must be an integer")
        if (data.get("available") is True) != (value is not None):
            raise ValueError("Unknown sensor values must be null")
        return data


class HueResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["hue_result"]
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    status: Literal["success", "failed", "unknown"]
    result: dict = Field(default_factory=dict)


class AutomationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str = Field(pattern=r"^automation\.[a-z0-9_]+$", max_length=255)
    name: str = Field(min_length=1, max_length=160)
    enabled: bool = Field(strict=True)
    last_triggered: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)


class AutomationRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str = Field(pattern=r"^automation\.[a-z0-9_]+$", max_length=255)
    name: str = Field(min_length=1, max_length=160)
    at: float = Field(ge=0, allow_inf_nan=False)
    source: str = Field(default="", max_length=200)


class AutomationReport(BaseModel):
    """What HA's automations are and when they last acted. Display only: nothing here
    can enable, disable, edit or trigger an automation."""
    model_config = ConfigDict(extra="forbid")
    rules: list[AutomationRule] = Field(max_length=200)
    runs: list[AutomationRun] = Field(max_length=50)


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["snapshot"]
    sequence: int = Field(strict=True, ge=1, le=2**53 - 1)
    observations: list[Observation] = Field(max_length=128)
    climates: list[ClimateState] = Field(default_factory=list, max_length=20)
    ir_buttons: list[IRButtonState] = Field(default_factory=list, max_length=120)
    environment: list[EnvironmentReading] = Field(default_factory=list, max_length=80)

    # Absent unless the household opted in on the HA side.
    automations: Optional[AutomationReport] = None

    @model_validator(mode="after")
    def unique_entities(self):
        for key in ("id", "entity_id"):
            values = [getattr(item, key) for item in self.environment]
            if len(values) != len(set(values)):
                raise ValueError("Duplicate environmental sensors")
        if len({(s.name, s.kind) for s in self.environment}) != len(self.environment):
            raise ValueError("Ambiguous sensor mapping")
        for key in ("id", "entity_id"):
            values = [getattr(item, key) for item in self.observations]
            if len(values) != len(set(values)):
                raise ValueError("Duplicate observations")
        for key in ("id", "entity_id", "name"):
            values = [getattr(item, key) for item in self.climates]
            if len(values) != len(set(values)):
                raise ValueError("Duplicate climates")
        for key in ("id", "entity_id"):
            values = [getattr(item, key) for item in self.ir_buttons]
            if len(set(values)) != len(values):
                raise ValueError("Duplicate IR buttons")
        if len({(s.name, s.button) for s in self.ir_buttons}) != len(self.ir_buttons):
            raise ValueError("Ambiguous IR buttons")
        return self
