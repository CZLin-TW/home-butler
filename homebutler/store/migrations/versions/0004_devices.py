"""Devices mirrored from Home Assistant, their entities, the hand-edited settings, sensor and AC history.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

from homebutler.store.models import UTCDateTime

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TABLES = ("devices", "device_entities", "device_settings", "sensor_readings", "ac_history")


def upgrade():
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("area", sa.String(100), nullable=False),
        sa.Column("ha_floor_id", sa.String(80), nullable=False),
        sa.Column("first_seen_at", UTCDateTime(), nullable=False),
        sa.Column("last_seen_at", UTCDateTime(), nullable=False),
        sa.Column("removed_at", UTCDateTime()),
        sa.UniqueConstraint("kind", "name", name="uq_devices_kind_name"),
    )
    op.create_table(
        "device_entities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("device_id", sa.Integer, sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("ha_id", sa.String(32), nullable=False, unique=True),
        sa.Column("entity_id", sa.String(160), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("label", sa.String(60), nullable=False),
        sa.Column("removed_at", UTCDateTime()),
    )
    op.create_table(
        "device_settings",
        sa.Column("name", sa.String(160), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("location", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("external_id", sa.String(80), nullable=False),
        sa.Column("brand", sa.String(32), nullable=False),
        sa.Column("temperature_offset", sa.Float, nullable=False),
        sa.Column("humidity_offset", sa.Float, nullable=False),
        sa.Column("auto_off_hours_text", sa.String(40), nullable=False),
        sa.Column("auto_off_hold_text", sa.String(40), nullable=False),
        sa.Column("humidity_rule_text", sa.Text, nullable=False),
        sa.Column("removed_at", UTCDateTime()),
    )
    op.create_table(
        "sensor_readings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("device_id", sa.Integer, sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("t", UTCDateTime(), nullable=False),
        sa.Column("temperature", sa.Float),
        sa.Column("humidity", sa.Float),
        sa.Column("co2", sa.Float),
        sa.Column("light_level", sa.Integer),
    )
    op.create_index("ix_sensor_readings", "sensor_readings", ["device_id", "t"])
    op.create_table(
        "ac_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("device_id", sa.Integer, sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("t", UTCDateTime(), nullable=False),
        sa.Column("power", sa.String(4), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("temperature", sa.Float),
        sa.Column("fan", sa.String(16), nullable=False),
    )
    op.create_index("ix_ac_history", "ac_history", ["device_id", "t"])
    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade():
    op.drop_index("ix_ac_history", "ac_history")
    op.drop_index("ix_sensor_readings", "sensor_readings")
    for table in reversed(TABLES):
        op.drop_table(table)
