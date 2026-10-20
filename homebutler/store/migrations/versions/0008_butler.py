"""The butler's conversation turns, and the pairing codes' device secret.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

from homebutler.store.models import UTCDateTime

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("pairing_codes") as table:
        table.add_column(sa.Column("device_token_hash", sa.String(64)))
        table.add_column(sa.Column("picture_url", sa.String(500), nullable=False, server_default=""))
        table.create_unique_constraint("uq_pairing_codes_device_token_hash", ["device_token_hash"])
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("member_id", sa.Integer, sa.ForeignKey("members.id"), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
    )
    op.create_index("ix_conversation_member", "conversation_messages", ["member_id", "created_at"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute('ALTER TABLE "conversation_messages" ENABLE ROW LEVEL SECURITY')


def downgrade():
    op.drop_index("ix_conversation_member", "conversation_messages")
    op.drop_table("conversation_messages")
    with op.batch_alter_table("pairing_codes") as table:
        table.drop_constraint("uq_pairing_codes_device_token_hash", type_="unique")
        table.drop_column("picture_url")
        table.drop_column("device_token_hash")
