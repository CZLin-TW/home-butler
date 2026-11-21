"""Dehumidifier humidity ceiling by day and by night.

The single ceiling becomes the night's (kept as it was); the day's starts at 60 % (agreed 2026-11-21: after the
cold front a bedroom sat at 62–64 % all day and never reached 65+2).

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("dehumidifiers") as table:
        table.alter_column("rh_cap", new_column_name="rh_cap_night", existing_type=sa.Integer, existing_nullable=False,
                           existing_server_default="65")
        table.add_column(sa.Column("rh_cap_day", sa.Integer, nullable=False, server_default="60"))


def downgrade():
    with op.batch_alter_table("dehumidifiers") as table:
        table.drop_column("rh_cap_day")
        table.alter_column("rh_cap_night", new_column_name="rh_cap", existing_type=sa.Integer, existing_nullable=False,
                           existing_server_default="65")
