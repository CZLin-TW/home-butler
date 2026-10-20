"""Computers that report their health, and their metrics.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

from homebutler.store.models import UTCDateTime

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "computers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token_id", sa.Integer, sa.ForeignKey("api_tokens.id"), nullable=False, unique=True),
        sa.Column("hostname", sa.String(80), nullable=False),
        sa.Column("ip", sa.String(45), nullable=False),
        sa.Column("cpu_model", sa.String(128), nullable=False),
        sa.Column("gpu_model", sa.String(128), nullable=False),
        sa.Column("last_heartbeat_at", UTCDateTime()),
        sa.Column("current", sa.JSON, nullable=False),
    )
    op.create_table(
        "computer_metrics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("computer_id", sa.Integer, sa.ForeignKey("computers.id"), nullable=False),
        sa.Column("t", UTCDateTime(), nullable=False),
        sa.Column("cpu_pct", sa.Float), sa.Column("ram_pct", sa.Float), sa.Column("gpu_pct", sa.Float),
        sa.Column("cpu_temp", sa.Float), sa.Column("gpu_temp", sa.Float), sa.Column("soc_temp", sa.Float),
        sa.Column("mem_pressure_pct", sa.Integer), sa.Column("mem_pressure_level", sa.String(10)),
        sa.Column("power_w", sa.Float),
    )
    op.create_index("ix_computer_metrics", "computer_metrics", ["computer_id", "t"])
    if op.get_bind().dialect.name == "postgresql":
        for table in ("computers", "computer_metrics"):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade():
    op.drop_index("ix_computer_metrics", "computer_metrics")
    op.drop_table("computer_metrics")
    op.drop_table("computers")
