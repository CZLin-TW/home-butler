"""Food.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

from homebutler.store.models import UTCDateTime

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "food_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("expires_on", sa.Date),
        sa.Column("added_on", sa.Date, nullable=False),
        sa.Column("added_by_id", sa.Integer, sa.ForeignKey("members.id")),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("consumed_at", UTCDateTime()),
    )
    op.create_index("ix_food_active", "food_items", ["household_id", "status"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute('ALTER TABLE "food_items" ENABLE ROW LEVEL SECURITY')


def downgrade():
    op.drop_index("ix_food_active", "food_items")
    op.drop_table("food_items")
