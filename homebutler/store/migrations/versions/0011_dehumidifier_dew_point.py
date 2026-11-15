"""Dehumidifier auto mode by dew point (day and night) with a humidity ceiling; the time-of-day curve is gone.

Units that followed a sensor's curve (threshold_source custom) move to the dew-point mode with its defaults.
The curve lived in the 智能居家 tab (「濕度控制規則」), copied into device_settings.humidity_rule_text; nothing
reads it any more.

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("dehumidifiers") as table:
        table.add_column(sa.Column("dew_day", sa.Integer, nullable=False, server_default="18"))
        table.add_column(sa.Column("dew_night", sa.Integer, nullable=True, server_default="19"))
        table.add_column(sa.Column("night_start", sa.Integer, nullable=False, server_default="23"))
        table.add_column(sa.Column("night_end", sa.Integer, nullable=False, server_default="7"))
        table.add_column(sa.Column("rh_cap", sa.Integer, nullable=False, server_default="65"))
    op.execute("UPDATE dehumidifiers SET threshold_source = 'dewpoint' WHERE threshold_source = 'custom'")
    with op.batch_alter_table("device_settings") as table:
        table.drop_column("humidity_rule_text")


def downgrade():
    with op.batch_alter_table("device_settings") as table:
        table.add_column(sa.Column("humidity_rule_text", sa.Text, nullable=False, server_default=""))
    op.execute("UPDATE dehumidifiers SET threshold_source = 'fixed' WHERE threshold_source = 'dewpoint'")
    with op.batch_alter_table("dehumidifiers") as table:
        for name in ("rh_cap", "night_end", "night_start", "dew_night", "dew_day"):
            table.drop_column(name)
