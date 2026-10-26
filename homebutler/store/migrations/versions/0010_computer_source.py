"""Computers reported through Home Assistant: known by a source name instead of their own key.

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("computers") as table:
        table.add_column(sa.Column("source", sa.String(80), nullable=True))
        table.alter_column("token_id", existing_type=sa.Integer, nullable=True)
        table.create_unique_constraint("uq_computers_source", ["source"])


def downgrade():
    with op.batch_alter_table("computers") as table:
        table.drop_constraint("uq_computers_source", type_="unique")
        table.alter_column("token_id", existing_type=sa.Integer, nullable=False)
        table.drop_column("source")
