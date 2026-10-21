"""The order the Dashboard lists rooms in.

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rooms",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.UniqueConstraint("household_id", "name", name="uq_rooms_household_name"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute('ALTER TABLE "rooms" ENABLE ROW LEVEL SECURITY')


def downgrade():
    op.drop_table("rooms")
