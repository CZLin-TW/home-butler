"""The dehumidifier module's tables (homebutler/dehumidifier). Removing the module: a later version drops them.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

from homebutler.store.models import UTCDateTime

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "dehumidifiers",
        sa.Column("name", sa.String(160), primary_key=True),
        sa.Column("auto_mode", sa.Boolean, nullable=False),
        sa.Column("sensor_name", sa.String(160), nullable=False),
        sa.Column("duration_min", sa.Integer, nullable=False),
        sa.Column("threshold", sa.Integer, nullable=False),
        sa.Column("threshold_source", sa.String(10), nullable=False),
        sa.Column("phase", sa.String(24), nullable=False),
        sa.Column("countdown_min", sa.Integer),
        sa.Column("above_since", UTCDateTime()),
        sa.Column("below_since", UTCDateTime()),
        sa.Column("sensor_missing_ticks", sa.Integer, nullable=False),
        sa.Column("expected", sa.JSON),
        sa.Column("applied_threshold", sa.Integer),
        sa.Column("last_event", sa.String(40), nullable=False),
        sa.Column("last_event_at", UTCDateTime()),
        sa.Column("power", sa.Boolean),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("target", sa.String(8), nullable=False),
        sa.Column("status_at", UTCDateTime()),
        sa.Column("status_error", sa.Text, nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
    )
    op.create_table(
        "dehumidifier_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("t", UTCDateTime(), nullable=False),
        sa.Column("power", sa.String(4), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
    )
    op.create_index("ix_dehumidifier_history", "dehumidifier_history", ["name", "t"])
    if op.get_bind().dialect.name == "postgresql":
        for table in ("dehumidifiers", "dehumidifier_history"):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade():
    op.drop_index("ix_dehumidifier_history", "dehumidifier_history")
    op.drop_table("dehumidifier_history")
    op.drop_table("dehumidifiers")
