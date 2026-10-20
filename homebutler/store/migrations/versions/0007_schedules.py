"""Schedules and the automatic AC shutdown's run records.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

from homebutler.store.models import UTCDateTime

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "auto_off_cycles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("device_id", sa.Integer, sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("started_at", UTCDateTime(), nullable=False),
        sa.Column("hours", sa.Integer, nullable=False),
        sa.Column("closed_at", UTCDateTime()),
    )
    op.create_index("uq_auto_off_open", "auto_off_cycles", ["device_id"], unique=True,
                    sqlite_where=sa.text("closed_at IS NULL"), postgresql_where=sa.text("closed_at IS NULL"))
    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id"), nullable=False),
        sa.Column("target_kind", sa.String(16), nullable=False),
        sa.Column("target_name", sa.String(160), nullable=False),
        sa.Column("params", sa.JSON, nullable=False),
        sa.Column("due_at", UTCDateTime(), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("cycle_id", sa.Integer, sa.ForeignKey("auto_off_cycles.id")),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("attempt_id", sa.String(32), unique=True),
        sa.Column("result", sa.Text, nullable=False),
        sa.Column("created_by_id", sa.Integer, sa.ForeignKey("members.id")),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("finished_at", UTCDateTime()),
        sa.Column("dismissed_at", UTCDateTime()),
    )
    op.create_index("ix_schedules_due", "schedules", ["status", "due_at"])
    if op.get_bind().dialect.name == "postgresql":
        for table in ("auto_off_cycles", "schedules"):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade():
    op.drop_index("ix_schedules_due", "schedules")
    op.drop_table("schedules")
    op.drop_index("uq_auto_off_open", "auto_off_cycles")
    op.drop_table("auto_off_cycles")
